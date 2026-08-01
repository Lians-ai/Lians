"""Create or safely upgrade the local-only homelab environment file."""

from __future__ import annotations

import base64
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

DEFAULT_EVIDENCE_SIGNING_KEY_ID = "lians-homelab-ed25519-v1"
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class EnvironmentBootstrapError(RuntimeError):
    """Raised without reflecting secret values from an invalid environment."""


def _unquote(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _last_value(content: str, name: str) -> str | None:
    matches = list(re.finditer(rf"(?m)^{re.escape(name)}=([^\r\n]*)$", content))
    return _unquote(matches[-1].group(1)) if matches else None


def _upsert(content: str, name: str, value: str) -> str:
    matches = list(re.finditer(rf"(?m)^{re.escape(name)}=[^\r\n]*$", content))
    assignment = f"{name}={value}"
    if matches:
        match = matches[-1]
        return content[: match.start()] + assignment + content[match.end() :]
    if content and not content.endswith(("\n", "\r")):
        content += "\n"
    return content + assignment + "\n"


def _validate_private_key(value: str) -> None:
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise EnvironmentBootstrapError(
            "LIANS_EVIDENCE_SIGNING_PRIVATE_KEY must be valid base64; no changes were made"
        ) from exc
    if len(raw) != 32:
        raise EnvironmentBootstrapError(
            "LIANS_EVIDENCE_SIGNING_PRIVATE_KEY must decode to 32 bytes; no changes were made"
        )


def _validate_key_id(value: str) -> None:
    if not _KEY_ID_PATTERN.fullmatch(value):
        raise EnvironmentBootstrapError(
            "LIANS_EVIDENCE_SIGNING_KEY_ID must be a clear 1-128 character identifier; "
            "no changes were made"
        )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def bootstrap_environment(example_path: Path, target_path: Path) -> str:
    """Return ``created``, ``upgraded``, or ``unchanged`` without exposing secrets."""

    created = not target_path.exists()
    if created:
        content = example_path.read_text(encoding="utf-8")
        content = _upsert(content, "LIANS_ADMIN_SECRET", secrets.token_hex(32))
        content = _upsert(
            content,
            "LIANS_MASTER_ENCRYPTION_KEY",
            base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        )
    else:
        content = target_path.read_text(encoding="utf-8")

    changed = created
    private_key = _last_value(content, "LIANS_EVIDENCE_SIGNING_PRIVATE_KEY")
    if not private_key:
        private_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        content = _upsert(content, "LIANS_EVIDENCE_SIGNING_PRIVATE_KEY", private_key)
        changed = True
    _validate_private_key(private_key)

    key_id = _last_value(content, "LIANS_EVIDENCE_SIGNING_KEY_ID")
    if not key_id:
        key_id = DEFAULT_EVIDENCE_SIGNING_KEY_ID
        content = _upsert(content, "LIANS_EVIDENCE_SIGNING_KEY_ID", key_id)
        changed = True
    _validate_key_id(key_id)

    if changed:
        _atomic_write(target_path, content)
    else:
        try:
            target_path.chmod(0o600)
        except OSError:
            pass
    return "created" if created else ("upgraded" if changed else "unchanged")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: env_bootstrap.py EXAMPLE_ENV TARGET_ENV", file=sys.stderr)
        return 2
    try:
        status = bootstrap_environment(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, EnvironmentBootstrapError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
