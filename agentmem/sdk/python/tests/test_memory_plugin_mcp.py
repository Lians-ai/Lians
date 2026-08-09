from __future__ import annotations

import base64
import json
import os
import sys
import types
from pathlib import Path

import pytest

from lians import memory_plugin_mcp


def _profile(data_home: Path, *, mode: str, version: str, **values: object) -> None:
    data_home.mkdir(parents=True)
    document: dict[str, object] = {
        "schema_version": 1,
        "mode": mode,
        "sdk": {"version": version},
    }
    document.update(values)
    (data_home / "profile.json").write_text(json.dumps(document), encoding="utf-8")


def _use_data_home(monkeypatch: pytest.MonkeyPatch, data_home: Path) -> None:
    monkeypatch.setattr(memory_plugin_mcp, "native_data_home", lambda _values=None: data_home)


def test_local_profile_forces_project_isolation_and_uses_bge_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "bge"
    artifact.mkdir()
    _profile(
        data_home,
        mode="local",
        version="0.5.0+test",
        bge_artifact_dir=str(artifact),
    )
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    (data_home / memory_plugin_mcp.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n", encoding="ascii"
    )
    first = tmp_path / "customer-a" / "repo"
    second = tmp_path / "customer-b" / "repo"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    _use_data_home(monkeypatch, data_home)
    ambient = {
        "LIANS_AGENT_ID": "ambient-agent",
        "LIANS_NAMESPACE": "ambient-namespace",
        "LIANS_URL": "https://ambient.invalid",
        "LIANS_API_KEY": "must-be-removed",
        "LIANS_MCP_SUBJECT_ID": "ambient-subject",
        "LIANS_MCP_LOCAL_SUBJECT_ID": "ambient-local-subject",
    }

    first_env, _ = memory_plugin_mcp.configured_environment(
        ambient,
        project_root=first,
        installed_version="0.5.0+test",
    )
    second_env, _ = memory_plugin_mcp.configured_environment(
        ambient,
        project_root=second,
        installed_version="0.5.0+test",
    )

    assert first_env["LIANS_MCP_PROJECT_ROOT"] == str(first.resolve())
    assert first_env["LIANS_AGENT_ID"] == ""
    assert first_env["LIANS_NAMESPACE"] == ""
    assert first_env["BGE_ONNX_ARTIFACT_DIR"] == str(artifact)
    assert first_env["LIANS_LOCAL_DB"] != second_env["LIANS_LOCAL_DB"]
    assert "LIANS_URL" not in first_env
    assert "LIANS_API_KEY" not in first_env
    assert first_env["MASTER_ENCRYPTION_KEY"] == encoded_key
    assert first_env["AGENTMEM_ALLOW_UNENCRYPTED"] == "false"
    assert first_env["LIANS_MCP_LOCAL_SUBJECT_ID"] != second_env["LIANS_MCP_LOCAL_SUBJECT_ID"]
    expected_first_subject = f"codex-project:{memory_plugin_mcp.project_scope(first)}"
    assert first_env["LIANS_MCP_LOCAL_SUBJECT_ID"] == expected_first_subject
    assert first_env["LIANS_MCP_SUBJECT_ID"] == first_env["LIANS_MCP_LOCAL_SUBJECT_ID"]
    assert second_env["LIANS_MCP_SUBJECT_ID"] == second_env["LIANS_MCP_LOCAL_SUBJECT_ID"]


def test_installed_wrapper_scrubs_hostile_local_provider_and_egress_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "bge"
    artifact.mkdir()
    _profile(
        data_home,
        mode="local",
        version="0.5.0+test",
        bge_artifact_dir=str(artifact),
    )
    encoded_key = base64.b64encode(b"p" * 32).decode("ascii")
    (data_home / memory_plugin_mcp.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n",
        encoding="ascii",
    )
    _use_data_home(monkeypatch, data_home)
    hostile = {
        name: "hostile-inherited-value"
        for name, _value in memory_plugin_mcp._LOCAL_RUNTIME_SECURITY_ENV
    }
    hostile.update(
        {
            "MASTER_ENCRYPTION_KEY": base64.b64encode(b"x" * 32).decode("ascii"),
            "AGENTMEM_ALLOW_UNENCRYPTED": "true",
            "BGE_ONNX_ARTIFACT_DIR": str(tmp_path / "untrusted-model"),
            "PYTHONPATH": str(tmp_path / "import-canary"),
            "PYTHONHOME": str(tmp_path / "runtime-canary"),
            "PYTHONNOUSERSITE": "0",
            "PYTHONSAFEPATH": "0",
            "LIANS_MCP_ENABLED_TOOLS": "remember,recall,admin",
            "LIANS_MCP_SCHEMA_PROFILE": "full",
            "LIANS_MCP_RECALL_K": "100",
            "LIANS_MCP_CONTEXT_MAX_TOKENS": "2500",
            "LIANS_MCP_PREWARM": "foreground",
            "BGE_ONNX_INTRA_OP_THREADS": "256",
        }
    )
    monkeypatch.setattr(memory_plugin_mcp.os, "cpu_count", lambda: 64)

    child, _ = memory_plugin_mcp.configured_environment(
        hostile,
        project_root=tmp_path,
        installed_version="0.5.0+test",
    )

    required_forced = {
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
    for name, value in memory_plugin_mcp._LOCAL_RUNTIME_SECURITY_ENV:
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
    assert child["LIANS_MCP_ENABLED_TOOLS"] == "remember,recall"
    assert child["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert child["LIANS_MCP_RECALL_K"] == "20"
    assert child["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert child["LIANS_MCP_PREWARM"] == "background"
    assert child["BGE_ONNX_INTRA_OP_THREADS"] == "8"


def test_local_codex_dynamic_scope_defers_all_project_paths_to_request_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProjectRootMustNotBeResolved:
        def __fspath__(self) -> str:
            raise AssertionError("dynamic Codex mode must not inspect the launcher cwd")

    data_home = tmp_path / "data"
    artifact = tmp_path / "bge"
    artifact.mkdir()
    _profile(
        data_home,
        mode="local",
        version="0.5.0+test",
        bge_artifact_dir=str(artifact),
    )
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    (data_home / memory_plugin_mcp.LOCAL_MASTER_KEY_FILENAME).write_text(
        encoded_key + "\n",
        encoding="ascii",
    )
    _use_data_home(monkeypatch, data_home)

    child, _ = memory_plugin_mcp.configured_environment(
        {
            memory_plugin_mcp.CODEX_DYNAMIC_SCOPE_ENV: "true",
            "LIANS_MCP_PROJECT_ROOT": "ambient-project",
            "LIANS_LOCAL_DB": "ambient.sqlite3",
            "LIANS_AGENT_ID": "ambient-agent",
            "LIANS_NAMESPACE": "ambient-namespace",
            "LIANS_MCP_SUBJECT_ID": "ambient-subject",
            "LIANS_MCP_LOCAL_SUBJECT_ID": "ambient-local-subject",
            "LIANS_MCP_ENABLED_TOOLS": "recall,admin",
            "LIANS_MCP_SCHEMA_PROFILE": "full",
            "LIANS_MCP_RECALL_K": "100",
            "LIANS_MCP_CONTEXT_MAX_TOKENS": "2500",
            "LIANS_MCP_PREWARM": "background",
        },
        project_root=ProjectRootMustNotBeResolved(),
        installed_version="0.5.0+test",
    )

    assert child[memory_plugin_mcp.CODEX_DYNAMIC_SCOPE_ENV] == "true"
    assert child[memory_plugin_mcp.MCP_DATA_HOME_ENV] == str(data_home)
    assert child["LIANS_MEMORY_HOME"] == str(data_home)
    assert child["LIANS_AGENT_ID"] == ""
    assert child["LIANS_NAMESPACE"] == ""
    assert "LIANS_MCP_PROJECT_ROOT" not in child
    assert "LIANS_LOCAL_DB" not in child
    assert "LIANS_MCP_SUBJECT_ID" not in child
    assert "LIANS_MCP_LOCAL_SUBJECT_ID" not in child
    assert child["LIANS_MCP_ENABLED_TOOLS"] == "remember,recall"
    assert child["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert child["LIANS_MCP_RECALL_K"] == "20"
    assert child["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert child["LIANS_MCP_PREWARM"] == "off"
    assert not (data_home / "projects").exists()


def test_managed_profile_owns_url_but_key_remains_environment_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    _profile(
        data_home,
        mode="managed",
        version="0.5.0+test",
        managed_url="https://partner.lians.example/",
    )
    _use_data_home(monkeypatch, data_home)
    child, _ = memory_plugin_mcp.configured_environment(
        {
            "LIANS_URL": "https://ambient.invalid",
            "LIANS_API_KEY": "partner-secret",
            "LIANS_AGENT_ID": "ambient-agent",
            "LIANS_NAMESPACE": "ambient-namespace",
            "MASTER_ENCRYPTION_KEY": "ambient-local-key",
            "AGENTMEM_ALLOW_UNENCRYPTED": "true",
            "LIANS_MCP_SUBJECT_ID": "ambient-subject",
            "LIANS_MCP_LOCAL_SUBJECT_ID": "ambient-local-subject",
            "LIANS_MCP_ENABLED_TOOLS": "remember,recall,admin",
            "LIANS_MCP_SCHEMA_PROFILE": "full",
            "LIANS_MCP_RECALL_K": "100",
            "LIANS_MCP_CONTEXT_MAX_TOKENS": "2500",
            "LIANS_MCP_PREWARM": "foreground",
        },
        project_root=tmp_path,
        installed_version="0.5.0+test",
    )

    assert child["LIANS_URL"] == "https://partner.lians.example"
    assert child["LIANS_API_KEY"] == "partner-secret"
    assert child["LIANS_AGENT_ID"] == ""
    assert child["LIANS_NAMESPACE"] == ""
    assert "MASTER_ENCRYPTION_KEY" not in child
    assert "AGENTMEM_ALLOW_UNENCRYPTED" not in child
    assert "LIANS_MCP_LOCAL_SUBJECT_ID" not in child
    assert child["LIANS_MCP_ENABLED_TOOLS"] == "remember,recall"
    assert child["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert child["LIANS_MCP_RECALL_K"] == "20"
    assert child["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert child["LIANS_MCP_PREWARM"] == "background"
    assert (
        child["LIANS_MCP_SUBJECT_ID"]
        == f"codex-project:{memory_plugin_mcp.project_scope(tmp_path)}"
    )
    assert "partner-secret" not in (data_home / "profile.json").read_text(encoding="utf-8")


def test_profile_sdk_must_match_frozen_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    _profile(
        data_home,
        mode="managed",
        version="0.5.0+expected",
        managed_url="https://partner.example",
    )
    _use_data_home(monkeypatch, data_home)
    with pytest.raises(memory_plugin_mcp.MemoryPluginConfigurationError, match="does not match"):
        memory_plugin_mcp.configured_environment(
            {"LIANS_API_KEY": "set"},
            project_root=tmp_path,
            installed_version="0.5.0+other",
        )


def test_managed_profile_requires_an_explicit_https_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    _profile(data_home, mode="managed", version="0.5.0+test")
    _use_data_home(monkeypatch, data_home)
    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="explicit HTTPS URL",
    ):
        memory_plugin_mcp.configured_environment(
            {"LIANS_API_KEY": "set"},
            project_root=tmp_path,
            installed_version="0.5.0+test",
        )


@pytest.mark.parametrize(
    "managed_url",
    [
        "http://partner.example",
        "https://user@partner.example",
        "https://user:password@partner.example",
        "https://partner.example/api?token=secret-sentinel",
        "https://partner.example/api#configuration",
        "https:///missing-host",
        "https://partner.example/path with whitespace",
        "https://partner.example/path\nwith-control",
    ],
)
def test_managed_profile_rejects_secret_bearing_or_ambiguous_endpoints(
    tmp_path: Path,
    managed_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    _profile(
        data_home,
        mode="managed",
        version="0.5.0+test",
        managed_url=managed_url,
    )
    _use_data_home(monkeypatch, data_home)
    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="explicit HTTPS URL",
    ) as error:
        memory_plugin_mcp.configured_environment(
            {"LIANS_API_KEY": "set"},
            project_root=tmp_path,
            installed_version="0.5.0+test",
        )
    assert "password" not in str(error.value)
    assert "secret-sentinel" not in str(error.value)


def test_local_profile_requires_the_installation_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    artifact = tmp_path / "bge"
    artifact.mkdir()
    _profile(
        data_home,
        mode="local",
        version="0.5.0+test",
        bge_artifact_dir=str(artifact),
    )
    _use_data_home(monkeypatch, data_home)
    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="encryption key is missing",
    ):
        memory_plugin_mcp.configured_environment(
            {},
            project_root=tmp_path,
            installed_version="0.5.0+test",
        )


@pytest.mark.parametrize(
    ("platform", "values", "relative"),
    [
        (
            "win32",
            {"LOCALAPPDATA": "native-base", "PLUGIN_DATA": "plugin-cache"},
            Path("native-base") / "Lians" / "CodexMemory",
        ),
        (
            "darwin",
            {"XDG_DATA_HOME": "ignored-xdg", "PLUGIN_DATA": "plugin-cache"},
            Path("Library") / "Application Support" / "Lians" / "CodexMemory",
        ),
        (
            "linux",
            {"XDG_DATA_HOME": "native-base", "PLUGIN_DATA": "plugin-cache"},
            Path("native-base") / "lians" / "codex-memory",
        ),
        (
            "linux",
            {"PLUGIN_DATA": "plugin-cache"},
            Path(".local") / "share" / "lians" / "codex-memory",
        ),
    ],
)
def test_native_home_is_platform_specific_and_ignores_plugin_cache(
    tmp_path: Path,
    platform: str,
    values: dict[str, str],
    relative: Path,
) -> None:
    resolved_values = {
        name: str(tmp_path / value) if name != "PLUGIN_DATA" else value
        for name, value in values.items()
    }
    expected = (tmp_path / relative).resolve()

    assert (
        memory_plugin_mcp.native_data_home(
            resolved_values,
            platform=platform,
            home=tmp_path,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("platform", "base_name", "relative_base", "fallback"),
    [
        ("win32", "LOCALAPPDATA", "relative-local", Path("AppData") / "Local"),
        ("linux", "XDG_DATA_HOME", "relative-xdg", Path(".local") / "share"),
    ],
)
def test_native_home_ignores_relative_base_and_uses_absolute_home(
    tmp_path: Path,
    platform: str,
    base_name: str,
    relative_base: str,
    fallback: Path,
) -> None:
    suffix = (
        Path("Lians") / "CodexMemory"
        if platform == "win32"
        else Path("lians") / "codex-memory"
    )
    result = memory_plugin_mcp.native_data_home(
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
def test_native_home_rejects_relative_home_without_an_absolute_native_base(
    platform: str,
    values: dict[str, str],
) -> None:
    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="native user home must be an absolute path",
    ):
        memory_plugin_mcp.native_data_home(
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

    assert memory_plugin_mcp.native_data_home(
        {base_name: str(base)},
        platform=platform,
        home=Path("relative-home"),
    ) == (base / suffix).resolve()


@pytest.mark.parametrize("override", ["redirected", " ", "../elsewhere"])
def test_native_home_rejects_every_nonempty_override(
    tmp_path: Path,
    override: str,
) -> None:
    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="LIANS_MEMORY_HOME overrides are not supported",
    ):
        memory_plugin_mcp.native_data_home(
            {"LIANS_MEMORY_HOME": override},
            platform="linux",
            home=tmp_path,
        )


@pytest.mark.parametrize(
    ("filename", "reader"),
    [
        (memory_plugin_mcp.PROFILE_FILENAME, memory_plugin_mcp._read_profile),
        (
            memory_plugin_mcp.LOCAL_MASTER_KEY_FILENAME,
            memory_plugin_mcp._read_local_master_key,
        ),
    ],
)
def test_profile_and_key_reject_symlink_or_reparse_files(
    tmp_path: Path,
    filename: str,
    reader,
) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    outside = tmp_path / f"outside-{filename}"
    outside.write_text("{}", encoding="utf-8")
    try:
        (data_home / filename).symlink_to(outside)
    except OSError:
        pytest.skip("creating a test symlink is not permitted on this host")

    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="regular non-reparse file",
    ):
        reader(data_home)


def test_private_mcp_runtime_directory_rejects_reparse_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    runtime = data_home / memory_plugin_mcp.MCP_RUNTIME_DIRECTORY
    runtime.mkdir(parents=True)
    original = memory_plugin_mcp._is_symlink_or_reparse
    monkeypatch.setattr(
        memory_plugin_mcp,
        "_is_symlink_or_reparse",
        lambda path: path == runtime or original(path),
    )

    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match="non-reparse directory",
    ):
        memory_plugin_mcp._private_mcp_runtime_directory(data_home)


def test_private_mcp_runtime_directory_rejects_dotenv(
    tmp_path: Path,
) -> None:
    data_home = tmp_path / "data"
    runtime = data_home / memory_plugin_mcp.MCP_RUNTIME_DIRECTORY
    runtime.mkdir(parents=True)
    (runtime / ".env").write_text("LIANS_URL=https://poison.invalid\n", encoding="utf-8")

    with pytest.raises(
        memory_plugin_mcp.MemoryPluginConfigurationError,
        match=r"must not contain a \.env file",
    ):
        memory_plugin_mcp._private_mcp_runtime_directory(data_home)


def test_launcher_leaves_project_dotenv_before_importing_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "untrusted-project"
    project.mkdir()
    (project / ".env").write_text(
        "LIANS_URL=https://poison.invalid\nLIANS_API_KEY=poison-key\n",
        encoding="utf-8",
    )
    data_home = tmp_path / "native-data"
    captured: dict[str, object] = {}
    child = {
        "LIANS_MEMORY_HOME": str(data_home),
        memory_plugin_mcp.CODEX_DYNAMIC_SCOPE_ENV: "true",
    }
    monkeypatch.setattr(
        memory_plugin_mcp,
        "configured_environment",
        lambda: (dict(child), {"mode": "local"}),
    )
    fake_server = types.ModuleType("lians.mcp_server")

    def fake_main() -> None:
        captured["cwd"] = Path.cwd()
        captured["runtime_cwd"] = os.environ.get("LIANS_PLUGIN_RUNTIME_CWD")
        captured["url"] = os.environ.get("LIANS_URL")
        captured["api_key"] = os.environ.get("LIANS_API_KEY")

    fake_server.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lians.mcp_server", fake_server)
    monkeypatch.chdir(project)
    original_environment = dict(os.environ)
    try:
        result = memory_plugin_mcp.main([])
    finally:
        os.environ.clear()
        os.environ.update(original_environment)

    expected = (data_home / memory_plugin_mcp.MCP_RUNTIME_DIRECTORY).resolve()
    assert result == 0
    assert captured["cwd"] == expected
    assert captured["runtime_cwd"] == str(expected)
    assert captured["url"] is None
    assert captured["api_key"] is None
    assert not (expected / ".env").exists()
    if os.name != "nt":
        assert expected.stat().st_mode & 0o777 == 0o700
