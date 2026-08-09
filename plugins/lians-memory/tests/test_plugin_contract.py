from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _json(relative: str) -> dict:
    return json.loads((PLUGIN_ROOT / relative).read_text(encoding="utf-8"))


def test_manifest_and_team_marketplace_are_installable() -> None:
    manifest = _json(".codex-plugin/plugin.json")
    assert manifest["name"] == "lians-memory"
    assert re.fullmatch(r"0\.1\.0\+codex\.\d{14}", manifest["version"])
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert isinstance(manifest["interface"]["defaultPrompt"], list)
    assert (PLUGIN_ROOT / manifest["interface"]["composerIcon"]).is_file()

    marketplace = json.loads(
        (REPO_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] == "lians"
    [entry] = marketplace["plugins"]
    assert entry["name"] == "lians-memory"
    assert entry["source"] == {"source": "local", "path": "./plugins/lians-memory"}


def test_distributable_carries_apache_license_text() -> None:
    license_text = (PLUGIN_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text


def test_mcp_is_optional_compact_and_credential_forwarding_only() -> None:
    servers = _json(".mcp.json")["mcpServers"]
    assert set(servers) == {"lians_memory"}
    [server] = servers.values()
    assert server["command"] == "lians-memory-mcp"
    assert "args" not in server
    assert server["required"] is False
    assert server["enabled_tools"] == ["remember", "recall"]
    assert server["env_vars"] == ["LIANS_API_KEY"]
    assert server["env"]["LIANS_MEMORY_HOME"] == ""
    assert server["env"]["PYTHONPATH"] == ""
    assert server["env"]["PYTHONHOME"] == ""
    assert server["env"]["PYTHONNOUSERSITE"] == "1"
    assert server["env"]["PYTHONSAFEPATH"] == "1"
    assert server["env"]["LIANS_MCP_CODEX_DYNAMIC_SCOPE"] == "true"
    assert server["env"]["LIANS_MCP_SCHEMA_PROFILE"] == "compact"
    assert server["env"]["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "768"
    assert server["env"]["LIANS_MCP_PREWARM"] == "off"
    assert server["cwd"] == "."
    assert "PLUGIN_ROOT" not in json.dumps(server)
    assert not any("API_KEY" in key for key in server["env"])


def test_hooks_use_bundled_launcher_and_bound_context() -> None:
    document = _json("hooks/hooks.json")
    [session_group] = document["hooks"]["SessionStart"]
    [session_hook] = session_group["hooks"]
    assert session_group["matcher"] == "^(startup|resume|clear)$"
    assert session_hook["command"].endswith('run_hook.sh" prewarm')
    assert session_hook["command"].startswith('/bin/sh "${PLUGIN_ROOT}')
    assert "uv" not in session_hook["command"]
    assert "%SystemRoot%\\System32" in session_hook["commandWindows"]
    assert "%PLUGIN_ROOT%" in session_hook["commandWindows"]
    assert "run_hook.ps1" in session_hook["commandWindows"]

    [prompt_group] = document["hooks"]["UserPromptSubmit"]
    [prompt_hook] = prompt_group["hooks"]
    assert prompt_hook["command"].endswith('run_hook.sh" hook')
    assert "uv" not in prompt_hook["command"]
    assert "run_hook.ps1" in prompt_hook["commandWindows"]
    assert prompt_hook["additionalContextLimit"] == 768
    assert "matcher" not in prompt_group
    assert (PLUGIN_ROOT / "scripts/run_hook.sh").is_file()
    assert (PLUGIN_ROOT / "scripts/run_hook.ps1").is_file()
    powershell_wrapper = (PLUGIN_ROOT / "scripts/run_hook.ps1").read_text(encoding="utf-8")
    assert "[Console]::In.ReadToEnd()" in powershell_wrapper
    assert "$payload | & $python -B $launcher $Action" in powershell_wrapper
    shell_wrapper = (PLUGIN_ROOT / "scripts/run_hook.sh").read_text(encoding="utf-8")
    assert 'exec "$python" -B' in shell_wrapper
    for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONSAFEPATH"):
        assert variable in powershell_wrapper
        assert variable in shell_wrapper


def test_isolated_first_run_launcher_ignores_hostile_pythonpath(tmp_path: Path) -> None:
    canary = tmp_path / "hostile-imports"
    canary.mkdir()
    marker = tmp_path / "sitecustomize-imported"
    (canary / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    (canary / "bootstrap.py").write_text(
        "raise RuntimeError('hostile PYTHONPATH bootstrap imported')\n",
        encoding="utf-8",
    )
    environ = dict(os.environ)
    environ.update(
        {
            "PYTHONPATH": str(canary),
            "PYTHONHOME": "",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(PLUGIN_ROOT / "scripts/lians_plugin.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "hostile PYTHONPATH bootstrap imported" not in result.stderr
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook wrapper contract")
@pytest.mark.parametrize("use_xdg", [False, True])
def test_posix_hook_uses_only_absolute_native_base_and_scrubs_python_import_env(
    tmp_path: Path,
    use_xdg: bool,
) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    native_base = xdg if use_xdg else home / ".local" / "share"
    trusted_python = native_base / "lians" / "codex-memory" / "venv" / "bin" / "python"
    trusted_python.parent.mkdir(parents=True)
    marker = tmp_path / "trusted-marker"
    trusted_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"${PYTHONPATH-unset}\" \"${PYTHONHOME-unset}\" "
        "\"${PYTHONNOUSERSITE-unset}\" \"${PYTHONSAFEPATH-unset}\" > \"$MARKER\"\n",
        encoding="utf-8",
    )
    trusted_python.chmod(0o700)
    project = tmp_path / "project"
    trap_python = (
        project
        / "relative-base"
        / "lians"
        / "codex-memory"
        / "venv"
        / "bin"
        / "python"
    )
    trap_python.parent.mkdir(parents=True)
    trap_marker = tmp_path / "trap-marker"
    trap_python.write_text(
        f"#!/bin/sh\nprintf trapped > {shlex.quote(str(trap_marker))}\n",
        encoding="utf-8",
    )
    trap_python.chmod(0o700)
    environ = dict(os.environ)
    environ.update(
        {
            "HOME": "relative-home" if use_xdg else str(home),
            "XDG_DATA_HOME": str(xdg) if use_xdg else "relative-base",
            "PYTHONPATH": str(tmp_path / "hostile-imports"),
            "PYTHONHOME": str(tmp_path / "hostile-runtime"),
            "PYTHONNOUSERSITE": "0",
            "PYTHONSAFEPATH": "0",
            "MARKER": str(marker),
        }
    )

    result = subprocess.run(
        ["/bin/sh", str(PLUGIN_ROOT / "scripts/run_hook.sh"), "prewarm"],
        cwd=project,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == ["unset", "unset", "1", "1"]
    assert not trap_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows hook wrapper contract")
def test_windows_hook_rejects_relative_localappdata_project_trap(tmp_path: Path) -> None:
    shell = shutil.which("powershell.exe") or shutil.which("powershell")
    if shell is None:
        pytest.skip("Windows PowerShell is unavailable")
    project = tmp_path / "project"
    trap = project / "relative-base" / "Lians" / "CodexMemory" / "venv" / "Scripts"
    trap.mkdir(parents=True)
    (trap / "python.exe").write_bytes(b"project-local executable trap")
    environ = dict(os.environ)
    environ.update(
        {
            "LOCALAPPDATA": "relative-base",
            "USERPROFILE": "relative-home",
            "PYTHONPATH": str(tmp_path / "hostile-imports"),
            "PYTHONHOME": str(tmp_path / "hostile-runtime"),
        }
    )

    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PLUGIN_ROOT / "scripts/run_hook.ps1"),
            "-Action",
            "prewarm",
        ],
        cwd=project,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_bundled_wheel_matches_provenance_and_metadata() -> None:
    provenance = _json("vendor/provenance.json")
    artifact = provenance["artifact"]
    wheels = list((PLUGIN_ROOT / "vendor").glob("lians_sdk-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    assert wheel.name == artifact["filename"]
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == artifact["sha256"]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode("utf-8")
    assert "Name: lians-sdk\n" in metadata
    assert f"Version: {artifact['sdk_version']}\n" in metadata
    assert "lians/mcp_server.py" in names
    assert "lians/memory_plugin_mcp.py" in names
    assert "lians-memory-mcp = lians.memory_plugin_mcp:main" in entry_points
    assert "lians_engine/lians/bge_onnx.py" in names
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)


def test_plugin_runtime_is_synced_to_the_validated_integration() -> None:
    for name in ("user_prompt_submit_recall.py", "local_recall_daemon.py"):
        assert (PLUGIN_ROOT / "runtime" / name).read_bytes() == (
            REPO_ROOT / "integrations/codex" / name
        ).read_bytes()


def test_setup_wrappers_require_uv_managed_python() -> None:
    for name in ("setup.ps1", "doctor.ps1", "setup.sh", "doctor.sh"):
        text = (PLUGIN_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "--managed-python" in text
        assert "--no-project" in text
        assert "--python 3.11" in text
        assert "python -I -B" in text

    skill = (PLUGIN_ROOT / "skills/lians-memory/SKILL.md").read_text(encoding="utf-8")
    assert skill.count("python -I -B") >= 2


def test_plugin_contains_no_secret_values() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".whl", ".pyc"}
    )
    assert re.search(r"\blians_[A-Za-z0-9]{20,}\b", text) is None
    assert re.search(r"\bsk-[A-Za-z0-9]{20,}\b", text) is None
    assert re.search(r"\bBearer [A-Za-z0-9._~+/=-]{20,}\b", text) is None
