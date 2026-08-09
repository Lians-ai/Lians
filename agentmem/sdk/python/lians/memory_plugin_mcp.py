"""Codex plugin launcher for the frozen Lians Memory runtime.

The plugin installs this console script from its provenance-verified wheel into
the plugin's frozen virtual environment.  A small copy of that generated
console script is placed on ``uv tool``'s executable path, so starting MCP does
not resolve dependencies or depend on the plugin cache location.

Configuration is intentionally loaded before :mod:`lians.mcp_server`, whose
module-level settings are derived from the environment at import time.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
from importlib import metadata
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit


PROFILE_SCHEMA_VERSION = 1
PROFILE_FILENAME = "profile.json"
LOCAL_MASTER_KEY_FILENAME = "master.key"
MCP_RUNTIME_DIRECTORY = "mcp-runtime"
CODEX_DYNAMIC_SCOPE_ENV = "LIANS_MCP_CODEX_DYNAMIC_SCOPE"
MCP_DATA_HOME_ENV = "LIANS_MCP_DATA_HOME"

# Local mode is a plugin-owned, no-egress runtime. ``None`` means remove an
# inherited value; strings are forced even when the parent Codex process set a
# conflicting value. Keep this one explicit policy synchronized with the
# bootstrap copy used by the static hook/daemon path.
_LOCAL_RUNTIME_SECURITY_ENV: tuple[tuple[str, str | None], ...] = (
    ("LIANS_URL", None),
    ("LIANS_API_KEY", None),
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


class MemoryPluginConfigurationError(RuntimeError):
    """A safe, actionable plugin configuration error."""


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
        raise MemoryPluginConfigurationError(f"{description} is missing") from exc
    except OSError as exc:
        raise MemoryPluginConfigurationError(f"{description} is unavailable") from exc
    if _is_symlink_or_reparse(path) or not stat.S_ISREG(details.st_mode):
        raise MemoryPluginConfigurationError(f"{description} must be a regular non-reparse file")


def _validate_managed_url(value: object, *, profile_path: Path) -> str:
    """Validate a non-secret HTTPS service endpoint without echoing its value."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise MemoryPluginConfigurationError(
            f"managed plugin profile requires an explicit HTTPS URL: {profile_path}"
        )
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        raise MemoryPluginConfigurationError(
            f"managed plugin profile requires an explicit HTTPS URL: {profile_path}"
        )
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed or out-of-range values.
        _ = parsed.port
    except ValueError as exc:
        raise MemoryPluginConfigurationError(
            f"managed plugin profile requires an explicit HTTPS URL: {profile_path}"
        ) from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MemoryPluginConfigurationError(
            f"managed plugin profile requires an explicit HTTPS URL: {profile_path}"
        )
    return value.rstrip("/")


def native_data_home(
    environ: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the native, user-writable Lians Memory data directory."""

    values = os.environ if environ is None else environ
    if values.get("LIANS_MEMORY_HOME", ""):
        raise MemoryPluginConfigurationError(
            "LIANS_MEMORY_HOME overrides are not supported by the Codex plugin; "
            "unset it and rerun setup to use the OS-native private data directory"
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
                raise MemoryPluginConfigurationError(
                    "the native user home must be an absolute path"
                )
            root = user_home / "AppData" / "Local"
        return (root / "Lians" / "CodexMemory").resolve()
    if platform_name == "darwin":
        if not user_home.is_absolute():
            raise MemoryPluginConfigurationError(
                "the native user home must be an absolute path"
            )
        return (user_home / "Library" / "Application Support" / "Lians" / "CodexMemory").resolve()
    base = values.get("XDG_DATA_HOME", "").strip()
    configured = Path(base) if base else None
    if configured is not None and configured.is_absolute():
        root = configured
    else:
        if not user_home.is_absolute():
            raise MemoryPluginConfigurationError(
                "the native user home must be an absolute path"
            )
        root = user_home / ".local" / "share"
    return (root / "lians" / "codex-memory").resolve()


def project_scope(project_root: str | Path) -> str:
    """Derive the same stable project identifier used by the MCP server."""

    root = Path(project_root).expanduser().resolve()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip("-_.").lower()
    digest = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:12]
    return f"{(slug[:40] or 'project')}-{digest}"


def _codex_dynamic_scope_enabled(values: Mapping[str, str]) -> bool:
    raw = values.get(CODEX_DYNAMIC_SCOPE_ENV)
    if raw is None:
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MemoryPluginConfigurationError(
        f"{CODEX_DYNAMIC_SCOPE_ENV} must be true or false when set"
    )


def _read_profile(data_home: Path) -> dict[str, object]:
    path = data_home / PROFILE_FILENAME
    if not os.path.lexists(path):
        raise MemoryPluginConfigurationError(
            "Lians Memory is not set up; run the plugin setup command first"
        )
    _require_regular_file(path, "plugin profile")
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryPluginConfigurationError(f"invalid plugin profile: {path}") from exc

    if not isinstance(profile, dict) or profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise MemoryPluginConfigurationError(f"unsupported plugin profile: {path}")
    mode = profile.get("mode")
    if mode not in {"local", "managed"}:
        raise MemoryPluginConfigurationError(f"invalid plugin mode in {path}")
    if mode == "managed":
        profile["managed_url"] = _validate_managed_url(
            profile.get("managed_url"), profile_path=path
        )
    return profile


def _installed_version() -> str:
    try:
        return metadata.version("lians-sdk")
    except metadata.PackageNotFoundError as exc:  # pragma: no cover - broken installation
        raise MemoryPluginConfigurationError("the frozen Lians SDK is not installed") from exc


def _read_local_master_key(data_home: Path) -> str:
    path = data_home / LOCAL_MASTER_KEY_FILENAME
    if not os.path.lexists(path):
        raise MemoryPluginConfigurationError("local encryption key is missing; rerun plugin setup")
    _require_regular_file(path, "local encryption key")
    try:
        encoded = path.read_text(encoding="ascii").strip()
        decoded = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, binascii.Error, ValueError) as exc:
        raise MemoryPluginConfigurationError(
            "local encryption key is invalid; restore the original key or start a new profile"
        ) from exc
    if len(decoded) != 32:
        raise MemoryPluginConfigurationError(
            "local encryption key is invalid; restore the original key or start a new profile"
        )
    return encoded


def _private_mcp_runtime_directory(data_home: Path) -> Path:
    """Create an empty, private cwd so engine settings cannot read a repo .env."""

    runtime = data_home / MCP_RUNTIME_DIRECTORY
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        details = runtime.lstat()
    except OSError as exc:
        raise MemoryPluginConfigurationError(
            "the private MCP runtime directory is unavailable; rerun plugin setup"
        ) from exc
    if _is_symlink_or_reparse(runtime) or not stat.S_ISDIR(details.st_mode):
        raise MemoryPluginConfigurationError(
            "the private MCP runtime directory must be a non-reparse directory"
        )
    if os.path.lexists(runtime / ".env"):
        raise MemoryPluginConfigurationError(
            "the private MCP runtime directory must not contain a .env file"
        )
    try:
        if os.name != "nt":
            runtime.chmod(0o700)
        return runtime.resolve(strict=True)
    except OSError as exc:
        raise MemoryPluginConfigurationError(
            "the private MCP runtime directory could not be secured; rerun plugin setup"
        ) from exc


def _apply_local_runtime_security(child: dict[str, str]) -> None:
    for name, value in _LOCAL_RUNTIME_SECURITY_ENV:
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value


def configured_environment(
    environ: Mapping[str, str] | None = None,
    *,
    project_root: str | Path | None = None,
    installed_version: str | None = None,
    require_managed_key: bool = True,
) -> tuple[dict[str, str], dict[str, object]]:
    """Load the plugin profile and return the MCP process environment.

    Static integrations use the inherited working directory for project
    isolation. Codex dynamic mode instead binds from authenticated MCP request
    metadata after startup, so the launcher's working directory is ignored.
    Secrets are accepted only from the process environment and are never read
    from or written to the profile.
    """

    values = os.environ if environ is None else environ
    child = dict(values)
    # These must also be set by the outer MCP/hook launchers so they take
    # effect before this module is imported. Reassert them for every runtime
    # child before the engine and any subprocess-visible code are loaded.
    child["PYTHONPATH"] = ""
    child["PYTHONHOME"] = ""
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    dynamic_scope = _codex_dynamic_scope_enabled(values)
    home = native_data_home(values)
    profile = _read_profile(home)
    root = None
    if not dynamic_scope:
        root = (
            Path.cwd().resolve()
            if project_root is None
            else Path(project_root).expanduser().resolve()
        )

    sdk = profile.get("sdk")
    version = _installed_version() if installed_version is None else installed_version
    if not isinstance(sdk, dict) or sdk.get("version") != version:
        raise MemoryPluginConfigurationError(
            "plugin profile does not match the installed SDK; rerun plugin setup"
        )

    child["LIANS_MEMORY_HOME"] = str(home)
    child[MCP_DATA_HOME_ENV] = str(home)
    if dynamic_scope:
        child[CODEX_DYNAMIC_SCOPE_ENV] = "true"
        child.pop("LIANS_MCP_PROJECT_ROOT", None)
    else:
        child.pop(CODEX_DYNAMIC_SCOPE_ENV, None)
        assert root is not None
        child["LIANS_MCP_PROJECT_ROOT"] = str(root)
    # The plugin is project-scoped by construction.  Ambient SDK settings from
    # another integration must not collapse multiple repositories together.
    child["LIANS_AGENT_ID"] = ""
    child["LIANS_NAMESPACE"] = ""
    project_subject_id = None
    if dynamic_scope:
        child.pop("LIANS_MCP_SUBJECT_ID", None)
        child.pop("LIANS_MCP_LOCAL_SUBJECT_ID", None)
    else:
        assert root is not None
        project_subject_id = f"codex-project:{project_scope(root)}"
        child["LIANS_MCP_SUBJECT_ID"] = project_subject_id
    child["LIANS_MCP_ENABLED_TOOLS"] = "remember,recall"
    child["LIANS_MCP_SCHEMA_PROFILE"] = "compact"
    child["LIANS_MCP_RECALL_K"] = "20"
    child["LIANS_MCP_CONTEXT_MAX_TOKENS"] = "768"
    # Codex dynamic mode cannot safely prewarm until the first authenticated
    # sandbox metadata binds the project. Static integrations have a trusted
    # project root at startup and keep the latency-oriented background prewarm.
    child["LIANS_MCP_PREWARM"] = "off" if dynamic_scope else "background"

    mode = profile["mode"]
    if mode == "local":
        _apply_local_runtime_security(child)
        child["MASTER_ENCRYPTION_KEY"] = _read_local_master_key(home)
        child["AGENTMEM_ALLOW_UNENCRYPTED"] = "false"
        if dynamic_scope:
            # The MCP server derives these only after Codex supplies a validated
            # sandboxCwd. Leaving them unset prevents plugin-root fallback.
            child.pop("LIANS_LOCAL_DB", None)
        else:
            assert root is not None and project_subject_id is not None
            # Preserve the original local-only variable for older SDK runtimes.
            child["LIANS_MCP_LOCAL_SUBJECT_ID"] = project_subject_id
            project_dir = home / "projects" / project_scope(root)
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                if os.name != "nt":
                    project_dir.chmod(0o700)
            except OSError as exc:
                raise MemoryPluginConfigurationError(
                    f"cannot create the project memory directory: {project_dir}"
                ) from exc
            child["LIANS_LOCAL_DB"] = str(project_dir / "memory.sqlite3")
        artifact = profile.get("bge_artifact_dir")
        if not isinstance(artifact, str) or not artifact:
            raise MemoryPluginConfigurationError(
                "local profile is missing its BGE artifact directory; rerun plugin setup"
            )
        child["BGE_ONNX_ARTIFACT_DIR"] = artifact
        child["BGE_ONNX_INTRA_OP_THREADS"] = str(max(1, min(8, os.cpu_count() or 1)))
    else:
        child.pop("LIANS_LOCAL_DB", None)
        child.pop("EMBEDDING_PROVIDER", None)
        child.pop("BGE_ONNX_ARTIFACT_DIR", None)
        child.pop("MASTER_ENCRYPTION_KEY", None)
        child.pop("AGENTMEM_ALLOW_UNENCRYPTED", None)
        child.pop("LIANS_MCP_LOCAL_SUBJECT_ID", None)
        managed_url = str(profile["managed_url"])
        child["LIANS_URL"] = managed_url.rstrip("/")
        if require_managed_key and not child.get("LIANS_API_KEY", "").strip():
            raise MemoryPluginConfigurationError(
                "managed mode requires LIANS_API_KEY in the Codex process environment"
            )

    return child, profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lians-memory-mcp")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the profile and frozen launcher without starting MCP",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare the plugin environment, then start the compact MCP server."""

    args = _parser().parse_args(argv)
    try:
        child, profile = configured_environment()
        runtime_cwd = _private_mcp_runtime_directory(Path(child["LIANS_MEMORY_HOME"]))
        child["LIANS_PLUGIN_RUNTIME_CWD"] = str(runtime_cwd)
        os.environ.clear()
        os.environ.update(child)
        if args.check:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": profile["mode"],
                        "data_home": child["LIANS_MEMORY_HOME"],
                        "project_root": child.get("LIANS_MCP_PROJECT_ROOT"),
                        "dynamic_scope": child.get(CODEX_DYNAMIC_SCOPE_ENV) == "true",
                    },
                    sort_keys=True,
                )
            )
            return 0

        try:
            os.chdir(runtime_cwd)
        except OSError as exc:
            raise MemoryPluginConfigurationError(
                "the private MCP runtime directory is unavailable; rerun plugin setup"
            ) from exc
        # Import only after the profile has populated every module-level MCP
        # setting and the process has left the untrusted repository cwd. The
        # engine's Pydantic settings read `.env` relative to cwd at import/use.
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    except MemoryPluginConfigurationError as exc:
        print(f"Lians Memory MCP is unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
