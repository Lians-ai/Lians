"""The small consumer surface for evidence-backed AI work verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .project import Project
from .store import MemoryStore
from .task_contract import TaskContractService, workspace_snapshot
from .verification import VerificationService

CHECK_SCHEMA = "https://lians.ai/schemas/check-config/v0.1"
CHECK_PATH = Path(".lians") / "check.json"
_CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET = re.compile(
    r"(?i)(?:sk-(?:proj-)?[0-9a-z_-]{20,}|ghp_[0-9a-z_]{20,}|"
    r"github_pat_[0-9a-z_]{20,}|AKIA[0-9A-Z]{16}|Bearer\s+[0-9a-z._~+/=-]{8,}|"
    r"-----BEGIN(?: RSA| EC| OPENSSH)? PRIVATE KEY-----|"
    r"(?:token|secret|password|api[_-]?key)\s*[:=]\s*\S+)"
)
_SECRET_OPTION = re.compile(
    r"(?i)^--?(?:api[-_]?key|access[-_]?token|authorization|password|secret|token)(?:=|$)"
)
_SAFE_ENVIRONMENT = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "TERM",
    "COLORTERM",
    "VIRTUAL_ENV",
    "PNPM_HOME",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "GOPATH",
    "GOMODCACHE",
}
_MAX_CAPTURE_BYTES = 1_000_000
_MAX_TOTAL_OUTPUT_BYTES = 16_000_000
_MAX_WORKSPACE_BYTES = 32_000_000


class CheckConfigError(ValueError):
    """A Lians Check configuration is missing, unsafe, or no longer authorized."""


@dataclass(frozen=True)
class CheckSpec:
    id: str
    label: str
    argv: tuple[str, ...]
    timeout_seconds: int = 600

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "argv": list(self.argv),
            "timeout_seconds": self.timeout_seconds,
        }


def _bounded_text(path: Path, maximum: int = 2_000_000) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _clean_spec(value: CheckSpec | dict[str, Any], *, index: int) -> CheckSpec:
    raw = value.public() if isinstance(value, CheckSpec) else value
    if not isinstance(raw, dict) or set(raw) != {
        "id",
        "label",
        "argv",
        "timeout_seconds",
    }:
        raise CheckConfigError(f"checks[{index}] has an invalid shape")
    check_id = str(raw["id"]).strip().casefold()
    if not _CHECK_ID.fullmatch(check_id):
        raise CheckConfigError(f"checks[{index}].id is invalid")
    label = " ".join(str(raw["label"]).strip().split())
    if not label or len(label) > 80:
        raise CheckConfigError(f"checks[{index}].label is invalid")
    argv = raw["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= 30:
        raise CheckConfigError(f"checks[{index}].argv must contain 1 to 30 arguments")
    clean_argv = tuple(str(item) for item in argv)
    if any(
        not item or len(item) > 1_000 or "\x00" in item or "\n" in item or "\r" in item
        for item in clean_argv
    ):
        raise CheckConfigError(f"checks[{index}].argv contains an invalid argument")
    if clean_argv[0].startswith("-"):
        raise CheckConfigError(f"checks[{index}].argv has an invalid executable")
    if any(_SECRET.search(item) or _SECRET_OPTION.search(item) for item in clean_argv):
        raise CheckConfigError(f"checks[{index}].argv must not contain credentials")
    timeout = raw["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= 1_800:
        raise CheckConfigError(f"checks[{index}].timeout_seconds must be 1 to 1800")
    return CheckSpec(check_id, label, clean_argv, timeout)


def _config_body(checks: list[CheckSpec]) -> dict[str, Any]:
    return {"schema": CHECK_SCHEMA, "checks": [item.public() for item in checks]}


def _config_digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _task_id(body: dict[str, Any]) -> str:
    return f"check-{_config_digest(body)[:24]}"


def _load_config(root: Path) -> tuple[dict[str, Any], list[CheckSpec]]:
    path = root / CHECK_PATH
    raw = _bounded_text(path, maximum=128_000)
    if raw is None:
        raise CheckConfigError("Lians Check is not set up for this project. Run `lians init`.")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckConfigError("The Lians Check configuration is invalid. Run `lians init --force`.") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "task_id", "checks"}:
        raise CheckConfigError("The Lians Check configuration has an invalid shape.")
    if document.get("schema") != CHECK_SCHEMA:
        raise CheckConfigError("The Lians Check configuration version is not supported.")
    values = document.get("checks")
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise CheckConfigError("Lians Check requires 1 to 8 checks.")
    checks = [_clean_spec(value, index=index) for index, value in enumerate(values)]
    if len({item.id for item in checks}) != len(checks):
        raise CheckConfigError("Lians Check IDs must be unique.")
    body = _config_body(checks)
    if document.get("task_id") != _task_id(body):
        raise CheckConfigError(
            "The Lians Check configuration changed after authorization. Run `lians init --force`."
        )
    return document, checks


def _package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lock").exists() or (root / "bun.lockb").exists():
        return "bun"
    return "npm"


def _python_check_command(root: Path, *arguments: str) -> tuple[str, ...]:
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return ("uv", "run", *arguments)
    if (root / "poetry.lock").is_file() and shutil.which("poetry"):
        return ("poetry", "run", *arguments)
    for candidate in (Path(".venv/bin/python"), Path(".venv/Scripts/python.exe")):
        if (root / candidate).is_file():
            return (candidate.as_posix(), "-m", *arguments)
    executable = (
        "python"
        if shutil.which("python")
        else "python3"
        if shutil.which("python3")
        else sys.executable
    )
    return (executable, "-m", *arguments)


def detect_checks(root: Path) -> list[CheckSpec]:
    """Discover a short, high-signal check set without executing project code."""

    detected: list[CheckSpec] = []
    package = _bounded_text(root / "package.json")
    if package is not None:
        try:
            scripts = json.loads(package).get("scripts") or {}
        except (AttributeError, json.JSONDecodeError):
            scripts = {}
        if isinstance(scripts, dict):
            manager = _package_manager(root)
            labels = {
                "test": "Tests",
                "build": "Build",
                "typecheck": "Type check",
                "lint": "Lint",
            }
            for name in ("test", "build", "typecheck", "lint"):
                command = scripts.get(name)
                if not isinstance(command, str) or not command.strip():
                    continue
                if name == "test" and "no test specified" in command.casefold():
                    continue
                detected.append(
                    CheckSpec(name, labels[name], (manager, "run", name))
                )

    pyproject = _bounded_text(root / "pyproject.toml") or ""
    has_python_tests = any(
        (root / value).exists() for value in ("tests", "test", "pytest.ini")
    ) or "[tool.pytest" in pyproject
    existing = {item.id for item in detected}
    if has_python_tests:
        check_id = "python-tests" if "test" in existing else "tests"
        detected.append(
            CheckSpec(check_id, "Python tests", _python_check_command(root, "pytest", "-q"))
        )
        existing.add(check_id)
    if "[tool.ruff" in pyproject:
        check_id = "python-lint" if "lint" in existing else "lint"
        detected.append(
            CheckSpec(check_id, "Python lint", _python_check_command(root, "ruff", "check", "."))
        )
    if (root / "go.mod").is_file():
        detected.append(CheckSpec("go-tests", "Go tests", ("go", "test", "./...")))
    if (root / "Cargo.toml").is_file():
        detected.append(
            CheckSpec("rust-tests", "Rust tests", ("cargo", "test", "--all-targets"))
        )
    return detected[:8]


def preview_checks(root: Path, *, force: bool = False) -> list[CheckSpec]:
    """Show the exact existing policy or the commands a fresh setup would authorize."""

    if (root / CHECK_PATH).exists() and not force:
        return _load_config(root)[1]
    return detect_checks(root)


def parse_custom_check(value: str) -> CheckSpec:
    """Parse NAME=COMMAND without enabling a shell."""

    name, separator, command = value.partition("=")
    if not separator:
        raise CheckConfigError("Custom checks use NAME=COMMAND.")
    check_id = re.sub(r"[^a-z0-9-]+", "-", name.strip().casefold()).strip("-")
    label = " ".join(part.capitalize() for part in name.replace("-", " ").split())
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise CheckConfigError("The custom check command could not be parsed.") from exc
    return _clean_spec(
        {
            "id": check_id,
            "label": label or "Check",
            "argv": argv,
            "timeout_seconds": 600,
        },
        index=0,
    )


def _write_config(root: Path, document: dict[str, Any]) -> None:
    directory = root / CHECK_PATH.parent
    path = root / CHECK_PATH
    if directory.is_symlink() or path.is_symlink():
        raise CheckConfigError("Refusing to write Lians Check through a symbolic link.")
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".check.", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise CheckConfigError("Lians could not save the project check policy safely.") from exc


def _runner_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENVIRONMENT
    }
    environment.update(
        {
            "CI": "1",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return environment


def _safe_excerpt(stdout: bytes, stderr: bytes) -> str:
    raw = (stdout + b"\n" + stderr)[-8_000:].decode("utf-8", errors="replace")
    rendered = _ANSI.sub("", raw)
    rendered = _SECRET.sub("[redacted]", rendered)
    rendered = "\n".join(line.strip() for line in rendered.splitlines() if line.strip())
    return rendered[-600:] if rendered else "No output was produced."


def _run_check(root: Path, spec: CheckSpec) -> dict[str, Any]:
    started = time.monotonic()
    output_limited = False
    timed_out = False
    try:
        process = subprocess.Popen(
            list(spec.argv),
            cwd=root,
            env=_runner_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        output_limit = threading.Event()
        captured: dict[str, tuple[bytes, bytes]] = {}

        def read_stream(name: str, stream) -> None:
            digest = hashlib.sha256()
            tail = bytearray()
            total = 0
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                tail.extend(chunk)
                if len(tail) > _MAX_CAPTURE_BYTES:
                    del tail[:-_MAX_CAPTURE_BYTES]
                if total > _MAX_TOTAL_OUTPUT_BYTES:
                    output_limit.set()
            captured[name] = (bytes(tail), digest.digest())

        readers = [
            threading.Thread(
                target=read_stream,
                args=(name, stream),
                daemon=True,
            )
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in readers:
            reader.start()
        deadline = started + spec.timeout_seconds
        while process.poll() is None:
            if output_limit.is_set() or time.monotonic() >= deadline:
                output_limited = output_limit.is_set()
                timed_out = not output_limited
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                break
            time.sleep(0.02)
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        output_limited = output_limited or output_limit.is_set()
        stdout, stdout_digest = captured.get("stdout", (b"", hashlib.sha256().digest()))
        stderr, stderr_digest = captured.get("stderr", (b"", hashlib.sha256().digest()))
        exit_code = 125 if output_limited else 124 if timed_out else int(process.returncode)
        digest = hashlib.sha256(stdout_digest + b"\x00" + stderr_digest).hexdigest()
    except OSError as exc:
        stdout = b""
        stderr = str(exc).encode("utf-8", errors="replace")
        exit_code = 127
        timed_out = False
        digest = hashlib.sha256(stdout + b"\x00" + stderr).hexdigest()
    except subprocess.TimeoutExpired:
        stdout = b""
        stderr = b"The check process did not stop after its time limit."
        exit_code = 124
        timed_out = True
        digest = hashlib.sha256(stdout + b"\x00" + stderr).hexdigest()
    duration = round(time.monotonic() - started, 3)
    status = "passed" if exit_code == 0 else "failed"
    evidence = (
        f"Lians ran the configured command in {duration:.3f}s; exit code {exit_code}; "
        f"output SHA-256 {digest}."
    )
    return {
        "name": spec.id,
        "label": spec.label,
        "status": status,
        "evidence": evidence,
        "command": " ".join(spec.argv),
        "exit_code": exit_code,
        "output_sha256": digest,
        "duration_seconds": duration,
        "timed_out": timed_out,
        "detail": (
            "Output exceeded the 16 MB safety limit."
            if output_limited
            else "The check exceeded its time limit."
            if timed_out
            else _safe_excerpt(stdout, stderr)
        ),
    }


def _git_text(root: Path, arguments: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *arguments],
            cwd=root,
            env=_runner_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 100_000:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def resolve_base_ref(root: Path, requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    candidates: list[str] = []
    remote_head = _git_text(root, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if remote_head:
        candidates.append(remote_head)
    candidates.extend(("origin/main", "origin/master", "main", "master"))
    current = _git_text(root, ["branch", "--show-current"])
    for candidate in dict.fromkeys(candidates):
        if candidate == current:
            continue
        if _git_text(root, ["rev-parse", "--verify", f"{candidate}^{{commit}}"]):
            merge_base = _git_text(root, ["merge-base", "HEAD", candidate])
            if merge_base:
                return merge_base
    return "HEAD"


def _workspace_content_digest(root: Path) -> str:
    """Bind check ordering to tracked and untracked content, not only path status."""

    digest = hashlib.sha256()
    try:
        for arguments in (
            ["rev-parse", "HEAD"],
            ["diff", "--no-ext-diff", "--binary", "HEAD", "--"],
        ):
            result = subprocess.run(
                ["git", "-c", "core.quotepath=false", *arguments],
                cwd=root,
                env=_runner_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0 or len(result.stdout) > _MAX_WORKSPACE_BYTES:
                raise CheckConfigError("The current Git workspace is too large to bind safely.")
            digest.update(result.stdout)
            digest.update(b"\x00")
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root,
            env=_runner_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckConfigError("Lians could not bind the current Git workspace.") from exc
    if untracked.returncode != 0 or len(untracked.stdout) > 1_000_000:
        raise CheckConfigError("Lians could not bind the current untracked files.")
    total = 0
    for raw_path in untracked.stdout.split(b"\x00"):
        if not raw_path:
            continue
        path_text = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / path_text
        try:
            if path.is_symlink() or not path.is_file():
                raise CheckConfigError("Lians found an unsupported untracked file.")
            size = path.stat().st_size
            total += size
            if total > _MAX_WORKSPACE_BYTES:
                raise CheckConfigError("Untracked files are too large to bind safely.")
            digest.update(raw_path)
            digest.update(b"\x00")
            with path.open("rb") as handle:
                while chunk := handle.read(65_536):
                    digest.update(chunk)
            digest.update(b"\x00")
        except OSError as exc:
            raise CheckConfigError("Lians could not bind an untracked file.") from exc
    return digest.hexdigest()


class LiansCheckService:
    """Initialize and run the smallest evidence-backed Lians workflow."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.tasks = TaskContractService(store)
        self.verification = VerificationService(store)

    @staticmethod
    def _root(project: Project) -> Path:
        root = project.trusted_root
        if root is None or not (root / ".git").exists():
            raise CheckConfigError("Lians Check must run inside a Git repository.")
        return root

    def initialize(
        self,
        project: Project,
        *,
        checks: list[CheckSpec] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        root = self._root(project)
        existing = root / CHECK_PATH
        if existing.exists() and not force and checks is None:
            document, clean_checks = _load_config(root)
        else:
            clean_checks = [
                _clean_spec(value, index=index)
                for index, value in enumerate(checks if checks is not None else detect_checks(root))
            ]
            if not clean_checks:
                raise CheckConfigError(
                    "No project checks were found. Add one with `lians init --command tests=...`."
                )
            if len({item.id for item in clean_checks}) != len(clean_checks):
                raise CheckConfigError("Lians Check IDs must be unique.")
            body = _config_body(clean_checks)
            document = {**body, "task_id": _task_id(body)}
            _write_config(root, document)

        task_id = str(document["task_id"])
        config_digest = _config_digest(_config_body(clean_checks))
        criteria = [f"{item.label} passes for the current code" for item in clean_checks]
        constraints = [f"{item.label} must pass before review" for item in clean_checks]
        try:
            task = self.tasks.status(task_id, project_id=project.id)
        except LookupError:
            task = self.tasks.start(
                "Check the current AI-generated work before a person reviews it.",
                criteria,
                constraints=constraints,
                project_id=project.id,
                title="Proof of done",
                task_id=task_id,
                client="lians-init",
            )
        expected_criteria = [item["description"] for item in task["contract"]["success_criteria"]]
        if expected_criteria != criteria:
            raise CheckConfigError("The authorized Lians Check policy does not match this project.")
        try:
            configured = self.verification.policy(task_id, project_id=project.id)
        except LookupError:
            configured = None
        if configured is None or (
            force and configured["policy"].get("check_config_sha256") != config_digest
        ):
            configured = self.verification.configure(
                task_id,
                project_id=project.id,
                allowed_paths=["**"],
                criterion_paths={
                    f"criterion-{index}": ["**"]
                    for index in range(1, len(clean_checks) + 1)
                },
                required_checks=[item.id for item in clean_checks],
                check_config_sha256=config_digest,
                max_changed_files=2_000,
                max_advisories=5,
                client="lians-init",
            )
        if configured["policy"].get("check_config_sha256") != config_digest:
            raise CheckConfigError("The authorized Lians Check policy digest does not match.")
        return {
            "schema": "https://lians.ai/schemas/check-setup/v0.1",
            "status": "ready",
            "headline": "LIANS CHECK IS READY",
            "message": "Run `lians check` after your AI says it is done.",
            "next_step": (
                "Review `.lians/check.json`, then commit it if the policy should be shared."
            ),
            "project": project.public(),
            "config_path": str(existing),
            "task_id": task_id,
            "policy_sha256": config_digest,
            "checks": [item.public() for item in clean_checks],
            "policy_memory_id": configured["memory_id"],
        }

    def run(
        self,
        project: Project,
        *,
        base_ref: str = "auto",
        progress: Callable[[CheckSpec], None] | None = None,
    ) -> dict[str, Any]:
        root = self._root(project)
        document, checks = _load_config(root)
        task_id = str(document["task_id"])
        try:
            self.tasks.status(task_id, project_id=project.id)
            configured = self.verification.policy(task_id, project_id=project.id)
        except LookupError as exc:
            raise CheckConfigError(
                "This Lians Check policy is not authorized on this device. Run `lians init`."
            ) from exc
        config_digest = _config_digest(_config_body(checks))
        if configured["policy"].get("check_config_sha256") != config_digest:
            raise CheckConfigError("The authorized Lians Check policy digest does not match.")

        before_content = _workspace_content_digest(root)
        results = []
        for spec in checks:
            if progress is not None:
                progress(spec)
            results.append(_run_check(root, spec))
        after = workspace_snapshot(root)
        after_content = _workspace_content_digest(root)
        workspace_changed = before_content != after_content
        passed = [item for item in results if item["status"] == "passed"]
        failed = [item for item in results if item["status"] == "failed"]
        output_receipt = hashlib.sha256(
            json.dumps(results, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        evidence = [
            {
                "criterion_id": f"criterion-{index}",
                "evidence": item["evidence"],
                "trust_class": "measured_local",
                "source": "Lians Check local runner",
            }
            for index, item in enumerate(results, start=1)
            if item["status"] == "passed"
        ]
        constraint_checks = [
            {
                "constraint_id": f"constraint-{index}",
                "status": item["status"],
                "evidence": item["evidence"],
                "trust_class": "measured_local",
                "source": "Lians Check local runner",
            }
            for index, item in enumerate(results, start=1)
        ]
        blockers = (
            ["A configured check changed the Git workspace. Run Lians Check again."]
            if workspace_changed
            else []
        )
        def record_checkpoint(current_workspace: dict[str, Any]) -> None:
            self.tasks._checkpoint_trusted(
                task_id,
                (
                    f"Lians measured {len(passed)} passing and {len(failed)} failing checks."
                ),
                issuer="local_verification",
                receipt_sha256=output_receipt,
                project_id=project.id,
                current_action=(
                    "Fix the failing check and run Lians Check again."
                    if failed
                    else "Review the current changes."
                ),
                evidence=evidence,
                constraint_checks=constraint_checks,
                blockers=blockers,
                client="lians-check",
                workspace=current_workspace,
                _replace_evidence=True,
                _replace_constraint_checks=True,
            )

        record_checkpoint(after)
        base = resolve_base_ref(root, base_ref)
        verification_results = [
            {
                "name": item["name"],
                "status": item["status"],
                "evidence": item["evidence"],
                "command": None,
                "exit_code": item["exit_code"],
                "output_sha256": item["output_sha256"],
            }
            for item in results
        ]
        verified = self.verification._verify_measured_local(
            task_id,
            project=project,
            base_ref=base,
            agent_summary="Lians ran the configured checks against the current repository state.",
            check_results=verification_results,
        )
        final_content = _workspace_content_digest(root)
        if final_content != after_content:
            workspace_changed = True
            blockers = ["The Git workspace changed while Lians was creating its receipt."]
            record_checkpoint(workspace_snapshot(root))
            verified = self.verification._verify_measured_local(
                task_id,
                project=project,
                base_ref=base,
                agent_summary=(
                    "Lians detected a repository change after the configured checks finished."
                ),
                check_results=verification_results,
            )
        ready = verified["verdict"] == "ready_for_human_ship_review"
        status = "ready_for_review" if ready else "needs_work"
        next_step = (
            "Review the current changes. Human approval is still required."
            if ready
            else (
                f"Fix {failed[0]['label']} and run `lians check` again."
                if failed
                else "Resolve the listed blocker and run `lians check` again."
            )
        )
        return {
            "schema": "https://lians.ai/schemas/check-result/v0.1",
            "status": status,
            "headline": "READY TO REVIEW" if ready else "NEEDS WORK",
            "message": (
                f"{len(passed)} current check{'s' if len(passed) != 1 else ''} passed."
                if ready
                else f"{len(failed)} check{'s' if len(failed) != 1 else ''} failed or proof is incomplete."
            ),
            "project": project.public(),
            "task_id": task_id,
            "policy_sha256": config_digest,
            "base_ref": base,
            "checks": results,
            "workspace_changed_during_check": workspace_changed,
            "verification": {
                "verdict": verified["verdict"],
                "blockers": verified["blockers"],
                "changed_file_count": verified["receipt"]["changed_file_count"],
                "receipt_id": verified["receipt"]["id"],
                "external_check_trust": verified["receipt"]["trust"]["external_checks"],
            },
            "next_step": next_step,
            "claim_boundary": (
                "Lians measured the configured checks for the current code. This is not proof "
                "of semantic correctness, merge approval, or deployment safety."
            ),
        }
