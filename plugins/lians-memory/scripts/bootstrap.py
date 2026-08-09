#!/usr/bin/env python3
"""Portable, secret-free bootstrap helpers for the Lians Codex plugin.

This module intentionally uses only the Python standard library.  Codex can
therefore run the launcher before the plugin's frozen runtime has been synced.
The Lians SDK itself must come from the wheel bundled under ``vendor/``; public
package-index fallback is deliberately unsupported.
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from urllib.parse import urlsplit

PROFILE_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 1
LAUNCHER_RECORD_SCHEMA_VERSION = 1
DEFAULT_MANAGED_URL = ""
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PLUGIN_ROOT / "vendor"
RUNTIME_DIR = PLUGIN_ROOT / "runtime"
PROFILE_FILENAME = "profile.json"
LOCAL_MASTER_KEY_FILENAME = "master.key"
LAUNCHER_COMMAND = "lians-memory-mcp"
LAUNCHER_RECORD_FILENAME = "launcher.json"
DAEMON_TOKEN_FILENAME = "user.token"

# Local mode is a plugin-owned, no-egress runtime. ``None`` means remove an
# inherited value; strings are forced even when the parent Codex process set a
# conflicting value. Keep this one explicit policy synchronized with the SDK
# launcher copy used by the installed MCP command.
_LOCAL_RUNTIME_SECURITY_ENV: tuple[tuple[str, str | None], ...] = (
    # Explicit empty remote values prevent the hook's legacy Codex-config
    # fallback from rehydrating an older remote backend.
    ("LIANS_URL", ""),
    ("LIANS_API_KEY", ""),
    ("DEPLOYMENT_ENVIRONMENT", "development"),
    ("KMS_PROVIDER", "env"),
    ("MASTER_KEY_ID", None),
    ("MASTER_KEY_PREVIOUS_ID", None),
    ("MASTER_ENCRYPTION_KEY_PREVIOUS", None),
    ("SUBJECT_REFERENCE_KEY", None),
    ("KMS_AWS_KEY_ID", None),
    ("KMS_AWS_REGION", None),
    ("KMS_AWS_ENCRYPTED_KEY", None),
    ("KMS_AWS_PREVIOUS_KEY_ID", None),
    ("KMS_AWS_PREVIOUS_REGION", None),
    ("KMS_AWS_PREVIOUS_ENCRYPTED_KEY", None),
    ("KMS_AZURE_VAULT_URL", None),
    ("KMS_AZURE_SECRET_NAME", None),
    ("KMS_AZURE_PREVIOUS_VAULT_URL", None),
    ("KMS_AZURE_PREVIOUS_SECRET_NAME", None),
    ("KMS_VAULT_ADDR", None),
    ("KMS_VAULT_TOKEN", None),
    ("KMS_VAULT_PATH", None),
    ("KMS_VAULT_MOUNT_POINT", None),
    ("KMS_VAULT_PREVIOUS_ADDR", None),
    ("KMS_VAULT_PREVIOUS_PATH", None),
    ("KMS_VAULT_PREVIOUS_MOUNT_POINT", None),
    ("RECEIPT_SIGNING_PROVIDER", "local"),
    ("RECEIPT_SIGNING_PRIVATE_KEY", None),
    ("RECEIPT_SIGNING_KEY_ID", None),
    ("RECEIPT_VAULT_ADDR", None),
    ("RECEIPT_VAULT_TOKEN", None),
    ("RECEIPT_VAULT_TOKEN_FILE", None),
    ("RECEIPT_VAULT_NAMESPACE", None),
    ("RECEIPT_VAULT_MOUNT_POINT", None),
    ("RECEIPT_VAULT_KEY_NAME", None),
    ("RECEIPT_VAULT_KEY_VERSION", None),
    ("RECEIPT_VAULT_PUBLIC_KEY", None),
    ("RECEIPT_VAULT_TIMEOUT_SECONDS", None),
    ("EMBEDDING_PROVIDER", "bge-onnx"),
    ("EMBEDDING_DIM", "1024"),
    ("VOYAGE_API_KEY", None),
    ("VOYAGE_API_KEY_PATH", None),
    ("OPENAI_API_KEY", None),
    ("OPENAI_ADMIN_KEY", None),
    ("OPENAI_BASE_URL", None),
    ("OPENAI_CUSTOM_HEADERS", None),
    ("OPENAI_ORG_ID", None),
    ("OPENAI_ORGANIZATION", None),
    ("OPENAI_PROJECT", None),
    ("OPENAI_PROJECT_ID", None),
    ("ANTHROPIC_API_KEY", None),
    ("ANTHROPIC_AUTH_TOKEN", None),
    ("ANTHROPIC_BASE_URL", None),
    ("ANTHROPIC_CUSTOM_HEADERS", None),
    ("SUPERSESSION_LLM_STAGE", "false"),
    ("LLM_ADJUDICATION_ASYNC", "false"),
    ("AUTO_METADATA_ENABLED", "false"),
    ("AUTO_METADATA_LLM", "false"),
    ("GRAPH_EXTRACT_LLM", "false"),
    ("LIANS_DISTILL_MODEL", None),
    ("RECALL_RERANKER_MODEL", None),
    ("RECALL_RERANKER_ONNX_MODEL", None),
    ("RECALL_RERANKER_ONNX_TOKENIZER", None),
    ("RECALL_CACHE_ENABLED", "false"),
    ("RUNTIME_CACHE_ENABLED", "false"),
    ("REDIS_URL", None),
    ("AIRGAP_MODE", "true"),
    ("SIEM_URL", None),
    ("SIEM_TOKEN", None),
    ("OTEL_EXPORTER_OTLP_ENDPOINT", None),
    ("OTEL_EXPORTER_OTLP_HEADERS", None),
    ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None),
    ("OTEL_EXPORTER_OTLP_TRACES_HEADERS", None),
    ("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", None),
    ("OTEL_EXPORTER_OTLP_METRICS_HEADERS", None),
    ("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", None),
    ("OTEL_EXPORTER_OTLP_LOGS_HEADERS", None),
    ("OTEL_SDK_DISABLED", "true"),
    ("STRIPE_API_KEY", None),
    ("STRIPE_METER_WORKER_ENABLED", "false"),
    ("LEGACY_WEBHOOKS_ENABLED", "false"),
    ("INTEGRATION_WORKER_ENABLED", "false"),
    ("HF_ENDPOINT", None),
    ("HF_TOKEN", None),
    ("HUGGING_FACE_HUB_TOKEN", None),
    ("HF_HUB_OFFLINE", "1"),
    ("TRANSFORMERS_OFFLINE", "1"),
    ("HF_DATASETS_OFFLINE", "1"),
)

# The model stays outside the plugin.  Local users may explicitly download the
# pinned files into their own writable plugin-data directory, or provide an
# already-downloaded source directory.  These are the same reviewed artifacts
# used by the measured BGE ONNX integration.
BGE_REVISION = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
BGE_MODEL_SHA256 = "69ed3f810d3b6d13f70dff9ca89966f39c0a0e877fb88211be7bcc070df2a2ce"
BGE_TOKENIZER_SHA256 = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
BGE_MODEL_URL = (
    f"https://huggingface.co/BAAI/bge-large-en-v1.5/resolve/{BGE_REVISION}/onnx/model.onnx"
)
BGE_TOKENIZER_URL = (
    f"https://huggingface.co/BAAI/bge-large-en-v1.5/resolve/{BGE_REVISION}/tokenizer.json"
)

_WHEEL_NAME = re.compile(r"^lians_sdk-(?P<version>.+)-py3-none-any\.whl$")


class BootstrapError(RuntimeError):
    """A safe, actionable setup error."""


def validated_managed_url(value: object) -> str:
    """Return a normalized non-secret HTTPS service origin/base path."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise BootstrapError("managed plugin profile requires an explicit HTTPS URL")
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        raise BootstrapError("managed plugin profile requires an explicit HTTPS URL")
    try:
        parsed = urlsplit(value)
        # Accessing ``port`` forces urllib to reject malformed/out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        raise BootstrapError("managed plugin profile requires an explicit HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapError("managed plugin profile requires an explicit HTTPS URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class WheelArtifact:
    path: Path
    version: str
    sha256: str
    source_commit: str
    source_tree_sha256: str


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & reparse_flag)


def _require_regular_file(path: Path, description: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as exc:
        raise BootstrapError(f"{description} is missing") from exc
    except OSError as exc:
        raise BootstrapError(f"{description} is unavailable") from exc
    if _is_symlink_or_reparse(path) or not stat.S_ISREG(details.st_mode):
        raise BootstrapError(f"{description} must be a regular non-reparse file")


def _require_private_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise BootstrapError(f"private-state directory is unavailable: {path}") from exc
    if _is_symlink_or_reparse(path) or not stat.S_ISDIR(details.st_mode):
        raise BootstrapError(
            f"private-state directory must not be a symlink or reparse point: {path}"
        )


def _validated_data_home_path(value: str | Path) -> Path:
    """Canonicalize a data home only after rejecting redirects in its path."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.abspath(candidate))
    anchor = Path(candidate.anchor)
    current = anchor
    relative_parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for part in relative_parts:
        current /= part
        if not os.path.lexists(current):
            break
        if _is_symlink_or_reparse(current):
            raise BootstrapError(
                "LIANS_MEMORY_HOME must not contain a symlink or reparse-point redirect"
            )
        if current != candidate:
            try:
                if not stat.S_ISDIR(current.lstat().st_mode):
                    raise BootstrapError("LIANS_MEMORY_HOME has a non-directory path component")
            except OSError as exc:
                raise BootstrapError("LIANS_MEMORY_HOME path is unavailable") from exc
    return candidate.resolve()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def native_data_home(
    environ: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return a user-writable fallback without touching ``~/.codex``."""

    values = os.environ if environ is None else environ
    if values.get("LIANS_MEMORY_HOME", "").strip():
        raise BootstrapError(
            "LIANS_MEMORY_HOME overrides are not supported; use the native per-user data home"
        )

    platform_name = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else home
    if platform_name == "win32":
        base = values.get("LOCALAPPDATA", "").strip()
        configured = Path(base) if base else None
        if configured is not None and configured.is_absolute():
            root = configured
        else:
            if not user_home.is_absolute():
                raise BootstrapError("the native user home must be an absolute path")
            root = user_home / "AppData" / "Local"
        return _validated_data_home_path(root / "Lians" / "CodexMemory")
    if platform_name == "darwin":
        if not user_home.is_absolute():
            raise BootstrapError("the native user home must be an absolute path")
        return _validated_data_home_path(
            user_home / "Library" / "Application Support" / "Lians" / "CodexMemory"
        )
    base = values.get("XDG_DATA_HOME", "").strip()
    configured = Path(base) if base else None
    if configured is not None and configured.is_absolute():
        root = configured
    else:
        if not user_home.is_absolute():
            raise BootstrapError("the native user home must be an absolute path")
        root = user_home / ".local" / "share"
    return _validated_data_home_path(root / "lians" / "codex-memory")


def resolve_data_home(
    environ: Mapping[str, str] | None = None,
    *,
    explicit: str | Path | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    if explicit:
        raise BootstrapError(
            "custom data directories are not supported; use the native per-user data home"
        )
    if values.get("LIANS_MEMORY_HOME", "").strip():
        raise BootstrapError(
            "LIANS_MEMORY_HOME overrides are not supported; use the native per-user data home"
        )
    # The MCP launcher is a bare command and cannot rely on plugin-cache
    # interpolation or PLUGIN_DATA being inherited by the host.  One native
    # location keeps setup, hooks, and MCP on the same profile across restarts.
    return native_data_home(values)


def project_scope(project_root: str | Path) -> str:
    root = Path(project_root).expanduser().resolve()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip("-_.").lower()
    digest = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:12]
    return f"{(slug[:40] or 'project')}-{digest}"


def project_data_dir(data_home: Path, project_root: str | Path) -> Path:
    return data_home / "projects" / project_scope(project_root)


def profile_path(data_home: Path) -> Path:
    return data_home / PROFILE_FILENAME


def runtime_python(data_home: Path, *, platform: str | None = None) -> Path:
    platform_name = sys.platform if platform is None else platform
    relative = Path("Scripts/python.exe") if platform_name == "win32" else Path("bin/python")
    return data_home / "venv" / relative


def runtime_launcher(data_home: Path, *, platform: str | None = None) -> Path:
    platform_name = sys.platform if platform is None else platform
    relative = (
        Path("Scripts/lians-memory-mcp.exe")
        if platform_name == "win32"
        else Path("bin/lians-memory-mcp")
    )
    return data_home / "venv" / relative


def read_profile(data_home: Path) -> dict[str, object]:
    path = profile_path(data_home)
    if not os.path.lexists(path):
        raise BootstrapError("Lians Memory is not set up; run `lians_plugin.py setup` first")
    _require_regular_file(path, "plugin profile")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"invalid plugin profile: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise BootstrapError(f"unsupported plugin profile: {path}")
    mode = value.get("mode")
    if mode not in {"local", "managed"}:
        raise BootstrapError(f"invalid plugin mode in {path}")
    if mode == "managed":
        try:
            validated_managed_url(value.get("managed_url"))
        except BootstrapError as exc:
            raise BootstrapError(
                f"managed plugin profile requires an explicit HTTPS URL: {path}"
            ) from exc
    return value


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise BootstrapError("bundled SDK wheel must contain exactly one METADATA file")
            raw = archive.read(names[0]).decode("utf-8")
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError) as exc:
        raise BootstrapError(f"invalid bundled SDK wheel: {path.name}") from exc
    metadata = Parser().parsestr(raw)
    return metadata.get("Name", ""), metadata.get("Version", "")


def _load_provenance(vendor_dir: Path) -> dict[str, object]:
    path = vendor_dir / "provenance.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BootstrapError(
            "vendor/provenance.json is required; refusing an unverified SDK"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("vendor/provenance.json is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        raise BootstrapError("unsupported vendor provenance schema")
    artifact = value.get("artifact")
    if not isinstance(artifact, dict):
        raise BootstrapError("vendor provenance is missing artifact metadata")
    return artifact


def discover_wheel(vendor_dir: Path = VENDOR_DIR) -> WheelArtifact:
    wheels = sorted(vendor_dir.glob("lians_sdk-*.whl"))
    if len(wheels) != 1:
        raise BootstrapError(
            f"expected exactly one bundled lians_sdk wheel in {vendor_dir}; found {len(wheels)}"
        )
    path = wheels[0]
    if not _WHEEL_NAME.fullmatch(path.name):
        raise BootstrapError(f"unsupported SDK wheel filename: {path.name}")
    artifact = _load_provenance(vendor_dir)
    required = ("filename", "sha256", "sdk_version", "source_commit", "source_tree_sha256")
    if any(not isinstance(artifact.get(key), str) or not artifact[key] for key in required):
        raise BootstrapError("vendor provenance is incomplete")
    if artifact["filename"] != path.name:
        raise BootstrapError("vendor provenance does not name the bundled SDK wheel")
    expected_hash = str(artifact["sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise BootstrapError("vendor provenance contains an invalid SHA-256")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise BootstrapError("bundled SDK wheel SHA-256 does not match provenance")
    package_name, version = _wheel_metadata(path)
    if package_name.lower().replace("_", "-") != "lians-sdk":
        raise BootstrapError("bundled wheel is not lians-sdk")
    if version != artifact["sdk_version"]:
        raise BootstrapError("wheel METADATA version does not match provenance")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(artifact["source_commit"])):
        raise BootstrapError("vendor provenance contains an invalid source commit")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(artifact["source_tree_sha256"])):
        raise BootstrapError("vendor provenance contains an invalid source-tree SHA-256")
    return WheelArtifact(
        path=path,
        version=version,
        sha256=actual_hash,
        source_commit=str(artifact["source_commit"]),
        source_tree_sha256=str(artifact["source_tree_sha256"]),
    )


def validate_frozen_runtime(
    runtime_dir: Path = RUNTIME_DIR, wheel: WheelArtifact | None = None
) -> str:
    pyproject = runtime_dir / "pyproject.toml"
    lock = runtime_dir / "uv.lock"
    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BootstrapError("runtime/pyproject.toml is missing") from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise BootstrapError("runtime/pyproject.toml is invalid") from exc
    if not lock.is_file():
        raise BootstrapError("runtime/uv.lock is missing")
    dependencies = document.get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list):
        raise BootstrapError("runtime dependencies must be a list")
    lians_entries = [
        str(item) for item in dependencies if str(item).lower().startswith("lians-sdk")
    ]
    sources = document.get("tool", {}).get("uv", {}).get("sources", {})
    lians_source = sources.get("lians-sdk", {}) if isinstance(sources, dict) else {}
    source_path = lians_source.get("path") if isinstance(lians_source, dict) else None
    if len(lians_entries) != 1 or not isinstance(source_path, str):
        raise BootstrapError("runtime must reference exactly one bundled lians-sdk wheel")
    if wheel is not None and Path(source_path).name != wheel.path.name:
        raise BootstrapError("runtime does not reference the provenance-verified SDK wheel")
    lock_text = lock.read_text(encoding="utf-8", errors="strict")
    if wheel is not None and wheel.sha256 not in lock_text:
        raise BootstrapError("runtime lock does not contain the bundled SDK wheel hash")
    return sha256_file(lock)


def _absolute_path_entries(
    value: str,
    *,
    cwd: Path | None = None,
) -> list[Path]:
    working_directory = (Path.cwd() if cwd is None else cwd).resolve()
    entries: list[Path] = []
    for raw in value.split(os.pathsep):
        item = raw.strip().strip('"')
        if not item:
            continue
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(working_directory)
        except ValueError:
            pass
        else:
            continue
        entries.append(resolved)
    return entries


def _executable_from_explicit_path(
    command: str,
    environ: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
) -> str | None:
    values = os.environ if environ is None else environ
    platform_name = sys.platform if platform is None else platform
    filenames = [f"{command}.exe", command] if platform_name == "win32" else [command]
    for directory in _absolute_path_entries(values.get("PATH", ""), cwd=cwd):
        for filename in filenames:
            candidate = directory / filename
            try:
                if not candidate.is_file():
                    continue
                if platform_name != "win32" and not os.access(candidate, os.X_OK):
                    continue
                return str(candidate.resolve())
            except OSError:
                continue
    return None


def find_uv(
    environ: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    cwd: Path | None = None,
) -> str:
    executable = _executable_from_explicit_path("uv", environ, platform=platform, cwd=cwd)
    if not executable:
        raise BootstrapError(
            "uv is required. Install it from https://docs.astral.sh/uv/ and rerun setup"
        )
    return executable


def _windows_system_directory() -> Path:
    if sys.platform != "win32":
        raise BootstrapError("Windows system tools are unavailable on this platform")
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError, ValueError) as exc:
        raise BootstrapError("could not resolve the trusted Windows system directory") from exc
    if not length or length >= len(buffer):
        raise BootstrapError("could not resolve the trusted Windows system directory")
    path = Path(buffer.value)
    if not path.is_absolute():
        raise BootstrapError("could not resolve the trusted Windows system directory")
    return path


def _trusted_windows_tool(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    if name not in {"icacls", "powershell", "whoami"}:
        raise BootstrapError("unsupported Windows system tool")
    system_directory = _windows_system_directory()
    fixed = (
        system_directory / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if name == "powershell"
        else system_directory / f"{name}.exe"
    )
    try:
        _require_regular_file(fixed, f"trusted Windows {name} executable")
        return str(fixed)
    except BootstrapError:
        fallback = _executable_from_explicit_path(
            name,
            environ,
            platform="win32",
        )
        if fallback:
            return fallback
        raise BootstrapError(f"trusted Windows {name} is unavailable")


def _windows_user_sid() -> str:
    whoami = _trusted_windows_tool("whoami")
    result = subprocess.run(
        [whoami, "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        row = next(csv.reader([result.stdout.strip()]))
        sid = row[1].strip()
    except (IndexError, StopIteration, csv.Error) as exc:
        raise BootstrapError("could not identify the Windows account for private state") from exc
    if result.returncode or not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid, re.IGNORECASE):
        raise BootstrapError("could not identify the Windows account for private state")
    return sid


def _windows_acl_snapshot(path: Path) -> dict[str, object]:
    powershell = _trusted_windows_tool("powershell")
    script = (
        "$ErrorActionPreference='Stop';"
        "$target=[Environment]::GetEnvironmentVariable('LIANS_BOOTSTRAP_ACL_PATH','Process');"
        "$acl=Get-Acl -LiteralPath $target;"
        "$rules=@($acl.GetAccessRules($true,$true,"
        "[System.Security.Principal.SecurityIdentifier])|ForEach-Object{"
        "[pscustomobject]@{sid=$_.IdentityReference.Value;"
        "access=$_.AccessControlType.ToString();rights=$_.FileSystemRights.ToString();"
        "inherited=[bool]$_.IsInherited;inheritance=$_.InheritanceFlags.ToString()}});"
        "[pscustomobject]@{protected=[bool]$acl.AreAccessRulesProtected;"
        "current_sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "owner_sid=$acl.GetOwner("
        "[System.Security.Principal.SecurityIdentifier]).Value;"
        "rules=$rules}|ConvertTo-Json -Depth 4 -Compress"
    )
    child_env = dict(os.environ)
    child_env["LIANS_BOOTSTRAP_ACL_PATH"] = str(path)
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        env=child_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        snapshot = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise BootstrapError("could not inspect Windows private-state permissions") from exc
    if result.returncode or not isinstance(snapshot, dict):
        raise BootstrapError("could not inspect Windows private-state permissions")
    return snapshot


def _windows_acl_snapshot_is_private(
    snapshot: Mapping[str, object],
    *,
    is_directory: bool,
    allow_secure_inheritance: bool = False,
) -> bool:
    current_sid = snapshot.get("current_sid")
    owner_sid = snapshot.get("owner_sid")
    raw_rules = snapshot.get("rules")
    if (
        (not snapshot.get("protected") and not allow_secure_inheritance)
        or not isinstance(current_sid, str)
        or not isinstance(owner_sid, str)
        or owner_sid.upper() != current_sid.upper()
    ):
        return False
    if isinstance(raw_rules, dict):
        rules: list[object] = [raw_rules]
    elif isinstance(raw_rules, list):
        rules = raw_rules
    else:
        return False
    allowed = {current_sid.upper(), "S-1-5-18", "S-1-5-32-544"}
    seen: set[str] = set()
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            return False
        sid = str(raw_rule.get("sid", "")).upper()
        rights = str(raw_rule.get("rights", ""))
        inheritance = str(raw_rule.get("inheritance", ""))
        if (
            sid not in allowed
            or raw_rule.get("access") != "Allow"
            or (raw_rule.get("inherited") is not False and not allow_secure_inheritance)
            or "FullControl" not in rights
        ):
            return False
        if is_directory and not {"ContainerInherit", "ObjectInherit"}.issubset(
            {part.strip() for part in inheritance.split(",")}
        ):
            return False
        seen.add(sid)
    return seen == allowed


def _private_path_permissions_ok(
    path: Path,
    *,
    is_directory: bool,
    platform: str | None = None,
    allow_secure_inheritance: bool = False,
) -> bool:
    platform_name = sys.platform if platform is None else platform
    try:
        details = path.lstat()
        if _is_symlink_or_reparse(path):
            return False
        if is_directory and not stat.S_ISDIR(details.st_mode):
            return False
        if not is_directory and not stat.S_ISREG(details.st_mode):
            return False
        if platform_name == "win32":
            return _windows_acl_snapshot_is_private(
                _windows_acl_snapshot(path),
                is_directory=is_directory,
                allow_secure_inheritance=allow_secure_inheritance,
            )
        expected = 0o700 if is_directory else 0o600
        if stat.S_IMODE(details.st_mode) != expected:
            return False
        getuid = getattr(os, "getuid", None)
        return getuid is None or details.st_uid == getuid()
    except (BootstrapError, OSError):
        return False


def _restrict_private_path(
    path: Path,
    *,
    is_directory: bool,
    platform: str | None = None,
    verify: bool = True,
) -> None:
    platform_name = sys.platform if platform is None else platform
    if is_directory:
        _require_private_directory(path)
    else:
        _require_regular_file(path, "private-state file")
    if platform_name != "win32":
        path.chmod(0o700 if is_directory else 0o600)
        if verify and not _private_path_permissions_ok(
            path, is_directory=is_directory, platform=platform_name
        ):
            raise BootstrapError("could not apply owner-only permissions to private state")
        return

    icacls = _trusted_windows_tool("icacls")
    user_sid = _windows_user_sid()
    # /reset first removes every unrelated explicit ACE. Removing inheritance
    # alone is insufficient for an existing or restored file with a broad DACL.
    reset = subprocess.run(
        [icacls, str(path), "/reset", "/Q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    ownership = subprocess.run(
        [icacls, str(path), "/setowner", f"*{user_sid}", "/Q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    permission = "(OI)(CI)(F)" if is_directory else "(F)"
    protected = subprocess.run(
        [
            icacls,
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{user_sid}:{permission}",
            f"*S-1-5-18:{permission}",
            f"*S-1-5-32-544:{permission}",
            "/Q",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if reset.returncode or ownership.returncode or protected.returncode:
        raise BootstrapError("could not apply an owner-only Windows ACL to private state")
    if verify and not _private_path_permissions_ok(
        path, is_directory=is_directory, platform=platform_name
    ):
        raise BootstrapError("Windows private-state ACL verification failed")


def _safe_directory(path: Path) -> None:
    if os.path.lexists(path):
        _require_private_directory(path)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        _require_private_directory(path)
    # Runtime paths are re-canonicalized idempotently. On Windows the explicit
    # inheritable DACL keeps subsequently created SQLite and daemon token files
    # private without depending on a possibly shared LIANS_MEMORY_HOME parent.
    _restrict_private_path(path, is_directory=True, verify=False)


def _runtime_directory(path: Path, *, repair_permissions: bool) -> None:
    """Prepare a runtime directory without reapplying its ACL on every prompt.

    Setup, doctor, and SessionStart remain responsible for the full owner/DACL
    contract.  A warm UserPromptSubmit process only needs to reject path swaps
    (symlinks, reparses, or a non-directory) before using those already-private
    paths.  Newly encountered project paths still take the full secure-creation
    path once.
    """

    if repair_permissions or not os.path.lexists(path):
        _safe_directory(path)
        return
    _require_private_directory(path)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    _safe_directory(path.parent)
    if os.path.lexists(path):
        _require_regular_file(path, f"{path.name} state file")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temp.chmod(0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _restrict_private_file(path: Path) -> None:
    _require_regular_file(path, "private-state file")
    _restrict_private_path(path, is_directory=False)


def _decode_local_master_key(path: Path) -> str:
    _require_regular_file(path, "local encryption key")
    try:
        encoded = path.read_text(encoding="ascii").strip()
        decoded = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, binascii.Error, ValueError) as exc:
        raise BootstrapError(
            "local encryption key is invalid; restore it before using existing memory"
        ) from exc
    if len(decoded) != 32:
        raise BootstrapError(
            "local encryption key is invalid; restore it before using existing memory"
        )
    return encoded


def _existing_local_memory_state(data_home: Path) -> bool:
    projects = data_home / "projects"
    if not os.path.lexists(projects):
        return False
    try:
        _require_private_directory(projects)
    except BootstrapError:
        return True
    pending = [projects]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return True
        for entry in entries:
            path = Path(entry.path)
            if _is_symlink_or_reparse(path):
                return True
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                return True
            name = entry.name.lower()
            if is_file and (
                name == "memory.sqlite3"
                or name.startswith("memory.sqlite3-")
                or name.endswith((".sqlite", ".sqlite3", ".db"))
            ):
                return True
    return False


def ensure_local_master_key(data_home: Path) -> Path:
    """Create one stable install-local key without ever returning or printing it."""

    _safe_directory(data_home)
    path = data_home / LOCAL_MASTER_KEY_FILENAME
    if not os.path.lexists(path):
        if _existing_local_memory_state(data_home):
            raise BootstrapError(
                "local memory state exists but master.key is missing; restore the original key "
                "or move the existing project memory state aside before rerunning setup"
            )
        encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
    _decode_local_master_key(path)
    _restrict_private_file(path)
    return path


def _existing_daemon_token_is_private(daemon_dir: Path) -> bool:
    token = daemon_dir / DAEMON_TOKEN_FILENAME
    if not os.path.lexists(token):
        return True
    try:
        _require_regular_file(token, "daemon authentication token")
    except BootstrapError:
        return False
    return _private_path_permissions_ok(token, is_directory=False, allow_secure_inheritance=True)


def _preflight_existing_daemon_token(
    data_home: Path,
    project_dir: Path,
) -> tuple[bool, bool]:
    """Snapshot token safety before ancestor ACL normalization can propagate."""

    daemon_dir = project_dir / "daemon"
    for directory in (data_home, data_home / "projects", project_dir, daemon_dir):
        if not os.path.lexists(directory):
            return False, True
        _require_private_directory(directory)
    token = daemon_dir / DAEMON_TOKEN_FILENAME
    if not os.path.lexists(token):
        return False, True
    return True, _existing_daemon_token_is_private(daemon_dir)


def _require_existing_daemon_token_private(daemon_dir: Path) -> None:
    if _existing_daemon_token_is_private(daemon_dir):
        return
    raise BootstrapError(
        "existing daemon authentication token is not a private regular file; "
        "stop the Lians daemon, remove user.token, and rerun setup to rotate it"
    )


def _run(command: Sequence[str], *, environ: Mapping[str, str]) -> None:
    completed = subprocess.run(list(command), env=dict(environ), check=False)
    if completed.returncode:
        raise BootstrapError(f"command failed with exit code {completed.returncode}: {command[0]}")


def _scrubbed_setup_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Keep process/bootstrap necessities without forwarding model/API secrets."""

    values = os.environ if environ is None else environ
    allowed = {
        "APPDATA",
        "COMSPEC",
        "CURL_CA_BUNDLE",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
    child = {key: value for key, value in values.items() if key.upper() in allowed}
    for key in list(child):
        if key.upper() == "PATH":
            child[key] = os.pathsep.join(str(path) for path in _absolute_path_entries(child[key]))
    return child


def _installed_sdk_matches_bundle(
    data_home: Path,
    wheel: WheelArtifact,
    *,
    platform: str | None = None,
) -> bool:
    """Verify installed provenance and every hashed SDK RECORD entry."""

    platform_name = sys.platform if platform is None else platform
    venv = data_home / "venv"
    site_packages = (
        venv / "Lib" / "site-packages"
        if platform_name == "win32"
        else venv / "lib" / "python3.11" / "site-packages"
    )
    try:
        _require_private_directory(venv)
        _require_private_directory(site_packages)
        distributions = list(site_packages.glob("lians_sdk-*.dist-info"))
        if len(distributions) != 1:
            return False
        distribution = distributions[0]
        _require_private_directory(distribution)
        metadata_path = distribution / "METADATA"
        direct_url_path = distribution / "direct_url.json"
        record_path = distribution / "RECORD"
        for path in (metadata_path, direct_url_path, record_path):
            _require_regular_file(path, "installed SDK metadata")

        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        if (
            metadata.get("Name", "").lower().replace("_", "-") != "lians-sdk"
            or metadata.get("Version") != wheel.version
        ):
            return False

        direct_url = json.loads(direct_url_path.read_text(encoding="utf-8"))
        if not isinstance(direct_url, dict) or not isinstance(direct_url.get("url"), str):
            return False
        source = urlsplit(direct_url["url"])
        if (
            source.scheme.lower() != "file"
            or source.netloc not in {"", "localhost"}
            or source.query
            or source.fragment
        ):
            return False
        installed_from = Path(urllib.request.url2pathname(source.path)).resolve()
        if installed_from != wheel.path.resolve():
            return False

        venv_root = venv.resolve()
        hashed_package_files = 0
        with record_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return False
        for row in rows:
            if len(row) != 3 or not row[1]:
                continue
            algorithm, separator, expected = row[1].partition("=")
            if algorithm != "sha256" or not separator or not expected:
                return False
            installed_path = (site_packages / Path(row[0])).resolve()
            try:
                installed_path.relative_to(venv_root)
            except ValueError:
                return False
            _require_regular_file(installed_path, "installed SDK file")
            actual = (
                base64.urlsafe_b64encode(bytes.fromhex(sha256_file(installed_path)))
                .rstrip(b"=")
                .decode("ascii")
            )
            if actual != expected or (row[2] and installed_path.stat().st_size != int(row[2])):
                return False
            try:
                relative = installed_path.relative_to(site_packages.resolve())
            except ValueError:
                relative = Path()
            if relative.parts and relative.parts[0] == "lians":
                hashed_package_files += 1
        return hashed_package_files > 0
    except (
        BootstrapError,
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        csv.Error,
    ):
        return False


def sync_runtime(data_home: Path, wheel: WheelArtifact, runtime_dir: Path = RUNTIME_DIR) -> str:
    lock_hash = validate_frozen_runtime(runtime_dir, wheel)
    _safe_directory(data_home)
    _safe_directory(data_home / "cache" / "uv")
    child_env = _scrubbed_setup_environment()
    child_env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(data_home / "venv"),
            "UV_CACHE_DIR": str(data_home / "cache" / "uv"),
            "UV_NO_CONFIG": "1",
        }
    )
    _run(
        [
            find_uv(),
            "sync",
            "--managed-python",
            "--python",
            "3.11",
            "--project",
            str(runtime_dir),
            "--frozen",
            "--no-dev",
            "--reinstall-package",
            "lians-sdk",
        ],
        environ=child_env,
    )
    python = runtime_python(data_home)
    if not python.is_file():
        raise BootstrapError(f"uv completed but the runtime interpreter is missing: {python}")
    if not _installed_sdk_matches_bundle(data_home, wheel):
        raise BootstrapError(
            "the installed Lians SDK does not match the provenance-verified bundled wheel"
        )
    return lock_hash


def uv_tool_bin_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return uv's cross-platform executable directory without changing PATH."""

    values = os.environ if environ is None else environ
    child_env = _scrubbed_setup_environment(values)
    child_env["UV_NO_CONFIG"] = "1"
    completed = subprocess.run(
        [find_uv(values), "tool", "dir", "--bin"],
        env=child_env,
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode or not lines:
        raise BootstrapError("could not determine uv's tool executable directory")
    return Path(lines[-1]).expanduser().resolve()


def _launcher_record_path(data_home: Path) -> Path:
    return data_home / LAUNCHER_RECORD_FILENAME


def _read_launcher_record(data_home: Path) -> dict[str, object] | None:
    path = _launcher_record_path(data_home)
    if not os.path.lexists(path):
        return None
    try:
        _require_regular_file(path, "launcher ownership record")
        record = json.loads(path.read_text(encoding="utf-8"))
    except (BootstrapError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != LAUNCHER_RECORD_SCHEMA_VERSION
    ):
        return None
    return record


def _record_owns_launcher(data_home: Path, destination: Path) -> bool:
    record = _read_launcher_record(data_home)
    if record is None or record.get("path") != str(destination):
        return False
    expected = record.get("sha256")
    try:
        _require_regular_file(destination, "MCP launcher")
    except BootstrapError:
        return False
    return isinstance(expected, str) and sha256_file(destination) == expected


def _owned_launcher_still_works(
    destination: Path,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Confirm a locked Windows console shim reaches the refreshed runtime."""

    values = os.environ if environ is None else environ
    child = _scrubbed_setup_environment(values)
    child["PYTHONPATH"] = ""
    child["PYTHONHOME"] = ""
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    try:
        result = subprocess.run(
            [str(destination), "--check"],
            env=child,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        document = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError):
        return False
    return result.returncode == 0 and isinstance(document, dict) and document.get("ok") is True


def install_launcher(
    data_home: Path,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    tool_bin: Path | None = None,
) -> Path:
    """Publish the frozen runtime's console shim on uv's executable path.

    Copying the generated console shim preserves its absolute frozen-venv
    interpreter reference.  It avoids a second dependency resolution and, on
    Windows, provides the real ``.exe`` required by process launchers.
    """

    platform_name = sys.platform if platform is None else platform
    source = runtime_launcher(data_home, platform=platform_name)
    try:
        _require_regular_file(source, "frozen runtime MCP launcher")
    except BootstrapError:
        raise BootstrapError(
            f"the frozen runtime does not provide {LAUNCHER_COMMAND}; "
            "the bundled SDK wheel must be rebuilt and setup rerun"
        )

    bin_dir = uv_tool_bin_dir(environ) if tool_bin is None else Path(tool_bin).resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{LAUNCHER_COMMAND}.exe" if platform_name == "win32" else LAUNCHER_COMMAND
    destination = bin_dir / filename
    source_hash = sha256_file(source)
    if os.path.lexists(destination):
        if _is_symlink_or_reparse(destination):
            raise BootstrapError(
                f"refusing to replace a symlink or reparse-point launcher: {destination}. "
                "Move it aside and rerun setup."
            )
        same_content = destination.is_file() and sha256_file(destination) == source_hash
        if not same_content and not _record_owns_launcher(data_home, destination):
            raise BootstrapError(
                f"refusing to replace an unowned launcher: {destination}. "
                "Move it aside and rerun setup."
            )

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{LAUNCHER_COMMAND}.", dir=bin_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    kept_owned_launcher = False
    try:
        shutil.copyfile(source, temporary)
        if platform_name != "win32":
            temporary.chmod(0o700)
        os.replace(temporary, destination)
    except OSError as exc:
        # Windows cannot replace an executable while Codex has it open. The
        # console shim contains only the stable interpreter/entry-point bridge;
        # the just-synced Python environment contains the actual SDK. Retain an
        # owned shim only when its live --check reaches that refreshed runtime.
        if (
            platform_name == "win32"
            and _record_owns_launcher(data_home, destination)
            and _owned_launcher_still_works(destination, environ)
        ):
            kept_owned_launcher = True
        else:
            raise BootstrapError(f"could not install the MCP launcher: {destination}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    if kept_owned_launcher:
        _require_regular_file(destination, "MCP launcher")

    _atomic_json(
        _launcher_record_path(data_home),
        {
            "schema_version": LAUNCHER_RECORD_SCHEMA_VERSION,
            "command": LAUNCHER_COMMAND,
            "path": str(destination),
            "sha256": sha256_file(destination),
            "runtime": str(source),
        },
    )
    return destination


def launcher_on_path(
    launcher: Path,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    discovered = shutil.which(LAUNCHER_COMMAND, path=values.get("PATH"))
    if not discovered:
        return False
    try:
        return Path(discovered).resolve() == launcher.resolve()
    except OSError:
        return False


def launcher_path_message(launcher: Path) -> str:
    return (
        f"{launcher.parent} is not visible on PATH. Run `uv tool update-shell`, "
        "then fully restart Codex."
    )


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return
    _safe_directory(destination.parent)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "lians-memory-plugin/0.1"})
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise BootstrapError(f"model download failed: {destination.name}") from exc
    if sha256_file(temporary) != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise BootstrapError(f"downloaded {destination.name} failed SHA-256 verification")
    os.replace(temporary, destination)


def _verify_bge_source(source: Path) -> tuple[Path, Path]:
    model = source / "model.onnx"
    tokenizer = source / "tokenizer.json"
    if not model.is_file() or not tokenizer.is_file():
        raise BootstrapError("BGE source must contain model.onnx and tokenizer.json")
    if sha256_file(model) != BGE_MODEL_SHA256:
        raise BootstrapError("BGE source model.onnx has the wrong SHA-256")
    if sha256_file(tokenizer) != BGE_TOKENIZER_SHA256:
        raise BootstrapError("BGE source tokenizer.json has the wrong SHA-256")
    return model, tokenizer


def stage_bge(
    data_home: Path,
    *,
    source: Path | None,
    download: bool,
) -> Path:
    downloads = data_home / "downloads" / f"bge-large-en-v1.5-{BGE_REVISION}"
    if source is None:
        if not download:
            raise BootstrapError(
                "local mode requires --download-bge or --bge-source; no model is bundled"
            )
        _download_verified(BGE_MODEL_URL, downloads / "model.onnx", BGE_MODEL_SHA256)
        _download_verified(BGE_TOKENIZER_URL, downloads / "tokenizer.json", BGE_TOKENIZER_SHA256)
        source = downloads
    model, tokenizer = _verify_bge_source(source.expanduser().resolve())
    output = data_home / "models" / "bge-large-en-v1.5-onnx"
    if output.exists():
        if validate_bge_artifact_directory(output):
            return output
        raise BootstrapError(
            f"existing BGE artifact directory is invalid: {output}. Move it aside and rerun setup."
        )
    _safe_directory(output.parent)
    python = runtime_python(data_home)
    child_env = dict(os.environ)
    _run(
        [
            str(python),
            "-m",
            "lians.bge_onnx_export",
            "--model",
            str(model),
            "--tokenizer",
            str(tokenizer),
            "--output",
            str(output),
        ],
        environ=child_env,
    )
    return output


def write_profile(
    data_home: Path,
    *,
    mode: str,
    wheel: WheelArtifact,
    lock_sha256: str,
    managed_url: str,
    bge_artifact_dir: Path | None,
) -> Path:
    if mode not in {"local", "managed"}:
        raise BootstrapError("mode must be local or managed")
    profile: dict[str, object] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "mode": mode,
        "sdk": {
            "artifact": wheel.path.name,
            "version": wheel.version,
            "sha256": wheel.sha256,
            "source_commit": wheel.source_commit,
            "source_tree_sha256": wheel.source_tree_sha256,
        },
        "runtime_lock_sha256": lock_sha256,
    }
    if mode == "managed":
        profile["managed_url"] = validated_managed_url(managed_url)
    else:
        if bge_artifact_dir is None:
            raise BootstrapError("local profile requires a verified BGE artifact directory")
        profile["embedding_provider"] = "bge-onnx"
        profile["bge_artifact_dir"] = str(bge_artifact_dir)
    path = profile_path(data_home)
    _atomic_json(path, profile)
    return path


def verify_profile_matches_bundle(
    profile: Mapping[str, object],
    *,
    plugin_root: Path = PLUGIN_ROOT,
) -> None:
    """Fail closed when an updated plugin has not been resynced yet."""

    wheel = discover_wheel(plugin_root / "vendor")
    lock_hash = validate_frozen_runtime(plugin_root / "runtime", wheel)
    sdk = profile.get("sdk")
    if not isinstance(sdk, dict):
        raise BootstrapError("plugin profile is missing SDK provenance; rerun setup")
    if sdk.get("version") != wheel.version or sdk.get("sha256") != wheel.sha256:
        raise BootstrapError("plugin SDK changed; rerun setup before use")
    if profile.get("runtime_lock_sha256") != lock_hash:
        raise BootstrapError("plugin runtime lock changed; rerun setup before use")


def _apply_local_runtime_security(child: dict[str, str]) -> None:
    for name, value in _LOCAL_RUNTIME_SECURITY_ENV:
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value


def configure_runtime_environment(
    data_home: Path,
    profile: Mapping[str, object],
    environ: Mapping[str, str],
    *,
    project_root: str | Path,
    require_managed_key: bool,
    repair_private_paths: bool = True,
) -> dict[str, str]:
    child = dict(environ)
    # The outer wrappers set these before Python starts. Reassert them for the
    # frozen runtime child so direct launcher paths cannot restore ambient
    # import roots or user-site packages.
    child["PYTHONPATH"] = ""
    child["PYTHONHOME"] = ""
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    root = Path(project_root).expanduser().resolve()
    child["LIANS_MCP_PROJECT_ROOT"] = str(root)
    child["LIANS_AGENT_ID"] = ""
    child["LIANS_NAMESPACE"] = ""
    project_subject_id = f"codex-project:{project_scope(root)}"
    child["LIANS_MCP_SUBJECT_ID"] = project_subject_id
    child["LIANS_MCP_ENABLED_TOOLS"] = "remember,recall"
    child["LIANS_MCP_SCHEMA_PROFILE"] = "compact"
    child["LIANS_MCP_RECALL_K"] = "20"
    child["LIANS_MCP_CONTEXT_MAX_TOKENS"] = "768"
    child["LIANS_MCP_PREWARM"] = "background"
    child["LIANS_CODEX_HOOK_K"] = "20"
    child["LIANS_CODEX_HOOK_MAX_TOKENS"] = "768"
    child["LIANS_CODEX_HOOK_MIN_SCORE"] = "0.45"
    child["LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS"] = "1800"
    child["LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS"] = "3000"
    child["LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS"] = "120000"
    project_dir = project_data_dir(data_home, root)
    if repair_private_paths:
        existing_token, existing_token_was_private = _preflight_existing_daemon_token(
            data_home, project_dir
        )
    else:
        token = project_dir / "daemon" / DAEMON_TOKEN_FILENAME
        existing_token = os.path.lexists(token)
        if existing_token:
            _require_regular_file(token, "daemon authentication token")
        existing_token_was_private = True
    _runtime_directory(data_home / "projects", repair_permissions=repair_private_paths)
    _runtime_directory(project_dir, repair_permissions=repair_private_paths)
    runtime_cwd = project_dir / "runtime"
    _runtime_directory(runtime_cwd, repair_permissions=repair_private_paths)
    child["LIANS_PLUGIN_RUNTIME_CWD"] = str(runtime_cwd)
    child["LIANS_CODEX_HOOK_RECEIPT"] = str(project_dir / "hook-receipts.jsonl")
    if existing_token and not existing_token_was_private:
        raise BootstrapError(
            "existing daemon authentication token was exposed before directory repair; "
            "stop the Lians daemon, remove user.token, and rerun setup to rotate it"
        )

    mode = profile.get("mode")
    if mode == "local":
        _apply_local_runtime_security(child)
        child["MASTER_ENCRYPTION_KEY"] = _decode_local_master_key(
            data_home / LOCAL_MASTER_KEY_FILENAME
        )
        child["AGENTMEM_ALLOW_UNENCRYPTED"] = "false"
        child["LIANS_MCP_LOCAL_SUBJECT_ID"] = project_subject_id
        child["LIANS_LOCAL_DB"] = str(project_dir / "memory.sqlite3")
        # SessionStart owns daemon creation after the full private-path/DACL
        # contract above. A warm UserPromptSubmit fast path is deliberately
        # client-only: if prewarm was skipped or the daemon expired, recall
        # fails open instead of creating local state without that validation.
        child["LIANS_CODEX_HOOK_DAEMON"] = "auto" if repair_private_paths else "client"
        daemon_dir = project_dir / "daemon"
        _runtime_directory(daemon_dir, repair_permissions=repair_private_paths)
        if repair_private_paths:
            _require_existing_daemon_token_private(daemon_dir)
        child["LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR"] = str(daemon_dir)
        artifact = profile.get("bge_artifact_dir")
        if not isinstance(artifact, str) or not artifact:
            raise BootstrapError("local profile is missing its BGE artifact directory")
        child["BGE_ONNX_ARTIFACT_DIR"] = artifact
        child["BGE_ONNX_INTRA_OP_THREADS"] = str(max(1, min(8, os.cpu_count() or 1)))
    elif mode == "managed":
        child.pop("LIANS_LOCAL_DB", None)
        child.pop("EMBEDDING_PROVIDER", None)
        child.pop("BGE_ONNX_ARTIFACT_DIR", None)
        child.pop("MASTER_ENCRYPTION_KEY", None)
        child.pop("AGENTMEM_ALLOW_UNENCRYPTED", None)
        child.pop("LIANS_MCP_LOCAL_SUBJECT_ID", None)
        child["LIANS_CODEX_HOOK_DAEMON"] = "off"
        url = validated_managed_url(profile["managed_url"])
        child["LIANS_URL"] = url
        if require_managed_key and not child.get("LIANS_API_KEY", "").strip():
            raise BootstrapError("managed mode requires LIANS_API_KEY in the process environment")
    else:
        raise BootstrapError("invalid plugin profile mode")
    return child


def setup(
    *,
    mode: str,
    data_home: Path,
    managed_url: str = DEFAULT_MANAGED_URL,
    bge_source: Path | None = None,
    download_bge: bool = False,
    plugin_root: Path = PLUGIN_ROOT,
) -> dict[str, object]:
    if mode not in {"local", "managed"}:
        raise BootstrapError("mode must be local or managed")
    if mode == "managed":
        managed_url = validated_managed_url(managed_url)
    initial_project_dir = project_data_dir(data_home, Path.cwd())
    existing_token, existing_token_was_private = _preflight_existing_daemon_token(
        data_home, initial_project_dir
    )
    if existing_token and not existing_token_was_private:
        raise BootstrapError(
            "existing daemon authentication token is unsafe; stop the Lians daemon, "
            "remove user.token, and rerun setup to rotate it"
        )
    wheel = discover_wheel(plugin_root / "vendor")
    lock_hash = sync_runtime(data_home, wheel, plugin_root / "runtime")
    artifact_dir = None
    if mode == "local":
        ensure_local_master_key(data_home)
        _safe_directory(data_home / "projects")
        _safe_directory(initial_project_dir)
        initial_daemon_dir = initial_project_dir / "daemon"
        _safe_directory(initial_daemon_dir)
        _require_existing_daemon_token_private(initial_daemon_dir)
        artifact_dir = stage_bge(data_home, source=bge_source, download=download_bge)
    path = write_profile(
        data_home,
        mode=mode,
        wheel=wheel,
        lock_sha256=lock_hash,
        managed_url=managed_url,
        bge_artifact_dir=artifact_dir,
    )
    launcher = install_launcher(data_home)
    path_ready = launcher_on_path(launcher)
    messages = [] if path_ready else [launcher_path_message(launcher)]
    return {
        "ok": True,
        "mode": mode,
        "data_home": str(data_home),
        "profile": str(path),
        "sdk_version": wheel.version,
        "sdk_sha256": wheel.sha256,
        "launcher": str(launcher),
        "launcher_on_path": path_ready,
        "managed_key_stored": False,
        "messages": messages,
    }


def _validated_codex_home(environ: Mapping[str, str], *, explicit: Path | None = None) -> Path:
    raw: str | Path = explicit if explicit is not None else environ.get("CODEX_HOME", "")
    if not raw:
        raw = Path.home() / ".codex"
    try:
        candidate = Path(raw).expanduser()
    except (TypeError, ValueError) as exc:
        raise BootstrapError("CODEX_HOME must be an absolute filesystem path") from exc
    if not candidate.is_absolute():
        raise BootstrapError("CODEX_HOME must be an absolute filesystem path")
    return Path(os.path.abspath(candidate))


def duplicate_user_configuration_warnings(
    *,
    environ: Mapping[str, str] | None = None,
    codex_home: Path | None = None,
) -> list[str]:
    """Detect additive legacy configuration without reading memory or secrets."""

    values = os.environ if environ is None else environ
    root = _validated_codex_home(values, explicit=codex_home)
    warnings: list[str] = []
    hooks = root / "hooks.json"
    if hooks.is_file():
        raw = ""
        try:
            raw = hooks.read_text(encoding="utf-8")
            document = json.loads(raw) if len(raw) <= 1_000_000 else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            document = {}
        command_values: list[str] = []

        def collect_commands(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {"command", "commandWindows"} and isinstance(nested, str):
                        command_values.append(nested)
                    else:
                        collect_commands(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_commands(nested)

        collect_commands(document)
        normalized_commands = [command.lower().replace("\\", "/") for command in command_values]
        known_legacy_hook = any(
            "user_prompt_submit_recall.py" in command
            or (
                "lians_plugin.py" in command
                and re.search(r"(?:^|\s)(?:hook|prewarm)(?:\s|$)", command)
            )
            for command in normalized_commands
        )
        if "lians" in raw.lower() or known_legacy_hook:
            warnings.append(
                "Existing user-level Lians hooks detected; plugin hooks load additively. "
                "Review duplicates with /hooks."
            )
    config = root / "config.toml"
    if config.is_file():
        try:
            document = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            document = {}
        servers = document.get("mcp_servers", {}) if isinstance(document, dict) else {}
        if isinstance(servers, dict) and isinstance(servers.get("lians"), dict):
            warnings.append(
                "Existing [mcp_servers.lians] detected in user config; disable one copy "
                "before relying on the plugin."
            )
    return warnings


def validate_bge_artifact_directory(path: Path) -> bool:
    """Cheap doctor check; the runtime rehashes both files before inference."""

    manifest_path = path / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        model = document["model"]
        tokenizer = document["tokenizer"]
        return bool(
            document.get("schema") == "lians.bge-onnx-artifact.v1"
            and model.get("revision") == BGE_REVISION
            and model.get("sha256") == BGE_MODEL_SHA256
            and tokenizer.get("sha256") == BGE_TOKENIZER_SHA256
            and (path / str(model.get("file"))).stat().st_size == model.get("bytes")
            and (path / str(tokenizer.get("file"))).stat().st_size == tokenizer.get("bytes")
        )
    except (FileNotFoundError, OSError, TypeError, KeyError, ValueError, json.JSONDecodeError):
        return False


def doctor(
    data_home: Path,
    environ: Mapping[str, str] | None = None,
    *,
    plugin_root: Path = PLUGIN_ROOT,
    codex_home: Path | None = None,
) -> dict[str, object]:
    values = os.environ if environ is None else environ
    checks: dict[str, bool] = {}
    messages: list[str] = []
    try:
        wheel = discover_wheel(plugin_root / "vendor")
        checks["bundled_sdk_verified"] = True
    except BootstrapError as exc:
        wheel = None
        checks["bundled_sdk_verified"] = False
        messages.append(str(exc))
    try:
        lock_hash = validate_frozen_runtime(plugin_root / "runtime", wheel)
        checks["frozen_runtime_valid"] = True
    except BootstrapError as exc:
        lock_hash = None
        checks["frozen_runtime_valid"] = False
        messages.append(str(exc))
    try:
        profile = read_profile(data_home)
        checks["profile_valid"] = True
    except BootstrapError as exc:
        profile = {}
        checks["profile_valid"] = False
        messages.append(str(exc))
    python = runtime_python(data_home)
    checks["runtime_ready"] = python.is_file()
    if not checks["runtime_ready"]:
        messages.append(f"runtime interpreter is missing: {python}")
    checks["installed_sdk_verified"] = bool(
        wheel is not None
        and checks["runtime_ready"]
        and _installed_sdk_matches_bundle(data_home, wheel)
    )
    if not checks["installed_sdk_verified"]:
        messages.append(
            "installed Lians SDK provenance or files do not match the bundled wheel; rerun setup"
        )
    checks["data_home_private"] = data_home.is_dir() and _private_path_permissions_ok(
        data_home, is_directory=True
    )
    if not checks["data_home_private"]:
        messages.append("plugin data directory permissions are not private; rerun setup")
    try:
        bin_dir = uv_tool_bin_dir(values)
        launcher_name = f"{LAUNCHER_COMMAND}.exe" if sys.platform == "win32" else LAUNCHER_COMMAND
        launcher = bin_dir / launcher_name
        checks["launcher_installed"] = _record_owns_launcher(data_home, launcher)
        if not checks["launcher_installed"]:
            messages.append(f"owned MCP launcher is missing or changed: {launcher}; rerun setup")
        checks["launcher_on_path"] = checks["launcher_installed"] and launcher_on_path(
            launcher, values
        )
        if checks["launcher_installed"] and not checks["launcher_on_path"]:
            messages.append(launcher_path_message(launcher))
    except BootstrapError as exc:
        launcher = None
        checks["launcher_installed"] = False
        checks["launcher_on_path"] = False
        messages.append(str(exc))
    if wheel is not None and profile:
        sdk = profile.get("sdk", {})
        checks["profile_matches_bundle"] = (
            isinstance(sdk, dict)
            and sdk.get("version") == wheel.version
            and sdk.get("sha256") == wheel.sha256
            and profile.get("runtime_lock_sha256") == lock_hash
        )
        if not checks["profile_matches_bundle"]:
            messages.append("profile SDK does not match the currently bundled wheel; rerun setup")
    else:
        checks["profile_matches_bundle"] = False
    mode = profile.get("mode")
    checks["managed_key_present"] = mode != "managed" or bool(
        values.get("LIANS_API_KEY", "").strip()
    )
    if mode == "managed" and not checks["managed_key_present"]:
        messages.append("LIANS_API_KEY is not set for managed mode")
    if mode == "local":
        artifact = profile.get("bge_artifact_dir")
        checks["local_model_ready"] = isinstance(artifact, str) and validate_bge_artifact_directory(
            Path(artifact)
        )
        if not checks["local_model_ready"]:
            messages.append("verified local BGE artifact directory is missing")
        try:
            _decode_local_master_key(data_home / LOCAL_MASTER_KEY_FILENAME)
            checks["local_key_ready"] = True
        except BootstrapError as exc:
            checks["local_key_ready"] = False
            messages.append(str(exc))
        checks["local_key_private"] = _private_path_permissions_ok(
            data_home / LOCAL_MASTER_KEY_FILENAME, is_directory=False
        )
        if not checks["local_key_private"]:
            messages.append("local encryption-key permissions are not private; rerun setup")
        current_project_dir = project_data_dir(data_home, Path.cwd())
        checks["project_directory_private"] = _private_path_permissions_ok(
            current_project_dir, is_directory=True
        )
        if not checks["project_directory_private"]:
            messages.append("current project memory directory is not private; rerun setup")
        checks["daemon_directory_private"] = _private_path_permissions_ok(
            current_project_dir / "daemon", is_directory=True
        )
        if not checks["daemon_directory_private"]:
            messages.append("current project daemon directory is not private; rerun setup")
        checks["daemon_token_private"] = _existing_daemon_token_is_private(
            current_project_dir / "daemon"
        )
        if not checks["daemon_token_private"]:
            messages.append(
                "existing daemon authentication token is unsafe; stop the Lians daemon, "
                "remove user.token, and rerun setup to rotate it"
            )
    else:
        checks["local_model_ready"] = mode == "managed"
        checks["local_key_ready"] = mode == "managed"
        checks["local_key_private"] = mode == "managed"
        checks["project_directory_private"] = mode == "managed"
        checks["daemon_directory_private"] = mode == "managed"
        checks["daemon_token_private"] = mode == "managed"
    migration_messages = duplicate_user_configuration_warnings(
        environ=values,
        codex_home=codex_home,
    )
    checks["legacy_configuration_clear"] = not migration_messages
    messages.extend(migration_messages)
    blocking = {
        "bundled_sdk_verified",
        "frozen_runtime_valid",
        "profile_valid",
        "runtime_ready",
        "installed_sdk_verified",
        "data_home_private",
        "launcher_installed",
        "launcher_on_path",
        "profile_matches_bundle",
        "managed_key_present",
        "local_model_ready",
        "local_key_ready",
        "local_key_private",
        "project_directory_private",
        "daemon_directory_private",
        "daemon_token_private",
        "legacy_configuration_clear",
    }
    ok = all(checks.get(name, False) for name in blocking)
    return {
        "ok": ok,
        "mode": mode if mode in {"local", "managed"} else None,
        "data_home": str(data_home),
        "checks": checks,
        "messages": messages,
        "launcher": str(launcher) if launcher is not None else None,
        "api_key": (
            "not-used"
            if mode == "local"
            else ("present" if values.get("LIANS_API_KEY", "").strip() else "not-set")
        ),
    }
