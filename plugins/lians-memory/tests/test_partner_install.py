from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap
import lians_plugin


def _load_bundled_hook():
    name = "lians_partner_install_hook_under_test"
    path = PLUGIN_ROOT / "runtime" / "user_prompt_submit_recall.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _set_subprocess_native_data_base(environ: dict[str, str], base: Path) -> None:
    environ.pop("LIANS_MEMORY_HOME", None)
    if sys.platform == "win32":
        environ["LOCALAPPDATA"] = str(base)
    elif sys.platform == "darwin":
        environ["HOME"] = str(base)
    else:
        environ["XDG_DATA_HOME"] = str(base)


def _fake_wheel(vendor: Path, version: str = "0.5.0+codex.test") -> Path:
    vendor.mkdir(parents=True)
    wheel = vendor / f"lians_sdk-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.4\nName: lians-sdk\nVersion: {version}\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"lians_sdk-{version}.dist-info/METADATA", metadata)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (vendor / "provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact": {
                    "filename": wheel.name,
                    "sha256": digest,
                    "sdk_version": version,
                    "source_commit": "a" * 40,
                    "source_tree_sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    return wheel


def _wheel_artifact(tmp_path: Path) -> bootstrap.WheelArtifact:
    wheel = tmp_path / "lians_sdk-0.5.0+codex.test-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    return bootstrap.WheelArtifact(
        path=wheel,
        version="0.5.0+codex.test",
        sha256=hashlib.sha256(b"wheel").hexdigest(),
        source_commit="a" * 40,
        source_tree_sha256="b" * 64,
    )


def _write_fake_installed_sdk(
    data_home: Path,
    wheel: bootstrap.WheelArtifact,
    *,
    version: str | None = None,
    source: Path | None = None,
    platform: str = "win32",
) -> Path:
    installed_version = wheel.version if version is None else version
    site_packages = (
        data_home / "venv" / "Lib" / "site-packages"
        if platform == "win32"
        else data_home / "venv" / "lib" / "python3.11" / "site-packages"
    )
    distribution = site_packages / f"lians_sdk-{installed_version}.dist-info"
    package = site_packages / "lians"
    distribution.mkdir(parents=True)
    package.mkdir()
    files = {
        "lians/__init__.py": b'__version__ = "test"\n',
        f"{distribution.name}/METADATA": (
            f"Metadata-Version: 2.4\nName: lians-sdk\nVersion: {installed_version}\n"
        ).encode(),
        f"{distribution.name}/direct_url.json": json.dumps(
            {"url": (wheel.path if source is None else source).resolve().as_uri()}
        ).encode(),
    }
    record_rows: list[str] = []
    for relative, content in files.items():
        target = site_packages / Path(relative)
        target.write_bytes(content)
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        record_rows.append(f"{relative},sha256={digest.decode('ascii')},{len(content)}")
    record = distribution / "RECORD"
    record_rows.append(f"{distribution.name}/RECORD,,")
    record.write_text("\n".join(record_rows) + "\n", encoding="utf-8")
    return package / "__init__.py"


def test_bundled_wheel_is_discovered_from_metadata_and_provenance(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    wheel = _fake_wheel(vendor)
    artifact = bootstrap.discover_wheel(vendor)
    assert artifact.path == wheel
    assert artifact.version == "0.5.0+codex.test"
    assert artifact.sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_bundled_wheel_rejects_tampering_and_ambiguity(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    wheel = _fake_wheel(vendor)
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(bootstrap.BootstrapError, match="SHA-256"):
        bootstrap.discover_wheel(vendor)

    vendor = tmp_path / "ambiguous"
    _fake_wheel(vendor)
    (vendor / "lians_sdk-9.9.9-py3-none-any.whl").write_bytes(b"other")
    with pytest.raises(bootstrap.BootstrapError, match="exactly one"):
        bootstrap.discover_wheel(vendor)


def test_native_data_home_is_cross_platform_and_never_codex(tmp_path: Path) -> None:
    windows = bootstrap.native_data_home(
        {"LOCALAPPDATA": str(tmp_path / "Local")}, platform="win32", home=tmp_path
    )
    linux = bootstrap.native_data_home({}, platform="linux", home=tmp_path)
    mac = bootstrap.native_data_home({}, platform="darwin", home=tmp_path)
    assert windows == (tmp_path / "Local" / "Lians" / "CodexMemory").resolve()
    assert linux == (tmp_path / ".local" / "share" / "lians" / "codex-memory").resolve()
    assert mac == (tmp_path / "Library" / "Application Support" / "Lians" / "CodexMemory").resolve()
    assert ".codex" not in str(windows)
    assert ".codex" not in str(linux)
    assert ".codex" not in str(mac)


@pytest.mark.parametrize(
    ("platform", "base_name", "relative_base", "fallback"),
    [
        ("win32", "LOCALAPPDATA", "relative-local", Path("AppData") / "Local"),
        ("linux", "XDG_DATA_HOME", "relative-xdg", Path(".local") / "share"),
    ],
)
def test_native_data_home_ignores_relative_base_and_uses_absolute_home(
    tmp_path: Path,
    platform: str,
    base_name: str,
    relative_base: str,
    fallback: Path,
) -> None:
    suffix = (
        Path("Lians") / "CodexMemory" if platform == "win32" else Path("lians") / "codex-memory"
    )
    result = bootstrap.native_data_home(
        {base_name: relative_base},
        platform=platform,
        home=tmp_path,
    )

    assert result == (tmp_path / fallback / suffix).resolve()
    assert not result.is_relative_to((Path.cwd() / relative_base).resolve())


@pytest.mark.parametrize(
    ("platform", "values"),
    [
        ("win32", {"LOCALAPPDATA": "relative-local"}),
        ("linux", {"XDG_DATA_HOME": "relative-xdg"}),
        ("darwin", {"XDG_DATA_HOME": "ignored"}),
    ],
)
def test_native_data_home_rejects_relative_home_without_an_absolute_native_base(
    platform: str,
    values: dict[str, str],
) -> None:
    with pytest.raises(
        bootstrap.BootstrapError,
        match="native user home must be an absolute path",
    ):
        bootstrap.native_data_home(
            values,
            platform=platform,
            home=Path("relative-home"),
        )


@pytest.mark.parametrize(
    ("platform", "base_name", "suffix"),
    [
        ("win32", "LOCALAPPDATA", Path("Lians") / "CodexMemory"),
        ("linux", "XDG_DATA_HOME", Path("lians") / "codex-memory"),
    ],
)
def test_absolute_native_base_does_not_depend_on_home(
    tmp_path: Path,
    platform: str,
    base_name: str,
    suffix: Path,
) -> None:
    base = tmp_path / "absolute-base"

    assert (
        bootstrap.native_data_home(
            {base_name: str(base)},
            platform=platform,
            home=Path("relative-home"),
        )
        == (base / suffix).resolve()
    )


def test_native_profile_location_does_not_depend_on_plugin_cache(tmp_path: Path) -> None:
    plugin_data = tmp_path / "codex-plugin-data"
    values = {
        "LIANS_MEMORY_HOME": "",
        "PLUGIN_DATA": str(plugin_data),
        "LOCALAPPDATA": str(tmp_path / "Local"),
    }
    expected_fallback = bootstrap.native_data_home(values)
    assert bootstrap.resolve_data_home(values) == expected_fallback.resolve()


def test_custom_memory_home_override_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(bootstrap.BootstrapError, match="overrides are not supported"):
        bootstrap.resolve_data_home({"LIANS_MEMORY_HOME": str(tmp_path / "state")})


def test_native_base_is_validated_before_resolving_reparse_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect = tmp_path / "redirect"
    redirect.mkdir()
    original = bootstrap._is_symlink_or_reparse
    monkeypatch.setattr(
        bootstrap,
        "_is_symlink_or_reparse",
        lambda path: path == redirect or original(path),
    )

    with pytest.raises(bootstrap.BootstrapError, match="must not contain"):
        bootstrap.native_data_home({"LOCALAPPDATA": str(redirect)}, platform="win32", home=tmp_path)


def test_uv_lookup_does_not_select_a_repo_cwd_executable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project_bin = project / "tools"
    trusted_bin = tmp_path / "trusted-bin"
    project_bin.mkdir(parents=True)
    trusted_bin.mkdir()
    (project / "uv.exe").write_bytes(b"malicious")
    (project_bin / "uv.exe").write_bytes(b"also-malicious")
    expected = trusted_bin / "uv.exe"
    expected.write_bytes(b"trusted")
    expected.chmod(0o700)

    discovered = bootstrap.find_uv(
        {"PATH": os.pathsep.join((str(project_bin), str(trusted_bin)))},
        platform="win32",
        cwd=project,
    )

    assert Path(discovered) == expected.resolve()


def test_windows_system_tool_lookup_ignores_project_cwd_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    system = tmp_path / "System32"
    powershell = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    project.mkdir()
    powershell.parent.mkdir(parents=True)
    for name in ("icacls.exe", "whoami.exe"):
        (system / name).write_bytes(b"trusted")
        (project / name).write_bytes(b"malicious")
    powershell.write_bytes(b"trusted")
    (project / "powershell.exe").write_bytes(b"malicious")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(project))
    monkeypatch.setattr(bootstrap, "_windows_system_directory", lambda: system)

    assert Path(bootstrap._trusted_windows_tool("icacls")) == system / "icacls.exe"
    assert Path(bootstrap._trusted_windows_tool("whoami")) == system / "whoami.exe"
    assert Path(bootstrap._trusted_windows_tool("powershell")) == powershell


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink contract")
def test_custom_memory_home_rejects_an_actual_symlink_component(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    redirect = tmp_path / "redirect"
    redirect.symlink_to(target, target_is_directory=True)

    with pytest.raises(bootstrap.BootstrapError, match="must not contain"):
        bootstrap.native_data_home(
            {"XDG_DATA_HOME": str(redirect)}, platform="linux", home=tmp_path
        )


def test_bge_staging_creates_export_parent_before_running_exporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    model = source / "model.onnx"
    tokenizer = source / "tokenizer.json"
    model.write_bytes(b"model")
    tokenizer.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_verify_bge_source", lambda _source: (model, tokenizer))

    observed: dict[str, object] = {}

    def fake_run(command: list[str], *, environ: dict[str, str]) -> None:
        observed["command"] = command
        observed["parent_exists"] = (data_home / "models").is_dir()
        observed["environment_is_mapping"] = isinstance(environ, dict)

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    output = bootstrap.stage_bge(data_home, source=source, download=False)

    assert output == data_home / "models" / "bge-large-en-v1.5-onnx"
    assert observed["parent_exists"] is True
    assert observed["environment_is_mapping"] is True
    assert str(output) in observed["command"]


def test_bge_staging_reuses_a_verified_existing_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    model = source / "model.onnx"
    tokenizer = source / "tokenizer.json"
    model.write_bytes(b"model")
    tokenizer.write_text("{}", encoding="utf-8")
    output = data_home / "models" / "bge-large-en-v1.5-onnx"
    output.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "_verify_bge_source", lambda _source: (model, tokenizer))
    monkeypatch.setattr(bootstrap, "validate_bge_artifact_directory", lambda path: path == output)
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("verified artifact should not be re-exported"),
    )

    assert bootstrap.stage_bge(data_home, source=source, download=False) == output


def test_project_scopes_and_local_databases_are_isolated(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "model"
    artifact.mkdir()
    profile = {
        "schema_version": 1,
        "mode": "local",
        "bge_artifact_dir": str(artifact),
    }
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).parent.mkdir(parents=True)
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n", encoding="ascii"
    )
    first_root = tmp_path / "customer-a" / "repo"
    second_root = tmp_path / "customer-b" / "repo"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    ambient = {"LIANS_URL": "https://should-not-be-used.invalid", "LIANS_API_KEY": "sentinel"}

    first = bootstrap.configure_runtime_environment(
        data_home, profile, ambient, project_root=first_root, require_managed_key=True
    )
    second = bootstrap.configure_runtime_environment(
        data_home, profile, ambient, project_root=second_root, require_managed_key=True
    )
    assert first["LIANS_LOCAL_DB"] != second["LIANS_LOCAL_DB"]
    assert str(data_home / "projects") in first["LIANS_LOCAL_DB"]
    assert first["LIANS_URL"] == ""
    assert first["LIANS_API_KEY"] == ""
    assert first["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert first["LIANS_MCP_PROJECT_ROOT"] == str(first_root.resolve())
    assert first["LIANS_AGENT_ID"] == ""
    assert first["LIANS_NAMESPACE"] == ""
    assert first["MASTER_ENCRYPTION_KEY"] == encoded_key
    assert first["AGENTMEM_ALLOW_UNENCRYPTED"] == "false"
    assert first["LIANS_MCP_LOCAL_SUBJECT_ID"].startswith("codex-project:")
    assert first["LIANS_MCP_LOCAL_SUBJECT_ID"] != second["LIANS_MCP_LOCAL_SUBJECT_ID"]
    assert first["LIANS_MCP_SUBJECT_ID"] == first["LIANS_MCP_LOCAL_SUBJECT_ID"]
    assert second["LIANS_MCP_SUBJECT_ID"] == second["LIANS_MCP_LOCAL_SUBJECT_ID"]
    assert first["LIANS_PLUGIN_RUNTIME_CWD"].startswith(str(data_home / "projects"))
    assert Path(first["LIANS_PLUGIN_RUNTIME_CWD"]).is_dir()
    assert first["LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR"].startswith(str(data_home / "projects"))
    assert first["LIANS_CODEX_HOOK_RECEIPT"].startswith(str(data_home / "projects"))


def test_explicit_empty_local_remote_values_override_legacy_codex_config(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "model"
    artifact.mkdir()
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    data_home.mkdir()
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n", encoding="ascii"
    )
    project = tmp_path / "project"
    project.mkdir()
    child = bootstrap.configure_runtime_environment(
        data_home,
        {"schema_version": 1, "mode": "local", "bge_artifact_dir": str(artifact)},
        {},
        project_root=project,
        require_managed_key=True,
    )
    legacy = tmp_path / "config.toml"
    legacy.write_text(
        "[mcp_servers.lians.env]\n"
        'LIANS_URL = "https://legacy-remote.invalid"\n'
        'LIANS_API_KEY = "legacy-key-must-not-return"\n'
        'LIANS_LOCAL_DB = "legacy.sqlite3"\n',
        encoding="utf-8",
    )
    hook = _load_bundled_hook()
    merged = hook.merged_lians_environment(child, config_path=legacy)
    settings = hook.build_settings({"cwd": str(project)}, child, config_path=legacy)

    assert merged["LIANS_URL"] == ""
    assert merged["LIANS_API_KEY"] == ""
    assert settings.backend == "local"
    assert settings.api_key == ""
    assert settings.local_db == child["LIANS_LOCAL_DB"]


def test_static_hook_launcher_scrubs_hostile_local_provider_and_egress_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "model"
    artifact.mkdir()
    data_home.mkdir()
    encoded_key = base64.b64encode(b"p" * 32).decode("ascii")
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n",
        encoding="ascii",
    )
    python = bootstrap.runtime_python(data_home)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    project = tmp_path / "project"
    project.mkdir()
    profile = {
        "schema_version": 1,
        "mode": "local",
        "bge_artifact_dir": str(artifact),
    }
    monkeypatch.chdir(project)
    monkeypatch.setattr(lians_plugin, "resolve_data_home", lambda: data_home)
    monkeypatch.setattr(lians_plugin, "read_profile", lambda _data_home: profile)
    monkeypatch.setattr(
        lians_plugin, "verify_profile_matches_bundle", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(lians_plugin, "runtime_python", lambda _data_home: python)
    for name, _value in bootstrap._LOCAL_RUNTIME_SECURITY_ENV:
        monkeypatch.setenv(name, "hostile-inherited-value")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"))
    monkeypatch.setenv("AGENTMEM_ALLOW_UNENCRYPTED", "true")
    monkeypatch.setenv("BGE_ONNX_ARTIFACT_DIR", str(tmp_path / "untrusted-model"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "import-canary"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "runtime-canary"))
    monkeypatch.setenv("PYTHONNOUSERSITE", "0")
    monkeypatch.setenv("PYTHONSAFEPATH", "0")
    monkeypatch.setenv("LIANS_MCP_ENABLED_TOOLS", "remember,recall,admin")
    monkeypatch.setenv("LIANS_MCP_SCHEMA_PROFILE", "full")
    monkeypatch.setenv("LIANS_MCP_RECALL_K", "100")
    monkeypatch.setenv("LIANS_MCP_CONTEXT_MAX_TOKENS", "2500")
    monkeypatch.setenv("LIANS_MCP_PREWARM", "foreground")
    monkeypatch.setenv("LIANS_CODEX_HOOK_K", "100")
    monkeypatch.setenv("LIANS_CODEX_HOOK_MAX_TOKENS", "2500")
    monkeypatch.setenv("LIANS_CODEX_HOOK_MIN_SCORE", "0")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS", "86400")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS", "120000")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS", "600000")
    monkeypatch.setenv("BGE_ONNX_INTRA_OP_THREADS", "256")
    monkeypatch.setattr(bootstrap.os, "cpu_count", lambda: 64)
    captured: dict[str, object] = {}

    def fake_run_hook_runtime(command: str, hook_arg: str | None, environ: dict[str, str]) -> int:
        captured["command"] = command
        captured["hook_arg"] = hook_arg
        captured["environment"] = environ
        return 0

    monkeypatch.setattr(lians_plugin, "_run_hook_runtime", fake_run_hook_runtime)

    assert lians_plugin._runtime_command("hook") == 0

    child = captured["environment"]
    assert isinstance(child, dict)
    required_forced = {
        "LIANS_URL": "",
        "LIANS_API_KEY": "",
        "DEPLOYMENT_ENVIRONMENT": "development",
        "KMS_PROVIDER": "env",
        "EMBEDDING_PROVIDER": "bge-onnx",
        "EMBEDDING_DIM": "1024",
        "AIRGAP_MODE": "true",
        "SUPERSESSION_LLM_STAGE": "false",
        "LLM_ADJUDICATION_ASYNC": "false",
        "AUTO_METADATA_ENABLED": "false",
        "AUTO_METADATA_LLM": "false",
        "GRAPH_EXTRACT_LLM": "false",
        "RECALL_CACHE_ENABLED": "false",
        "RUNTIME_CACHE_ENABLED": "false",
    }
    for name, value in required_forced.items():
        assert child[name] == value
    for name, value in bootstrap._LOCAL_RUNTIME_SECURITY_ENV:
        if value is None:
            assert name not in child
        else:
            assert child[name] == value
    assert child["MASTER_ENCRYPTION_KEY"] == encoded_key
    assert child["AGENTMEM_ALLOW_UNENCRYPTED"] == "false"
    assert child["BGE_ONNX_ARTIFACT_DIR"] == str(artifact)
    assert child["PYTHONPATH"] == ""
    assert child["PYTHONHOME"] == ""
    assert child["PYTHONNOUSERSITE"] == "1"
    assert child["PYTHONSAFEPATH"] == "1"
    assert child["LIANS_MCP_ENABLED_TOOLS"] == (
        "remember,recall,list_memories,correct_memory,forget_memory"
    )
    assert child["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert child["LIANS_MCP_RECALL_K"] == "20"
    assert child["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert child["LIANS_MCP_PREWARM"] == "background"
    assert child["LIANS_CODEX_HOOK_K"] == "20"
    assert child["LIANS_CODEX_HOOK_MAX_TOKENS"] == "768"
    assert child["LIANS_CODEX_HOOK_MIN_SCORE"] == "0.45"
    assert child["LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS"] == "1800"
    assert child["LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS"] == "3000"
    assert child["LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS"] == "120000"
    assert child["BGE_ONNX_INTRA_OP_THREADS"] == "8"
    assert captured["command"] == "hook"
    assert captured["hook_arg"] is None


def test_warm_hook_path_does_not_reapply_existing_directory_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "model"
    artifact.mkdir()
    encoded_key = base64.b64encode(b"w" * 32).decode("ascii")
    data_home.mkdir()
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n", encoding="ascii"
    )
    project = tmp_path / "project"
    project.mkdir()
    profile = {
        "schema_version": 1,
        "mode": "local",
        "bge_artifact_dir": str(artifact),
    }
    bootstrap.configure_runtime_environment(
        data_home,
        profile,
        {},
        project_root=project,
        require_managed_key=True,
    )
    monkeypatch.setattr(
        bootstrap,
        "_restrict_private_path",
        lambda *_args, **_kwargs: pytest.fail("warm hook must not rewrite private ACLs"),
    )
    child = bootstrap.configure_runtime_environment(
        data_home,
        profile,
        {},
        project_root=project,
        require_managed_key=True,
        repair_private_paths=False,
    )

    assert Path(child["LIANS_PLUGIN_RUNTIME_CWD"]).is_dir()
    assert Path(child["LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR"]).is_dir()
    assert child["LIANS_CODEX_HOOK_DAEMON"] == "client"


def test_warm_hook_without_session_prewarm_cannot_spawn_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "model"
    artifact.mkdir()
    encoded_key = base64.b64encode(b"n" * 32).decode("ascii")
    data_home.mkdir()
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n", encoding="ascii"
    )
    project = tmp_path / "project"
    project.mkdir()
    child = bootstrap.configure_runtime_environment(
        data_home,
        {
            "schema_version": 1,
            "mode": "local",
            "bge_artifact_dir": str(artifact),
        },
        {},
        project_root=project,
        require_managed_key=True,
        repair_private_paths=False,
    )
    hook = _load_bundled_hook()
    settings = hook.build_settings({"cwd": str(project)}, child)
    daemon = hook._daemon_runtime()
    monkeypatch.setattr(
        daemon,
        "spawn",
        lambda *_args, **_kwargs: pytest.fail("client-only hook must not spawn a daemon"),
    )

    with pytest.raises(RuntimeError, match="not ready"):
        hook.retrieve(settings, "memory query")


def test_runtime_launcher_leaves_malicious_project_dotenv_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "LIANS_URL=https://poison.invalid\nLIANS_API_KEY=poison-key\n",
        encoding="utf-8",
    )
    data_home = tmp_path / "data"
    bootstrap._safe_directory(data_home)
    python = bootstrap.runtime_python(data_home)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    profile = {
        "schema_version": 1,
        "mode": "managed",
        "managed_url": "https://api.lians.dev",
    }
    captured: dict[str, object] = {}
    monkeypatch.chdir(project)
    monkeypatch.setenv("LIANS_API_KEY", "process-key")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "import-canary"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "runtime-canary"))
    monkeypatch.setenv("PYTHONNOUSERSITE", "0")
    monkeypatch.setenv("PYTHONSAFEPATH", "0")
    monkeypatch.setenv("LIANS_MCP_ENABLED_TOOLS", "remember,recall,admin")
    monkeypatch.setenv("LIANS_MCP_SCHEMA_PROFILE", "full")
    monkeypatch.setenv("LIANS_MCP_RECALL_K", "100")
    monkeypatch.setenv("LIANS_MCP_CONTEXT_MAX_TOKENS", "2500")
    monkeypatch.setenv("LIANS_MCP_PREWARM", "foreground")
    monkeypatch.setenv("LIANS_CODEX_HOOK_K", "100")
    monkeypatch.setenv("LIANS_CODEX_HOOK_MAX_TOKENS", "2500")
    monkeypatch.setenv("LIANS_CODEX_HOOK_MIN_SCORE", "0")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS", "86400")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS", "120000")
    monkeypatch.setenv("LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS", "600000")
    monkeypatch.setattr(lians_plugin, "resolve_data_home", lambda: data_home)
    monkeypatch.setattr(lians_plugin, "read_profile", lambda _data_home: profile)
    monkeypatch.setattr(
        lians_plugin, "verify_profile_matches_bundle", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(lians_plugin, "runtime_python", lambda _data_home: python)

    def fake_execve(executable: str, argv: list[str], environ: dict[str, str]) -> None:
        captured["executable"] = executable
        captured["argv"] = argv
        captured["environment"] = environ
        captured["cwd"] = Path.cwd()
        raise SystemExit(0)

    monkeypatch.setattr(lians_plugin.os, "execve", fake_execve)

    with pytest.raises(SystemExit):
        lians_plugin._runtime_command("mcp")

    environment = captured["environment"]
    runtime_cwd = bootstrap.project_data_dir(data_home, project) / "runtime"
    assert captured["cwd"] == runtime_cwd
    assert not (runtime_cwd / ".env").exists()
    assert environment["LIANS_PLUGIN_RUNTIME_CWD"] == str(runtime_cwd)
    assert environment["LIANS_MCP_PROJECT_ROOT"] == str(project.resolve())
    assert environment["LIANS_URL"] == "https://api.lians.dev"
    assert environment["LIANS_API_KEY"] == "process-key"
    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONHOME"] == ""
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONSAFEPATH"] == "1"
    assert environment["LIANS_MCP_ENABLED_TOOLS"] == (
        "remember,recall,list_memories,correct_memory,forget_memory"
    )
    assert environment["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert environment["LIANS_MCP_RECALL_K"] == "20"
    assert environment["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert environment["LIANS_MCP_PREWARM"] == "background"
    assert environment["LIANS_CODEX_HOOK_K"] == "20"
    assert environment["LIANS_CODEX_HOOK_MAX_TOKENS"] == "768"
    assert environment["LIANS_CODEX_HOOK_MIN_SCORE"] == "0.45"
    assert environment["LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS"] == "1800"
    assert environment["LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS"] == "3000"
    assert environment["LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS"] == "120000"
    assert environment["LIANS_MCP_SUBJECT_ID"].startswith("codex-project:")
    assert bootstrap._private_path_permissions_ok(runtime_cwd, is_directory=True)


def test_local_sqlite_does_not_contain_raw_memory_plaintext_via_sdk_subprocess(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    sentinel = "partner-plaintext-sentinel-7f81e092"
    child = dict(os.environ)
    existing_pythonpath = child.get("PYTHONPATH", "")
    child["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(REPO_ROOT / "agentmem" / "sdk" / "python"), existing_pythonpath)
        if part
    )
    child.update(
        {
            "LIANS_TEST_DB": str(database),
            "LIANS_TEST_SENTINEL": sentinel,
            "MASTER_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode("ascii"),
            "AGENTMEM_ALLOW_UNENCRYPTED": "false",
            "EMBEDDING_PROVIDER": "local",
            "RECALL_CACHE_ENABLED": "false",
        }
    )
    code = (
        "import os\n"
        "from datetime import datetime, timezone\n"
        "from lians import LocalLiansClient\n"
        "client = LocalLiansClient(db_path=os.environ['LIANS_TEST_DB'], "
        "namespace='codex-test', embedding_provider='local')\n"
        "client.add(agent_id='codex', content=os.environ['LIANS_TEST_SENTINEL'], "
        "event_time=datetime.now(timezone.utc), subject_id='codex-project:test')\n"
        "result = client.recall(agent_id='codex', "
        "query='find the encrypted partner memory', k=5)\n"
        "assert any(memory.get('content') == os.environ['LIANS_TEST_SENTINEL'] "
        "for memory in result.get('memories', []))\n"
        "client.close()\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=child,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    sqlite_bytes = b"".join(path.read_bytes() for path in tmp_path.glob("memory.sqlite3*"))
    assert sentinel.encode() not in sqlite_bytes
    with sqlite3.connect(database) as connection:
        encrypted = connection.execute(
            "SELECT content_encrypted FROM memories LIMIT 1"
        ).fetchone()
    assert encrypted is not None
    assert isinstance(encrypted[0], bytes)
    assert len(encrypted[0]) >= 28  # 12-byte AES-GCM nonce plus 16-byte tag.
    assert encrypted[0] != sentinel.encode()


def test_local_master_key_is_random_stable_and_not_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected: list[Path] = []
    monkeypatch.setattr(bootstrap, "_restrict_private_file", protected.append)
    data_home = tmp_path / "data"

    first = bootstrap.ensure_local_master_key(data_home)
    first_value = first.read_text(encoding="ascii").strip()
    second = bootstrap.ensure_local_master_key(data_home)

    assert first == second == data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME
    assert len(base64.b64decode(first_value, validate=True)) == 32
    assert second.read_text(encoding="ascii").strip() == first_value
    assert protected == [first, first]


def test_missing_master_key_is_not_regenerated_over_existing_memory_state(
    tmp_path: Path,
) -> None:
    data_home = tmp_path / "data"
    database = data_home / "projects" / "existing-project" / "memory.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"existing encrypted database")

    with pytest.raises(bootstrap.BootstrapError, match="restore the original key"):
        bootstrap.ensure_local_master_key(data_home)

    assert not (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).exists()
    assert database.read_bytes() == b"existing encrypted database"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink contract")
def test_local_master_key_rejects_symlink_before_read_or_acl(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    victim = tmp_path / "victim.key"
    encoded = base64.b64encode(b"v" * 32).decode("ascii") + "\n"
    victim.write_text(encoded, encoding="ascii")
    original_mode = victim.stat().st_mode
    (data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME).symlink_to(victim)

    with pytest.raises(bootstrap.BootstrapError, match="regular non-reparse"):
        bootstrap.ensure_local_master_key(data_home)

    assert victim.read_text(encoding="ascii") == encoded
    assert victim.stat().st_mode == original_mode


def test_local_master_key_rejects_mocked_windows_reparse_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    key = data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME
    key.write_text(base64.b64encode(b"k" * 32).decode("ascii"), encoding="ascii")
    original = bootstrap._is_symlink_or_reparse
    monkeypatch.setattr(
        bootstrap,
        "_is_symlink_or_reparse",
        lambda path: path == key or original(path),
    )

    with pytest.raises(bootstrap.BootstrapError, match="regular non-reparse"):
        bootstrap.ensure_local_master_key(data_home)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink contract")
def test_profile_reader_rejects_symlink_without_following_it(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    victim = tmp_path / "profile-victim.json"
    victim.write_text(json.dumps({"schema_version": 1, "mode": "managed"}), encoding="utf-8")
    (data_home / bootstrap.PROFILE_FILENAME).symlink_to(victim)

    with pytest.raises(bootstrap.BootstrapError, match="regular non-reparse"):
        bootstrap.read_profile(data_home)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://user:secret@example.com",
        "https://api.lians.dev?api_key=secret",
        "https://api.lians.dev/#secret",
        "https://api.lians.dev/ path",
        " https://api.lians.dev",
        "https://api.lians.dev:99999",
        "http://api.lians.dev",
    ],
)
def test_managed_profile_rejects_secret_bearing_or_ambiguous_url_without_persisting(
    tmp_path: Path, unsafe_url: str
) -> None:
    data_home = tmp_path / "data"
    wheel = _wheel_artifact(tmp_path)
    with pytest.raises(bootstrap.BootstrapError, match="explicit HTTPS URL"):
        bootstrap.write_profile(
            data_home,
            mode="managed",
            wheel=wheel,
            lock_sha256="c" * 64,
            managed_url=unsafe_url,
            bge_artifact_dir=None,
        )
    assert not (data_home / bootstrap.PROFILE_FILENAME).exists()


def test_windows_acl_snapshot_rejects_a_preexisting_broad_explicit_ace() -> None:
    user_sid = "S-1-5-21-100-200-300-400"
    base_rules = [
        {
            "sid": user_sid,
            "access": "Allow",
            "rights": "FullControl",
            "inherited": False,
            "inheritance": "ContainerInherit, ObjectInherit",
        },
        {
            "sid": "S-1-5-18",
            "access": "Allow",
            "rights": "FullControl",
            "inherited": False,
            "inheritance": "ContainerInherit, ObjectInherit",
        },
        {
            "sid": "S-1-5-32-544",
            "access": "Allow",
            "rights": "FullControl",
            "inherited": False,
            "inheritance": "ContainerInherit, ObjectInherit",
        },
    ]
    clean = {
        "protected": True,
        "current_sid": user_sid,
        "owner_sid": user_sid,
        "rules": base_rules,
    }
    broad = {
        **clean,
        "rules": [
            *base_rules,
            {
                "sid": "S-1-1-0",
                "access": "Allow",
                "rights": "ReadAndExecute",
                "inherited": False,
                "inheritance": "ContainerInherit, ObjectInherit",
            },
        ],
    }
    assert bootstrap._windows_acl_snapshot_is_private(clean, is_directory=True)
    assert not bootstrap._windows_acl_snapshot_is_private(broad, is_directory=True)


def test_windows_acl_snapshot_drops_incompatible_powershell_module_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = {
        "protected": True,
        "current_sid": "S-1-5-21-100-200-300-400",
        "owner_sid": "S-1-5-21-100-200-300-400",
        "rules": [],
    }
    monkeypatch.setenv("PSMODULEPATH", "C:/PowerShell/7/Modules")
    monkeypatch.setattr(
        bootstrap, "_trusted_windows_tool", lambda _name: "powershell.exe"
    )

    def fake_run(*_args, **kwargs):
        child_env = kwargs["env"]
        assert all(name.upper() != "PSMODULEPATH" for name in child_env)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(snapshot),
            stderr="",
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    assert bootstrap._windows_acl_snapshot(tmp_path) == snapshot


def test_windows_acl_snapshot_rejects_a_foreign_owner() -> None:
    user_sid = "S-1-5-21-100-200-300-400"
    snapshot = {
        "protected": True,
        "current_sid": user_sid,
        "owner_sid": "S-1-5-21-999-888-777-666",
        "rules": [
            {
                "sid": sid,
                "access": "Allow",
                "rights": "FullControl",
                "inherited": False,
                "inheritance": "ContainerInherit, ObjectInherit",
            }
            for sid in (user_sid, "S-1-5-18", "S-1-5-32-544")
        ],
    }
    assert not bootstrap._windows_acl_snapshot_is_private(snapshot, is_directory=True)


def test_windows_acl_restriction_resets_before_granting_known_dacl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private"
    target.mkdir()
    calls: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "_trusted_windows_tool", lambda name: f"{name}.exe")
    monkeypatch.setattr(bootstrap, "_windows_user_sid", lambda: "S-1-5-21-100-200-300-400")
    monkeypatch.setattr(
        bootstrap,
        "_private_path_permissions_ok",
        lambda *_args, **_kwargs: True,
    )

    def fake_run(command: list[str], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    bootstrap._restrict_private_path(target, is_directory=True, platform="win32", verify=True)

    assert calls[0][2:] == ["/reset", "/Q"]
    assert calls[1][2:] == ["/setowner", "*S-1-5-21-100-200-300-400", "/Q"]
    assert "/inheritance:r" in calls[2]
    assert "/grant:r" in calls[2]
    assert "*S-1-1-0" not in " ".join(calls[2])
    assert "*S-1-5-18:(OI)(CI)(F)" in calls[2]


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows ACL smoke")
def test_windows_acl_real_smoke_removes_broad_ace_and_sets_current_owner(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private"
    target.mkdir()
    try:
        icacls = bootstrap._trusted_windows_tool("icacls")
    except bootstrap.BootstrapError:
        pytest.skip("icacls is unavailable")
    broad = subprocess.run(
        [icacls, str(target), "/grant", "*S-1-1-0:(OI)(CI)(RX)", "/Q"],
        check=False,
        capture_output=True,
        text=True,
    )
    if broad.returncode:
        pytest.skip("host did not permit the broad-ACL setup")
    assert not bootstrap._private_path_permissions_ok(target, is_directory=True)

    bootstrap._restrict_private_path(target, is_directory=True)

    snapshot = bootstrap._windows_acl_snapshot(target)
    assert snapshot["owner_sid"] == snapshot["current_sid"]
    assert bootstrap._windows_acl_snapshot_is_private(snapshot, is_directory=True)
    token = target / bootstrap.DAEMON_TOKEN_FILENAME
    token.write_text("a" * 64, encoding="ascii")
    assert bootstrap._existing_daemon_token_is_private(target)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission contract")
def test_posix_private_permission_contract_detects_and_repairs_broad_mode(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o755)
    key = directory / "master.key"
    key.write_text("not-a-real-key", encoding="ascii")
    key.chmod(0o644)
    assert not bootstrap._private_path_permissions_ok(directory, is_directory=True)
    assert not bootstrap._private_path_permissions_ok(key, is_directory=False)
    bootstrap._restrict_private_path(directory, is_directory=True)
    bootstrap._restrict_private_path(key, is_directory=False)
    assert bootstrap._private_path_permissions_ok(directory, is_directory=True)
    assert bootstrap._private_path_permissions_ok(key, is_directory=False)


def test_existing_broad_daemon_token_fails_closed_without_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon_dir = tmp_path / "daemon"
    daemon_dir.mkdir()
    token = daemon_dir / bootstrap.DAEMON_TOKEN_FILENAME
    token.write_text("a" * 64, encoding="ascii")
    original_mode = token.stat().st_mode
    monkeypatch.setattr(
        bootstrap,
        "_private_path_permissions_ok",
        lambda path, **_kwargs: path != token,
    )

    with pytest.raises(bootstrap.BootstrapError, match="stop the Lians daemon"):
        bootstrap._require_existing_daemon_token_private(daemon_dir)

    assert token.read_text(encoding="ascii") == "a" * 64
    assert token.stat().st_mode == original_mode


def test_configure_remembers_unsafe_token_state_before_ancestor_acl_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    project_dir = bootstrap.project_data_dir(data_home, project)
    daemon_dir = project_dir / "daemon"
    daemon_dir.mkdir(parents=True)
    token = daemon_dir / bootstrap.DAEMON_TOKEN_FILENAME
    token.write_text("c" * 64, encoding="ascii")
    monkeypatch.setattr(bootstrap, "_existing_daemon_token_is_private", lambda _path: False)

    with pytest.raises(bootstrap.BootstrapError, match="exposed before directory repair"):
        bootstrap.configure_runtime_environment(
            data_home,
            {
                "schema_version": 1,
                "mode": "managed",
                "managed_url": "https://api.lians.dev",
            },
            {"LIANS_API_KEY": "present"},
            project_root=project,
            require_managed_key=True,
        )

    assert token.read_text(encoding="ascii") == "c" * 64


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink contract")
def test_existing_daemon_token_symlink_fails_closed(tmp_path: Path) -> None:
    daemon_dir = tmp_path / "daemon"
    daemon_dir.mkdir()
    victim = tmp_path / "victim-token"
    victim.write_text("b" * 64, encoding="ascii")
    (daemon_dir / bootstrap.DAEMON_TOKEN_FILENAME).symlink_to(victim)

    with pytest.raises(bootstrap.BootstrapError, match="stop the Lians daemon"):
        bootstrap._require_existing_daemon_token_private(daemon_dir)

    assert victim.read_text(encoding="ascii") == "b" * 64


def test_managed_profile_never_persists_api_key(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    wheel = _wheel_artifact(tmp_path)
    bootstrap.write_profile(
        data_home,
        mode="managed",
        wheel=wheel,
        lock_sha256="c" * 64,
        managed_url="https://api.lians.dev",
        bge_artifact_dir=None,
    )
    secret = "partner-secret-sentinel"
    child = bootstrap.configure_runtime_environment(
        data_home,
        bootstrap.read_profile(data_home),
        {"LIANS_API_KEY": secret, "LIANS_URL": "https://ambient.invalid"},
        project_root=tmp_path,
        require_managed_key=True,
    )
    assert child["LIANS_API_KEY"] == secret
    assert child["LIANS_URL"] == "https://api.lians.dev"
    assert secret not in (data_home / bootstrap.PROFILE_FILENAME).read_text(encoding="utf-8")
    assert json.loads((data_home / bootstrap.PROFILE_FILENAME).read_text())["managed_url"] == (
        "https://api.lians.dev"
    )


def test_managed_profile_without_explicit_https_url_is_rejected(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    (data_home / bootstrap.PROFILE_FILENAME).write_text(
        json.dumps({"schema_version": 1, "mode": "managed", "sdk": {}}),
        encoding="utf-8",
    )
    with pytest.raises(bootstrap.BootstrapError, match="explicit HTTPS URL"):
        bootstrap.read_profile(data_home)


def test_unbootstrapped_hooks_fail_open_and_mcp_is_optional(tmp_path: Path) -> None:
    launcher = SCRIPTS / "lians_plugin.py"
    env = dict(os.environ)
    _set_subprocess_native_data_base(env, tmp_path / "not-configured")
    hook = subprocess.run(
        [sys.executable, str(launcher), "hook"],
        input='{"hook_event_name":"UserPromptSubmit","prompt":"hello"}',
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert hook.returncode == 0
    assert hook.stdout == ""
    assert hook.stderr == ""

    mcp = subprocess.run(
        [sys.executable, str(launcher), "mcp"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert mcp.returncode == 2
    assert "not set up" in mcp.stderr


def test_cli_requires_explicit_managed_url_and_rejects_one_shot_data_dir(
    tmp_path: Path,
) -> None:
    launcher = SCRIPTS / "lians_plugin.py"
    env = dict(os.environ)
    _set_subprocess_native_data_base(env, tmp_path / "data")

    missing_url = subprocess.run(
        [sys.executable, str(launcher), "setup", "--mode", "managed"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert missing_url.returncode == 2
    assert "requires --managed-url" in missing_url.stderr

    custom_data = subprocess.run(
        [sys.executable, str(launcher), "doctor", "--data-dir", str(tmp_path / "other")],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert custom_data.returncode == 2
    assert "unrecognized arguments: --data-dir" in custom_data.stderr


@pytest.mark.parametrize(
    ("platform", "relative", "destination_name"),
    [
        ("win32", Path("Scripts/lians-memory-mcp.exe"), "lians-memory-mcp.exe"),
        ("linux", Path("bin/lians-memory-mcp"), "lians-memory-mcp"),
        ("darwin", Path("bin/lians-memory-mcp"), "lians-memory-mcp"),
    ],
)
def test_launcher_copies_frozen_console_shim_without_resolving_dependencies(
    tmp_path: Path,
    platform: str,
    relative: Path,
    destination_name: str,
) -> None:
    data_home = tmp_path / "data"
    source = data_home / "venv" / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen-console-shim-v1")
    tool_bin = tmp_path / "uv-bin"

    installed = bootstrap.install_launcher(data_home, platform=platform, tool_bin=tool_bin)
    assert installed == tool_bin.resolve() / destination_name
    assert installed.read_bytes() == source.read_bytes()
    record = json.loads((data_home / bootstrap.LAUNCHER_RECORD_FILENAME).read_text())
    assert record["path"] == str(installed)
    assert record["sha256"] == hashlib.sha256(installed.read_bytes()).hexdigest()

    source.write_bytes(b"frozen-console-shim-v2")
    assert (
        bootstrap.install_launcher(data_home, platform=platform, tool_bin=tool_bin).read_bytes()
        == b"frozen-console-shim-v2"
    )


def test_launcher_refuses_to_replace_an_unowned_command(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    source = bootstrap.runtime_launcher(data_home, platform="linux")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"ours")
    tool_bin = tmp_path / "uv-bin"
    tool_bin.mkdir()
    (tool_bin / bootstrap.LAUNCHER_COMMAND).write_bytes(b"someone-elses")

    with pytest.raises(bootstrap.BootstrapError, match="unowned launcher"):
        bootstrap.install_launcher(data_home, platform="linux", tool_bin=tool_bin)


def test_windows_launcher_upgrade_keeps_locked_owned_working_shim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    source = bootstrap.runtime_launcher(data_home, platform="win32")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen-console-shim-v1")
    tool_bin = tmp_path / "uv-bin"
    installed = bootstrap.install_launcher(data_home, platform="win32", tool_bin=tool_bin)
    source.write_bytes(b"frozen-console-shim-v2")

    real_replace = bootstrap.os.replace

    def locked_launcher_replace(source_path: object, destination_path: object) -> None:
        if Path(destination_path) == installed:
            raise PermissionError("launcher is in use")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(bootstrap.os, "replace", locked_launcher_replace)
    monkeypatch.setattr(bootstrap, "_owned_launcher_still_works", lambda *_: True)

    retained = bootstrap.install_launcher(data_home, platform="win32", tool_bin=tool_bin)
    record = json.loads((data_home / bootstrap.LAUNCHER_RECORD_FILENAME).read_text())

    assert retained == installed
    assert retained.read_bytes() == b"frozen-console-shim-v1"
    assert record["sha256"] == hashlib.sha256(retained.read_bytes()).hexdigest()


def test_windows_launcher_upgrade_rejects_locked_broken_shim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    source = bootstrap.runtime_launcher(data_home, platform="win32")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen-console-shim-v1")
    tool_bin = tmp_path / "uv-bin"
    installed = bootstrap.install_launcher(data_home, platform="win32", tool_bin=tool_bin)
    source.write_bytes(b"frozen-console-shim-v2")

    real_replace = bootstrap.os.replace

    def locked_launcher_replace(source_path: object, destination_path: object) -> None:
        if Path(destination_path) == installed:
            raise PermissionError("launcher is in use")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(bootstrap.os, "replace", locked_launcher_replace)
    monkeypatch.setattr(bootstrap, "_owned_launcher_still_works", lambda *_: False)

    with pytest.raises(bootstrap.BootstrapError, match="could not install"):
        bootstrap.install_launcher(data_home, platform="win32", tool_bin=tool_bin)


def test_launcher_path_check_is_exact_and_gives_restart_guidance(tmp_path: Path) -> None:
    tool_bin = tmp_path / "uv-bin"
    tool_bin.mkdir()
    suffix = ".exe" if sys.platform == "win32" else ""
    launcher = tool_bin / f"{bootstrap.LAUNCHER_COMMAND}{suffix}"
    launcher.write_bytes(b"launcher")
    if sys.platform != "win32":
        launcher.chmod(0o700)

    assert bootstrap.launcher_on_path(launcher, {"PATH": str(tool_bin)})
    assert not bootstrap.launcher_on_path(launcher, {"PATH": str(tmp_path / "elsewhere")})
    message = bootstrap.launcher_path_message(launcher)
    assert "uv tool update-shell" in message
    assert "restart Codex" in message


def test_doctor_detects_additive_user_configuration_without_reading_database(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "hooks.json").write_text(
        '{"hooks":{"UserPromptSubmit":[{"command":"run Lians hook"}]}}',
        encoding="utf-8",
    )
    (codex_home / "config.toml").write_text(
        '[mcp_servers.lians]\ncommand = "uv"\n', encoding="utf-8"
    )
    warnings = bootstrap.duplicate_user_configuration_warnings(codex_home=codex_home)
    assert len(warnings) == 2
    assert "additively" in warnings[0]
    assert "disable one copy" in warnings[1]


def test_duplicate_warning_scan_honors_process_codex_home(tmp_path: Path) -> None:
    custom_codex_home = tmp_path / "custom-codex-home"
    custom_codex_home.mkdir()
    (custom_codex_home / "config.toml").write_text(
        '[mcp_servers.lians]\ncommand = "legacy"\n',
        encoding="utf-8",
    )

    warnings = bootstrap.duplicate_user_configuration_warnings(
        environ={"CODEX_HOME": str(custom_codex_home)}
    )

    assert len(warnings) == 1
    assert "disable one copy" in warnings[0]


def test_duplicate_warning_scan_allows_disabled_legacy_server(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        '[mcp_servers.lians]\nenabled = false\nurl = "https://legacy.invalid"\n',
        encoding="utf-8",
    )

    warnings = bootstrap.duplicate_user_configuration_warnings(codex_home=codex_home)

    assert warnings == []


def test_duplicate_warning_scan_rejects_relative_process_codex_home() -> None:
    with pytest.raises(bootstrap.BootstrapError, match="CODEX_HOME must be an absolute"):
        bootstrap.duplicate_user_configuration_warnings(environ={"CODEX_HOME": "relative"})


def test_legacy_hook_script_is_detected_without_lians_literal(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "/opt/python integrations/codex/"
                                        "user_prompt_submit_recall.py --prewarm-quiet"
                                    ),
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    warnings = bootstrap.duplicate_user_configuration_warnings(codex_home=codex_home)

    assert len(warnings) == 1
    assert "additively" in warnings[0]


def test_doctor_treats_duplicate_legacy_configuration_as_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    python = bootstrap.runtime_python(data_home)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    launcher = (
        tmp_path
        / "bin"
        / ("lians-memory-mcp.exe" if sys.platform == "win32" else "lians-memory-mcp")
    )
    launcher.parent.mkdir()
    launcher.write_bytes(b"launcher")
    wheel = _wheel_artifact(tmp_path)
    profile = {
        "schema_version": 1,
        "mode": "managed",
        "managed_url": "https://api.lians.dev",
        "runtime_lock_sha256": "c" * 64,
        "sdk": {"version": wheel.version, "sha256": wheel.sha256},
    }
    monkeypatch.setattr(bootstrap, "discover_wheel", lambda *_args, **_kwargs: wheel)
    monkeypatch.setattr(bootstrap, "validate_frozen_runtime", lambda *_args, **_kwargs: "c" * 64)
    monkeypatch.setattr(bootstrap, "read_profile", lambda _data_home: profile)
    monkeypatch.setattr(bootstrap, "_installed_sdk_matches_bundle", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bootstrap, "_private_path_permissions_ok", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bootstrap, "uv_tool_bin_dir", lambda _values: launcher.parent)
    monkeypatch.setattr(bootstrap, "_record_owns_launcher", lambda *_args: True)
    monkeypatch.setattr(bootstrap, "launcher_on_path", lambda *_args: True)
    monkeypatch.setattr(
        bootstrap,
        "duplicate_user_configuration_warnings",
        lambda **_kwargs: ["disable the legacy hook before relying on the plugin"],
    )

    result = bootstrap.doctor(data_home, {"LIANS_API_KEY": "present"})

    assert result["checks"]["legacy_configuration_clear"] is False
    assert result["ok"] is False
    assert "disable the legacy hook" in result["messages"][-1]


def test_doctor_blocks_an_insecure_local_key_or_daemon_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    key = data_home / bootstrap.LOCAL_MASTER_KEY_FILENAME
    key.write_text(base64.b64encode(b"k" * 32).decode("ascii"), encoding="ascii")
    python = bootstrap.runtime_python(data_home)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    launcher = (
        tmp_path
        / "bin"
        / ("lians-memory-mcp.exe" if sys.platform == "win32" else "lians-memory-mcp")
    )
    launcher.parent.mkdir()
    launcher.write_bytes(b"launcher")
    wheel = _wheel_artifact(tmp_path)
    profile = {
        "schema_version": 1,
        "mode": "local",
        "bge_artifact_dir": str(tmp_path / "model"),
        "runtime_lock_sha256": "c" * 64,
        "sdk": {"version": wheel.version, "sha256": wheel.sha256},
    }
    monkeypatch.setattr(bootstrap, "discover_wheel", lambda *_args, **_kwargs: wheel)
    monkeypatch.setattr(bootstrap, "validate_frozen_runtime", lambda *_args, **_kwargs: "c" * 64)
    monkeypatch.setattr(bootstrap, "read_profile", lambda _data_home: profile)
    monkeypatch.setattr(bootstrap, "_installed_sdk_matches_bundle", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        bootstrap,
        "_private_path_permissions_ok",
        lambda path, **_kwargs: path != key,
    )
    monkeypatch.setattr(bootstrap, "validate_bge_artifact_directory", lambda _path: True)
    monkeypatch.setattr(bootstrap, "_existing_daemon_token_is_private", lambda _path: False)
    monkeypatch.setattr(bootstrap, "uv_tool_bin_dir", lambda _values: launcher.parent)
    monkeypatch.setattr(bootstrap, "_record_owns_launcher", lambda *_args: True)
    monkeypatch.setattr(bootstrap, "launcher_on_path", lambda *_args: True)
    monkeypatch.setattr(bootstrap, "duplicate_user_configuration_warnings", lambda **_kwargs: [])

    result = bootstrap.doctor(data_home, {})

    assert result["checks"]["local_key_private"] is False
    assert result["checks"]["daemon_token_private"] is False
    assert result["ok"] is False


def test_sync_forces_bundled_sdk_reinstall_and_scrubs_setup_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "data"
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wheel = _wheel_artifact(tmp_path)
    captured: dict[str, object] = {}
    sentinel = "must-not-reach-uv"
    for name in (
        "LIANS_API_KEY",
        "MASTER_ENCRYPTION_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(name, sentinel)
    monkeypatch.setattr(bootstrap, "validate_frozen_runtime", lambda *_args, **_kwargs: "c" * 64)
    monkeypatch.setattr(bootstrap, "find_uv", lambda: "uv")
    monkeypatch.setattr(
        bootstrap,
        "_safe_directory",
        lambda path: path.mkdir(parents=True, exist_ok=True),
    )

    def fake_run(command: list[str], *, environ: dict[str, str]) -> None:
        captured["command"] = command
        captured["environment"] = environ
        _write_fake_installed_sdk(data_home, wheel, platform=sys.platform)
        python = bootstrap.runtime_python(data_home)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"python")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    assert bootstrap.sync_runtime(data_home, wheel, runtime_dir) == "c" * 64
    command = captured["command"]
    environment = captured["environment"]
    assert command.count("--reinstall-package") == 1
    assert command.count("lians-sdk") == 1
    assert command[-2:] == ["--reinstall-package", "lians-sdk"]
    assert "--frozen" in command
    assert sentinel not in environment.values()
    assert environment["UV_NO_CONFIG"] == "1"


def test_installed_sdk_verification_detects_tampering_version_and_source(
    tmp_path: Path,
) -> None:
    wheel = _wheel_artifact(tmp_path)
    good_home = tmp_path / "good"
    package_file = _write_fake_installed_sdk(good_home, wheel, platform=sys.platform)
    assert bootstrap._installed_sdk_matches_bundle(good_home, wheel)
    package_file.write_text("tampered", encoding="utf-8")
    assert not bootstrap._installed_sdk_matches_bundle(good_home, wheel)

    wrong_version_home = tmp_path / "wrong-version"
    _write_fake_installed_sdk(
        wrong_version_home,
        wheel,
        version="0.5.0+codex.wrong",
        platform=sys.platform,
    )
    assert not bootstrap._installed_sdk_matches_bundle(wrong_version_home, wheel)

    wrong_source = tmp_path / "other.whl"
    wrong_source.write_bytes(b"other")
    wrong_source_home = tmp_path / "wrong-source"
    _write_fake_installed_sdk(wrong_source_home, wheel, source=wrong_source, platform=sys.platform)
    assert not bootstrap._installed_sdk_matches_bundle(wrong_source_home, wheel)


def test_frozen_runtime_names_only_the_bundled_sdk() -> None:
    artifact = bootstrap.discover_wheel(PLUGIN_ROOT / "vendor")
    lock_hash = bootstrap.validate_frozen_runtime(PLUGIN_ROOT / "runtime", artifact)
    assert len(lock_hash) == 64
    pyproject = (PLUGIN_ROOT / "runtime" / "pyproject.toml").read_text(encoding="utf-8")
    lock = (PLUGIN_ROOT / "runtime" / "uv.lock").read_text(encoding="utf-8")
    assert artifact.path.name in pyproject
    assert artifact.sha256 in lock
    assert '\nname = "greenlet"\n' in lock
    assert '{ name = "sqlalchemy", extra = ["asyncio"] }' in lock
    assert "https://pypi.org/project/lians" not in pyproject.lower()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_clean_frozen_runtime_sync_installs_greenlet(tmp_path: Path) -> None:
    artifact = bootstrap.discover_wheel(PLUGIN_ROOT / "vendor")
    data_home = tmp_path / "data"

    lock_hash = bootstrap.sync_runtime(data_home, artifact)

    assert len(lock_hash) == 64
    greenlet_check = (
        "import importlib.metadata as metadata; import greenlet; "
        "print(metadata.version('greenlet'))"
    )
    completed = subprocess.run(
        [
            str(bootstrap.runtime_python(data_home)),
            "-I",
            "-B",
            "-c",
            greenlet_check,
        ],
        cwd=tmp_path,
        env=bootstrap._scrubbed_setup_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()


def test_distributable_contains_no_machine_state_or_model_binary() -> None:
    forbidden_names = {"config.toml", "hooks.json", "memory.sqlite3", "mcp.db"}
    model_suffixes = {".onnx", ".safetensors", ".pt", ".pth"}
    for path in PLUGIN_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(PLUGIN_ROOT)
        if "__pycache__" in relative.parts:
            continue
        assert path.name not in forbidden_names or relative == Path("hooks/hooks.json")
        assert path.suffix.lower() not in model_suffixes
        assert "venv" not in relative.parts
