"""Measure an installed Lians Codex plugin on a controlled Sol memory A/B.

This is deliberately an *installed-plugin* harness.  One persistent
``codex app-server --strict-config`` stdio process loads the normal user config
and enabled plugins; it never uses ``--ignore-user-config`` or
``--dangerously-bypass-hook-trust``.  Before either paid turn, the harness
initializes JSON-RPC and verifies the installed-cache Lians hooks for both arm
working directories.  The baseline project has an empty project-scoped memory
database and receives the complete LOCOMO conversation.  The candidate project
is seeded through the installed plugin's frozen runtime and receives only the
question.  A model-free, already-trusted ``UserPromptSubmit`` hook must inject
non-degraded memory for the candidate.

Live execution is opt-in.  The default is a paid-call-free dry run.  Use
``--live --order ba`` for the strict two-call candidate-then-baseline suite.
Reported Sol credits are estimates derived from Codex token telemetry, not a
provider-reported quota debit, and the verdict applies only to this workload.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "agentmem" / "benchmarks" / "data" / "locomo10.json"
FROZEN_CASE: Mapping[str, Any] = {
    "question_id": "conv0_q0",
    "conversation_idx": 0,
    "question": "When did Caroline go to the LGBTQ support group?",
    "ground_truth_answer": "7 May 2023",
    "evidence": ("D1:3",),
}

PLUGIN_ID = "lians-memory@lians"
MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "ultra"
SERVICE_TIER = "default"
MAX_CONTEXT_TOKENS = 768
HOOK_MIN_SCORE = 0.45
TARGET_USAGE_EXTENSION_PERCENT = 80.0
TARGET_USAGE_MULTIPLIER = 1.8
DEFAULT_SUITE_CREDIT_CAP = 10.0
SOL_CREDIT_RATES = {
    "uncached_input_credits_per_million": 125.0,
    "cached_input_credits_per_million": 12.5,
    "cache_write_input_credits_per_million": 156.25,
    "output_credits_per_million": 750.0,
}

EXECUTION_ORDERS: dict[str, tuple[tuple[str, int], ...]] = {
    "ba": (("candidate", 1), ("baseline", 1)),
}

APP_SERVER_CLIENT_NAME = "lians_installed_plugin_ab"
APP_SERVER_CLIENT_TITLE = "Lians Installed Plugin A/B"
APP_SERVER_CLIENT_VERSION = "1.0.0"
EXPECTED_MODEL_PROVIDER = "openai"
EXPECTED_HOOK_EVENTS = ("sessionStart", "userPromptSubmit")
_RPC_EOF = object()

_DATE_FORMATS = (
    "%I:%M %p on %d %B, %Y",
    "%H:%M on %d %B, %Y",
    "%d %B, %Y",
)


class BenchmarkError(RuntimeError):
    """Raised when a run cannot produce trustworthy comparable accounting."""


@dataclass(frozen=True)
class Invocation:
    stdout: bytes
    stderr: bytes
    returncode: int
    wall_time_ms: float
    thread_start_wall_time_ms: float | None = None
    turn_wall_time_ms: float | None = None


@dataclass(frozen=True)
class BenchmarkConfig:
    codex_exe: Path
    dataset_file: Path = DEFAULT_DATASET
    order: str = "ba"
    timeout_seconds: float = 300.0
    max_suite_estimated_credits: float = DEFAULT_SUITE_CREDIT_CAP
    raw_dir: Path | None = None


@dataclass(frozen=True)
class InstalledPlugin:
    plugin_id: str
    version: str
    root: Path
    manifest_sha256: str
    bootstrap_sha256: str
    hook_sha256: str


@dataclass(frozen=True)
class PreparedProjects:
    baseline_root: Path
    candidate_root: Path
    baseline_receipt: Path
    candidate_receipt: Path
    baseline_db: Path
    candidate_db: Path
    seed_report: Mapping[str, Any]
    data_home: Path


@dataclass(frozen=True)
class RunSpec:
    sequence: int
    mode: str
    repetition: int
    cwd: Path
    prompt: str
    receipt_path: Path
    receipt_offset: int
    timeout_seconds: float

    @property
    def label(self) -> str:
        arm = "B" if self.mode == "candidate" else "A"
        return f"{self.sequence:02d}-{self.mode}-{arm}{self.repetition}"


PluginDiscoverer = Callable[[Path], InstalledPlugin]
ProjectPreparer = Callable[
    [InstalledPlugin, Path, Path, Sequence[Mapping[str, Any]]], PreparedProjects
]


class AppServerClient(Protocol):
    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
        events: list[dict[str, Any]],
    ) -> Mapping[str, Any]: ...

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None: ...

    def read(self, *, timeout_seconds: float) -> dict[str, Any]: ...

    def stderr_bytes(self) -> bytes: ...

    def close(self) -> None: ...


AppServerFactory = Callable[[BenchmarkConfig], AppServerClient]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _full_conversation(conversation: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, session in conversation.items():
        if re.fullmatch(r"session_\d+", key) is None or not isinstance(session, list):
            continue
        parts.append(f"SESSION ({conversation.get(f'{key}_date_time', '')}):")
        for turn in session:
            if not isinstance(turn, Mapping):
                continue
            parts.append(f"{turn.get('speaker', '')}: {turn.get('text', '')}")
    if not parts:
        raise BenchmarkError("LOCOMO conversation contains no session turns")
    return "\n".join(parts)


def _decode(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchmarkError(f"{label} was not UTF-8") from exc


def _parse_events(stdout: bytes, label: str) -> list[dict[str, Any]]:
    text = _decode(stdout, f"{label} stdout")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"{label} stdout line {line_number} was not JSON") from exc
        if not isinstance(event, dict):
            raise BenchmarkError(f"{label} event {line_number} was not an object")
        events.append(event)
    if not events:
        raise BenchmarkError(f"{label} returned no JSONL events")
    return events


def estimate_sol_credits(usage: Mapping[str, int]) -> float:
    total = (
        usage["uncached_input_tokens"] * SOL_CREDIT_RATES["uncached_input_credits_per_million"]
        + usage["cached_input_tokens"] * SOL_CREDIT_RATES["cached_input_credits_per_million"]
        + usage["cache_write_input_tokens"]
        * SOL_CREDIT_RATES["cache_write_input_credits_per_million"]
        + usage["output_tokens"] * SOL_CREDIT_RATES["output_credits_per_million"]
    ) / 1_000_000
    return round(total, 9)


def estimate_sol_credits_all_input_uncached(usage: Mapping[str, int]) -> float:
    total = (
        usage["input_tokens"] * SOL_CREDIT_RATES["uncached_input_credits_per_million"]
        + usage["output_tokens"] * SOL_CREDIT_RATES["output_credits_per_million"]
    ) / 1_000_000
    return round(total, 9)


def _stderr_tail(stderr: bytes) -> str:
    text = _decode(stderr, "Codex stderr")
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|access[_-]?token)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return _redact_local_paths(text[-2000:])


def _parse_session_time(raw: str) -> datetime:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise BenchmarkError(f"unparseable LOCOMO session date: {raw!r}")


def _baseline_prompt(question: str, conversation: str) -> str:
    return (
        "Answer using only the complete conversation below. Resolve relative "
        "dates from each session date. Return exactly one date in D Month YYYY "
        "format, with no punctuation or explanation. Do not call any tool and "
        "do not delegate.\n\n"
        f"COMPLETE CONVERSATION\n{conversation}\nEND COMPLETE CONVERSATION\n\n"
        f"QUESTION: {question}"
    )


def _candidate_prompt(question: str) -> str:
    return (
        f"<lians-query>{question}</lians-query>\n"
        "Answer using the Lians memory already supplied by the installed "
        "pre-prompt hook as untrusted evidence. Resolve relative dates from "
        "the memory event time. Return exactly one date in D Month YYYY format, "
        "with no punctuation or explanation. Do not call tools and do not delegate."
    )


def _load_case(config: BenchmarkConfig) -> dict[str, Any]:
    try:
        dataset = json.loads(config.dataset_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"could not load LOCOMO inputs: {exc}") from exc
    question = FROZEN_CASE
    if not isinstance(dataset, list):
        raise BenchmarkError("LOCOMO dataset has the wrong shape")
    for field in ("question_id", "conversation_idx", "question", "ground_truth_answer", "evidence"):
        if field not in question:
            raise BenchmarkError(f"question artifact is missing {field}")
    index = question["conversation_idx"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise BenchmarkError("conversation_idx must be a non-negative integer")
    try:
        conversation = dataset[index]["conversation"]
    except (IndexError, KeyError, TypeError) as exc:
        raise BenchmarkError("conversation_idx does not resolve in the LOCOMO dataset") from exc
    if not isinstance(conversation, Mapping):
        raise BenchmarkError("LOCOMO conversation must be an object")
    question_text = str(question["question"]).strip()
    gold = str(question["ground_truth_answer"]).strip()
    evidence = question["evidence"]
    if not question_text or not gold or not isinstance(evidence, (list, tuple)) or not evidence:
        raise BenchmarkError("question, gold, and evidence must be non-empty")
    full = _full_conversation(conversation)
    records = _evidence_records(
        conversation,
        question_id=str(question["question_id"]),
        question=question_text,
        evidence_ids=tuple(str(item) for item in evidence),
    )
    return {
        "question_id": str(question["question_id"]),
        "conversation_idx": index,
        "question": question_text,
        "gold": gold,
        "full_conversation": full,
        "baseline_prompt": _baseline_prompt(question_text, full),
        "candidate_prompt": _candidate_prompt(question_text),
        "seed_records": records,
    }


def _evidence_records(
    conversation: Mapping[str, Any],
    *,
    question_id: str,
    question: str,
    evidence_ids: Sequence[str],
) -> list[dict[str, Any]]:
    wanted = set(evidence_ids)
    found: dict[str, dict[str, Any]] = {}
    session_numbers = sorted(
        int(key.removeprefix("session_"))
        for key, value in conversation.items()
        if re.fullmatch(r"session_\d+", key) and isinstance(value, list)
    )
    for number in session_numbers:
        turns = conversation[f"session_{number}"]
        when = _parse_session_time(str(conversation[f"session_{number}_date_time"]))
        for offset, turn in enumerate(turns):
            if not isinstance(turn, Mapping):
                continue
            dia_id = str(turn.get("dia_id", ""))
            if dia_id not in wanted:
                continue
            text = " ".join(str(turn.get("text", "")).split())
            speaker = " ".join(str(turn.get("speaker", "")).split())
            if not text:
                raise BenchmarkError(f"LOCOMO evidence {dia_id} has no text")
            # Including the exact question makes this tiny evidence-only store
            # score robustly above the installed hook's relevance threshold;
            # the answer is not copied into the memory.
            content = f"Evidence for {question!r}: {speaker}: {text}"
            found[dia_id] = {
                "content": content,
                "event_time": (when + timedelta(seconds=offset)).isoformat(),
                "source": f"locomo:{question_id}:{dia_id}",
                "metadata": {"dia_id": dia_id, "question_id": question_id},
            }
    missing = [item for item in evidence_ids if item not in found]
    if missing:
        raise BenchmarkError(f"LOCOMO evidence ids were not found: {missing}")
    if len(found) > 8:
        raise BenchmarkError("refusing to seed more than 8 evidence memories")
    return [found[item] for item in evidence_ids]


def _plugin_from_document(document: Mapping[str, Any]) -> InstalledPlugin:
    installed = document.get("installed")
    if not isinstance(installed, list):
        raise BenchmarkError("`codex plugin list --json` omitted installed plugins")
    matches = [
        item
        for item in installed
        if isinstance(item, Mapping) and item.get("pluginId") == PLUGIN_ID
    ]
    if len(matches) != 1:
        raise BenchmarkError(f"expected one installed {PLUGIN_ID}; found {len(matches)}")
    item = matches[0]
    if item.get("installed") is not True or item.get("enabled") is not True:
        raise BenchmarkError(f"{PLUGIN_ID} must be installed and enabled")
    source = item.get("source")
    root_value = source.get("path") if isinstance(source, Mapping) else None
    if not isinstance(root_value, str) or not root_value:
        raise BenchmarkError(f"{PLUGIN_ID} has no installed source path")
    source_root = Path(root_value).expanduser().resolve()
    root = source_root
    marketplace_name = item.get("marketplaceName")
    plugin_name = item.get("name")
    version = item.get("version")
    if all(isinstance(value, str) and value for value in (marketplace_name, plugin_name, version)):
        homes: list[Path] = []
        configured_home = os.environ.get("CODEX_HOME")
        if configured_home:
            homes.append(Path(configured_home).expanduser())
        homes.append(Path.home() / ".codex")
        for home in homes:
            candidate = (
                home / "plugins" / "cache" / str(marketplace_name) / str(plugin_name) / str(version)
            )
            if (candidate / ".codex-plugin" / "plugin.json").is_file():
                root = candidate.resolve()
                break
    manifest = root / ".codex-plugin" / "plugin.json"
    bootstrap = root / "scripts" / "bootstrap.py"
    hook = root / "runtime" / "user_prompt_submit_recall.py"
    for path, label in ((manifest, "manifest"), (bootstrap, "bootstrap"), (hook, "hook runtime")):
        if not path.is_file():
            raise BenchmarkError(f"installed plugin {label} is missing: {path}")
    try:
        manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("installed plugin manifest is invalid") from exc
    if manifest_doc.get("name") != "lians-memory" or manifest_doc.get("version") != item.get(
        "version"
    ):
        raise BenchmarkError("installed plugin manifest does not match Codex plugin inventory")
    return InstalledPlugin(
        plugin_id=PLUGIN_ID,
        version=str(item["version"]),
        root=root,
        manifest_sha256=_sha256_file(manifest),
        bootstrap_sha256=_sha256_file(bootstrap),
        hook_sha256=_sha256_file(hook),
    )


def discover_installed_plugin(codex_exe: Path) -> InstalledPlugin:
    try:
        result = subprocess.run(
            [str(codex_exe), "plugin", "list", "--json"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError(f"could not inspect installed Codex plugins: {exc}") from exc
    if result.returncode != 0:
        raise BenchmarkError(f"Codex plugin inventory failed: {_stderr_tail(result.stderr)}")
    try:
        document = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("Codex plugin inventory was not valid JSON") from exc
    if not isinstance(document, Mapping):
        raise BenchmarkError("Codex plugin inventory was not an object")
    return _plugin_from_document(document)


def _load_bootstrap(plugin: InstalledPlugin) -> Any:
    path = plugin.root / "scripts" / "bootstrap.py"
    name = f"_lians_installed_bootstrap_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BenchmarkError(f"could not load installed plugin bootstrap: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise BenchmarkError(f"installed plugin bootstrap failed to load: {exc}") from exc
    return module


_SEED_PROGRAM = r"""
import json, os, sqlite3, sys
from datetime import datetime
from lians import LocalLiansClient

payload = json.loads(sys.stdin.read())
items = []
for record in payload["records"]:
    items.append({
        "content": record["content"],
        "event_time": datetime.fromisoformat(record["event_time"]),
        "source": record["source"],
        "subject_id": payload["subject_id"],
        "metadata": record["metadata"],
        "importance": 1.0,
    })
with LocalLiansClient(
    db_path=payload["db_path"],
    namespace=payload["namespace"],
    embedding_provider="bge-onnx",
) as client:
    created = client.add_batch(payload["agent_id"], items)
print(json.dumps({
    "created_count": len(created),
    "created_subject_ids": sorted({item.get("subject_id") for item in created}),
    "embedding_provider": os.environ.get("EMBEDDING_PROVIDER"),
    "bge_artifact_dir": os.environ.get("BGE_ONNX_ARTIFACT_DIR"),
    "unencrypted_allowed": os.environ.get("AGENTMEM_ALLOW_UNENCRYPTED"),
}, sort_keys=True))
"""


def _project_binding(
    bootstrap: Any, plugin: InstalledPlugin, project_root: Path
) -> tuple[Any, Path, dict[str, str]]:
    try:
        data_home = bootstrap.resolve_data_home()
        profile = bootstrap.read_profile(data_home)
        bootstrap.verify_profile_matches_bundle(profile, plugin_root=plugin.root)
        if profile.get("mode") != "local":
            raise BenchmarkError("installed-plugin benchmark requires the local encrypted profile")
        artifact = profile.get("bge_artifact_dir")
        if not isinstance(artifact, str) or not bootstrap.validate_bge_artifact_directory(
            Path(artifact)
        ):
            raise BenchmarkError("installed plugin profile lacks the verified BGE artifact")
        child = bootstrap.configure_runtime_environment(
            data_home,
            profile,
            os.environ,
            project_root=project_root,
            require_managed_key=True,
        )
    except BenchmarkError:
        raise
    except Exception as exc:
        raise BenchmarkError(f"installed plugin is not ready: {exc}") from exc
    return bootstrap, Path(data_home), child


def _protected_subject_reference(value: Any, *, raw_subject_id: str) -> str:
    prefix = "lians:subject:v2:hmac-sha256:"
    if (
        not isinstance(value, str)
        or value == raw_subject_id
        or not value.startswith(prefix)
        or re.fullmatch(rf"{re.escape(prefix)}[0-9a-f]{{64}}:[0-9a-f]{{64}}", value) is None
    ):
        raise BenchmarkError("seeded SQLite subject was not a protected stable reference")
    return value


def _seed_candidate(
    bootstrap: Any,
    data_home: Path,
    child: Mapping[str, str],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    db = Path(child["LIANS_LOCAL_DB"])
    if db.exists():
        raise BenchmarkError(f"candidate project memory is not fresh: {db}")
    scope = bootstrap.project_scope(child["LIANS_MCP_PROJECT_ROOT"])
    namespace = f"mcp-{scope}"
    agent_id = f"mcp-{scope}"
    subject_id = f"codex-project:{scope}"
    payload = {
        "records": list(records),
        "db_path": str(db),
        "namespace": namespace,
        "agent_id": agent_id,
        "subject_id": subject_id,
    }
    python = Path(bootstrap.runtime_python(data_home))
    if not python.is_file():
        raise BenchmarkError("installed plugin frozen runtime is missing")
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", _SEED_PROGRAM],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=300,
            cwd=child["LIANS_PLUGIN_RUNTIME_CWD"],
            env=dict(child),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError(f"installed-runtime seed failed: {exc}") from exc
    if result.returncode != 0:
        raise BenchmarkError(f"installed-runtime seed failed: {_stderr_tail(result.stderr)}")
    try:
        reported = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("installed-runtime seed returned invalid JSON") from exc
    if reported.get("created_count") != len(records):
        raise BenchmarkError("installed-runtime seed did not create every evidence memory")
    if reported.get("created_subject_ids") != [subject_id]:
        raise BenchmarkError("installed-runtime seed was not project-subject scoped")
    if reported.get("embedding_provider") != "bge-onnx":
        raise BenchmarkError("installed-runtime seed did not use BGE ONNX")
    if reported.get("unencrypted_allowed") != "false":
        raise BenchmarkError("installed-runtime seed did not enforce encryption")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*), SUM(content_encrypted IS NOT NULL), "
            "COUNT(DISTINCT subject_id), MIN(subject_id), MAX(subject_id) "
            "FROM memories WHERE namespace = ? AND agent_id = ?",
            (namespace, agent_id),
        ).fetchone()
    if row is None or row[:3] != (len(records), len(records), 1) or row[3] != row[4]:
        raise BenchmarkError("seeded SQLite rows did not prove encrypted project scoping")
    stored_subject = _protected_subject_reference(row[3], raw_subject_id=subject_id)
    database_bytes = db.read_bytes()
    if any(str(record["content"]).encode("utf-8") in database_bytes for record in records):
        raise BenchmarkError("seeded memory plaintext was found in the project database")
    return {
        "record_count": len(records),
        "evidence_ids": [record["metadata"]["dia_id"] for record in records],
        "namespace": namespace,
        "agent_id": agent_id,
        "subject_reference_protected": True,
        "stored_subject_reference_sha256": _sha256_bytes(stored_subject.encode("utf-8")),
        "database_sha256": _sha256_file(db),
        "encrypted_rows": len(records),
        "plaintext_absent": True,
        "embedding_provider": "bge-onnx",
        "bge_artifact_sha256": hashlib.sha256(
            str(reported["bge_artifact_dir"]).encode()
        ).hexdigest(),
        "seed_wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def prepare_projects(
    plugin: InstalledPlugin,
    baseline_root: Path,
    candidate_root: Path,
    records: Sequence[Mapping[str, Any]],
) -> PreparedProjects:
    bootstrap = _load_bootstrap(plugin)
    bootstrap, data_home, baseline_env = _project_binding(bootstrap, plugin, baseline_root)
    _, candidate_data_home, candidate_env = _project_binding(bootstrap, plugin, candidate_root)
    if candidate_data_home != data_home:
        raise BenchmarkError("baseline and candidate resolved different plugin data homes")
    baseline_db = Path(baseline_env["LIANS_LOCAL_DB"])
    candidate_db = Path(candidate_env["LIANS_LOCAL_DB"])
    if baseline_db.exists():
        raise BenchmarkError(f"baseline project memory is not empty/fresh: {baseline_db}")
    for receipt in (
        Path(baseline_env["LIANS_CODEX_HOOK_RECEIPT"]),
        Path(candidate_env["LIANS_CODEX_HOOK_RECEIPT"]),
    ):
        if receipt.exists():
            raise BenchmarkError(f"project hook receipt is not fresh: {receipt}")
    seed_report = _seed_candidate(bootstrap, data_home, candidate_env, records)
    return PreparedProjects(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        baseline_receipt=Path(baseline_env["LIANS_CODEX_HOOK_RECEIPT"]),
        candidate_receipt=Path(candidate_env["LIANS_CODEX_HOOK_RECEIPT"]),
        baseline_db=baseline_db,
        candidate_db=candidate_db,
        seed_report=seed_report,
        data_home=data_home,
    )


def _app_server_command(config: BenchmarkConfig) -> tuple[str, ...]:
    command = (str(config.codex_exe), "app-server", "--strict-config")
    forbidden = {"--ignore-user-config", "--dangerously-bypass-hook-trust"}
    if forbidden.intersection(command):  # pragma: no cover - invariant
        raise AssertionError("installed-plugin harness contains a forbidden isolation flag")
    return command


def _thread_start_params(spec: RunSpec) -> dict[str, Any]:
    return {
        "model": MODEL,
        "modelProvider": EXPECTED_MODEL_PROVIDER,
        "cwd": str(spec.cwd),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "ephemeral": True,
        "experimentalRawEvents": True,
        "sessionStartSource": "startup",
        "serviceTier": SERVICE_TIER,
        "serviceName": APP_SERVER_CLIENT_NAME,
        "config": {
            "model_reasoning_effort": REASONING_EFFORT,
            "service_tier": SERVICE_TIER,
        },
    }


def _turn_start_params(spec: RunSpec, thread_id: str) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": spec.prompt}],
        "clientUserMessageId": (
            f"lians-ab-{spec.label}-{_sha256_bytes(spec.prompt.encode('utf-8'))[:12]}"
        ),
        "model": MODEL,
        "effort": REASONING_EFFORT,
        "serviceTier": SERVICE_TIER,
        "cwd": str(spec.cwd),
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
    }


def _make_spec(
    config: BenchmarkConfig,
    prepared: PreparedProjects,
    *,
    sequence: int,
    mode: str,
    repetition: int,
    prompt: str,
) -> RunSpec:
    root = prepared.candidate_root if mode == "candidate" else prepared.baseline_root
    receipt = prepared.candidate_receipt if mode == "candidate" else prepared.baseline_receipt
    offset = receipt.stat().st_size if receipt.exists() else 0
    return RunSpec(
        sequence=sequence,
        mode=mode,
        repetition=repetition,
        cwd=root,
        prompt=prompt,
        receipt_path=receipt,
        receipt_offset=offset,
        timeout_seconds=config.timeout_seconds,
    )


class _StdioAppServer:
    def __init__(self, config: BenchmarkConfig) -> None:
        self._command = _app_server_command(config)
        try:
            self._process = subprocess.Popen(
                list(self._command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=os.environ.copy(),
            )
        except OSError as exc:
            raise BenchmarkError(f"could not start Codex app-server: {exc}") from exc
        self._stdout: queue.Queue[bytes | object] = queue.Queue()
        self._stderr: list[bytes] = []
        self._stderr_lock = threading.Lock()
        self._next_request_id = 1
        self._closed = False
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        assert stream is not None  # Popen contract above
        for line in iter(stream.readline, b""):
            self._stdout.put(line)
        self._stdout.put(_RPC_EOF)

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        assert stream is not None  # Popen contract above
        for line in iter(stream.readline, b""):
            with self._stderr_lock:
                self._stderr.append(line)

    def _send(self, document: Mapping[str, Any]) -> None:
        if self._closed or self._process.poll() is not None:
            raise BenchmarkError(
                "Codex app-server exited before the JSON-RPC request: "
                f"{_stderr_tail(self.stderr_bytes())}"
            )
        stream = self._process.stdin
        if stream is None:  # pragma: no cover - Popen invariant
            raise BenchmarkError("Codex app-server stdin is unavailable")
        payload = (json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        try:
            stream.write(payload)
            stream.flush()
        except (BrokenPipeError, OSError) as exc:
            raise BenchmarkError(
                "Codex app-server rejected the JSON-RPC request: "
                f"{_stderr_tail(self.stderr_bytes())}"
            ) from exc

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
        events: list[dict[str, Any]],
    ) -> Mapping[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send({"method": method, "id": request_id, "params": dict(params)})
        deadline = time.monotonic() + timeout_seconds
        while True:
            message = self.read(timeout_seconds=max(0.001, deadline - time.monotonic()))
            if message.get("id") == request_id:
                error = message.get("error")
                if error is not None:
                    raise BenchmarkError(f"Codex app-server {method} failed: {error}")
                result = message.get("result")
                if not isinstance(result, Mapping):
                    raise BenchmarkError(f"Codex app-server {method} returned no object result")
                return result
            if "id" in message:
                raise BenchmarkError(
                    f"Codex app-server returned an unexpected response id during {method}"
                )
            events.append(message)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        document: dict[str, Any] = {"method": method}
        if params is not None:
            document["params"] = dict(params)
        self._send(document)

    def read(self, *, timeout_seconds: float) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise BenchmarkError("Codex app-server response timed out")
        try:
            raw = self._stdout.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise BenchmarkError("Codex app-server response timed out") from exc
        if raw is _RPC_EOF:
            raise BenchmarkError(
                f"Codex app-server closed stdout unexpectedly: {_stderr_tail(self.stderr_bytes())}"
            )
        assert isinstance(raw, bytes)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BenchmarkError("Codex app-server emitted invalid JSONL") from exc
        if not isinstance(message, dict):
            raise BenchmarkError("Codex app-server emitted a non-object JSON-RPC message")
        return message

    def stderr_bytes(self) -> bytes:
        with self._stderr_lock:
            return b"".join(self._stderr)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
                self._process.kill()
                self._process.wait(timeout=2)


def open_app_server(config: BenchmarkConfig) -> AppServerClient:
    return _StdioAppServer(config)


def _path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _validate_hook_preflight(
    result: Mapping[str, Any],
    *,
    cwds: Sequence[Path],
    plugin: InstalledPlugin,
) -> dict[str, Any]:
    data = result.get("data")
    if not isinstance(data, list) or len(data) != len(cwds):
        raise BenchmarkError(
            "hooks/list did not return exactly one entry for each A/B working directory"
        )
    expected_by_key = {_path_key(path): path for path in cwds}
    entries: dict[str, Mapping[str, Any]] = {}
    for item in data:
        if not isinstance(item, Mapping) or not isinstance(item.get("cwd"), str):
            raise BenchmarkError("hooks/list returned an invalid working-directory entry")
        key = _path_key(str(item["cwd"]))
        if key not in expected_by_key or key in entries:
            raise BenchmarkError("hooks/list returned an unexpected or duplicate working directory")
        entries[key] = item
    if set(entries) != set(expected_by_key):
        raise BenchmarkError("hooks/list omitted an A/B working directory")

    expected_source = _path_key(plugin.root / "hooks" / "hooks.json")
    expected_key_suffix = {
        "sessionStart": ":hooks/hooks.json:session_start:0:0",
        "userPromptSubmit": ":hooks/hooks.json:user_prompt_submit:0:0",
    }
    hashes_by_event: dict[str, set[str]] = {event: set() for event in EXPECTED_HOOK_EVENTS}
    for cwd_key in expected_by_key:
        entry = entries[cwd_key]
        if entry.get("warnings") != [] or entry.get("errors") != []:
            raise BenchmarkError(
                "hooks/list reported warnings or errors for an A/B working directory"
            )
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            raise BenchmarkError("hooks/list omitted hook metadata")
        lians = [
            hook
            for hook in hooks
            if isinstance(hook, Mapping) and hook.get("pluginId") == PLUGIN_ID
        ]
        if len(lians) != 2:
            raise BenchmarkError(f"expected exactly two installed {PLUGIN_ID} handlers per arm")
        by_event = {str(hook.get("eventName")): hook for hook in lians}
        if set(by_event) != set(EXPECTED_HOOK_EVENTS):
            raise BenchmarkError(
                "installed Lians handlers were not exactly SessionStart and UserPromptSubmit"
            )
        for event_name, hook in by_event.items():
            if hook.get("enabled") is not True or hook.get("trustStatus") != "trusted":
                raise BenchmarkError(
                    f"installed Lians {event_name} handler is not enabled and trusted"
                )
            if hook.get("source") != "plugin" or hook.get("handlerType") != "command":
                raise BenchmarkError(
                    f"installed Lians {event_name} handler has the wrong source or type"
                )
            source_path = hook.get("sourcePath")
            if not isinstance(source_path, str) or _path_key(source_path) != expected_source:
                raise BenchmarkError(
                    f"installed Lians {event_name} handler is not from the measured cache root"
                )
            key = hook.get("key")
            if (
                not isinstance(key, str)
                or not key.startswith(f"{PLUGIN_ID}:")
                or not key.endswith(expected_key_suffix[event_name])
            ):
                raise BenchmarkError(f"installed Lians {event_name} handler key is unexpected")
            command = hook.get("command")
            timeout = hook.get("timeoutSec")
            if not isinstance(command, str) or not command.strip():
                raise BenchmarkError(f"installed Lians {event_name} command is missing")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
                raise BenchmarkError(f"installed Lians {event_name} timeout is invalid")
            current_hash = hook.get("currentHash")
            if (
                not isinstance(current_hash, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash) is None
            ):
                raise BenchmarkError(f"installed Lians {event_name} hash is invalid")
            hashes_by_event[event_name].add(current_hash)
        if by_event["sessionStart"].get("matcher") != "^(startup|resume|clear)$":
            raise BenchmarkError("installed Lians SessionStart matcher is unexpected")
        if by_event["userPromptSubmit"].get("matcher") is not None:
            raise BenchmarkError("installed Lians UserPromptSubmit matcher is unexpected")
        if by_event["userPromptSubmit"].get("additionalContextLimit") != MAX_CONTEXT_TOKENS:
            raise BenchmarkError("installed Lians UserPromptSubmit context limit is unexpected")

    if any(len(values) != 1 for values in hashes_by_event.values()):
        raise BenchmarkError("A/B arms resolved different installed Lians hook definitions")
    return {
        "passed": True,
        "checked_before_paid_turns": True,
        "working_directory_count": len(cwds),
        "lians_handlers_per_working_directory": 2,
        "warnings": 0,
        "errors": 0,
        "all_enabled": True,
        "all_trusted": True,
        "source": "measured_installed_plugin_cache",
        "handler_hashes": {
            event: next(iter(hashes_by_event[event])) for event in EXPECTED_HOOK_EVENTS
        },
    }


def _preflight_app_server(
    client: AppServerClient,
    *,
    prepared: PreparedProjects,
    plugin: InstalledPlugin,
    timeout_seconds: float,
) -> dict[str, Any]:
    ignored: list[dict[str, Any]] = []
    initialized = client.request(
        "initialize",
        {
            "clientInfo": {
                "name": APP_SERVER_CLIENT_NAME,
                "title": APP_SERVER_CLIENT_TITLE,
                "version": APP_SERVER_CLIENT_VERSION,
            },
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
            },
        },
        timeout_seconds=timeout_seconds,
        events=ignored,
    )
    if not isinstance(initialized.get("userAgent"), str):
        raise BenchmarkError("Codex app-server initialize omitted its user agent")
    client.notify("initialized")
    cwds = (prepared.baseline_root.resolve(), prepared.candidate_root.resolve())
    hooks = client.request(
        "hooks/list",
        {"cwds": [str(path) for path in cwds]},
        timeout_seconds=timeout_seconds,
        events=ignored,
    )
    result = _validate_hook_preflight(hooks, cwds=cwds, plugin=plugin)
    session_dispatch: list[dict[str, Any]] = []
    for index, cwd in enumerate(cwds, start=1):
        events: list[dict[str, Any]] = []
        probe = RunSpec(
            sequence=index,
            mode="baseline" if index == 1 else "candidate",
            repetition=0,
            cwd=cwd,
            prompt="",
            receipt_path=prepared.baseline_receipt,
            receipt_offset=0,
            timeout_seconds=timeout_seconds,
        )
        thread = client.request(
            "thread/start",
            _thread_start_params(probe),
            timeout_seconds=timeout_seconds,
            events=events,
        )
        thread_id = _validate_thread_start(thread, spec=probe)
        session_dispatch.append(
            _validate_session_start_dispatch(
                events,
                thread_id=thread_id,
                plugin=plugin,
            )
        )
    result["session_start_dispatch"] = session_dispatch
    result["model_calls_during_preflight"] = 0
    return result


def _validate_session_start_dispatch(
    events: Sequence[Mapping[str, Any]],
    *,
    thread_id: str,
    plugin: InstalledPlugin,
) -> dict[str, Any]:
    """Require the installed SessionStart command to actually run before spend."""

    expected_source = _path_key(plugin.root / "hooks" / "hooks.json")
    matched: dict[str, list[Mapping[str, Any]]] = {
        "hook/started": [],
        "hook/completed": [],
    }
    for event in events:
        method = event.get("method")
        if method not in matched:
            continue
        params = event.get("params")
        if not isinstance(params, Mapping) or params.get("threadId") != thread_id:
            continue
        run = params.get("run")
        if not isinstance(run, Mapping):
            continue
        if run.get("eventName") != "SessionStart":
            continue
        if not isinstance(run.get("sourcePath"), str):
            continue
        if _path_key(str(run["sourcePath"])) != expected_source:
            continue
        matched[str(method)].append(run)
    if any(len(values) != 1 for values in matched.values()):
        raise BenchmarkError(
            "app-server did not dispatch exactly one installed Lians SessionStart hook; "
            "no paid turn will run"
        )
    started = matched["hook/started"][0]
    completed = matched["hook/completed"][0]
    if started.get("id") != completed.get("id"):
        raise BenchmarkError("SessionStart hook notifications did not identify the same run")
    if completed.get("status") != "completed":
        raise BenchmarkError("installed Lians SessionStart hook did not complete successfully")
    if completed.get("source") != "plugin" or completed.get("handlerType") != "command":
        raise BenchmarkError("SessionStart was not dispatched from the installed plugin command")
    return {
        "thread_id_sha256": _sha256_bytes(thread_id.encode("utf-8")),
        "run_id_sha256": _sha256_bytes(str(completed["id"]).encode("utf-8")),
        "status": "completed",
        "source": "installed_plugin_cache",
        "duration_ms": completed.get("durationMs"),
    }


def _validate_thread_start(
    result: Mapping[str, Any],
    *,
    spec: RunSpec,
) -> str:
    if result.get("model") != MODEL or result.get("modelProvider") != EXPECTED_MODEL_PROVIDER:
        raise BenchmarkError(f"{spec.label} app-server rerouted the requested model or provider")
    if result.get("reasoningEffort") != REASONING_EFFORT:
        raise BenchmarkError(f"{spec.label} app-server did not pin Ultra reasoning")
    if result.get("serviceTier") != SERVICE_TIER:
        raise BenchmarkError(f"{spec.label} app-server did not pin the default service tier")
    if not isinstance(result.get("cwd"), str) or _path_key(str(result["cwd"])) != _path_key(
        spec.cwd
    ):
        raise BenchmarkError(f"{spec.label} app-server changed the working directory")
    if result.get("approvalPolicy") != "never":
        raise BenchmarkError(f"{spec.label} app-server changed the approval policy")
    sandbox = result.get("sandbox")
    if not isinstance(sandbox, Mapping) or sandbox.get("type") != "readOnly":
        raise BenchmarkError(f"{spec.label} app-server changed the read-only sandbox")
    thread = result.get("thread")
    if not isinstance(thread, Mapping) or not isinstance(thread.get("id"), str):
        raise BenchmarkError(f"{spec.label} thread/start returned no thread id")
    if (
        thread.get("ephemeral") is not True
        or thread.get("modelProvider") != EXPECTED_MODEL_PROVIDER
    ):
        raise BenchmarkError(
            f"{spec.label} thread/start did not create the requested ephemeral OpenAI thread"
        )
    return str(thread["id"])


def _matching_turn_completed(message: Mapping[str, Any], thread_id: str, turn_id: str) -> bool:
    if message.get("method") != "turn/completed":
        return False
    params = message.get("params")
    if not isinstance(params, Mapping) or params.get("threadId") != thread_id:
        return False
    turn = params.get("turn")
    return isinstance(turn, Mapping) and turn.get("id") == turn_id


def _artifact_event(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Retain only bounded, path-free evidence needed to reproduce a verdict."""

    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not isinstance(params, Mapping):
        return None
    identity = {
        key: params[key] for key in ("threadId", "turnId") if isinstance(params.get(key), str)
    }
    if method == "rawResponse/completed":
        usage = params.get("usage")
        return {
            "method": method,
            "params": {
                **identity,
                "responseId": params.get("responseId"),
                "usage": dict(usage) if isinstance(usage, Mapping) else usage,
            },
        }
    if method in {"item/started", "item/completed", "rawResponseItem/completed"}:
        item = params.get("item")
        if not isinstance(item, Mapping):
            return None
        item_type = str(item.get("type", ""))
        safe_item: dict[str, Any] = {
            key: item[key]
            for key in (
                "id",
                "call_id",
                "type",
                "phase",
                "status",
                "server",
                "namespace",
                "tool",
                "name",
            )
            if key in item and isinstance(item[key], (str, int, float, bool, type(None)))
        }
        if item_type == "agentMessage" and isinstance(item.get("text"), str):
            safe_item["text"] = _redact_local_paths(str(item["text"]))
        return {"method": method, "params": {**identity, "item": safe_item}}
    if method == "model/rerouted":
        return {
            "method": method,
            "params": {
                **identity,
                "fromModel": params.get("fromModel"),
                "toModel": params.get("toModel"),
                "reason": params.get("reason"),
            },
        }
    if method == "turn/completed":
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return None
        return {
            "method": method,
            "params": {
                "threadId": params.get("threadId"),
                "turn": {
                    "id": turn.get("id"),
                    "status": turn.get("status"),
                    "error": None if turn.get("error") is None else {"present": True},
                },
            },
        }
    if "subagent" in method.casefold():
        return {"method": method, "params": identity}
    return None


def _redact_local_paths(value: str) -> str:
    value = re.sub(r"(?i)\b[a-z]:[\\/][^\s\"']+", "[LOCAL_PATH]", value)
    value = re.sub(r"(?i)(?:/Users|/home)/[^\s\"']+", "[LOCAL_PATH]", value)
    return value


def _run_app_server_turn(client: AppServerClient, spec: RunSpec) -> Invocation:
    started = time.perf_counter()
    deadline = time.monotonic() + spec.timeout_seconds
    events: list[dict[str, Any]] = []
    stderr_before = len(client.stderr_bytes())

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise BenchmarkError(f"{spec.label} exceeded {spec.timeout_seconds:g} seconds")
        return value

    thread_started = time.perf_counter()
    thread_result = client.request(
        "thread/start",
        _thread_start_params(spec),
        timeout_seconds=remaining(),
        events=events,
    )
    thread_id = _validate_thread_start(thread_result, spec=spec)
    thread_start_wall_time_ms = (time.perf_counter() - thread_started) * 1000
    turn_started = time.perf_counter()
    turn_result = client.request(
        "turn/start",
        _turn_start_params(spec, thread_id),
        timeout_seconds=remaining(),
        events=events,
    )
    turn = turn_result.get("turn")
    if not isinstance(turn, Mapping) or not isinstance(turn.get("id"), str):
        raise BenchmarkError(f"{spec.label} turn/start returned no turn id")
    turn_id = str(turn["id"])
    if turn.get("status") not in {"inProgress", "completed"}:
        raise BenchmarkError(f"{spec.label} turn/start returned an invalid status")
    while not any(_matching_turn_completed(event, thread_id, turn_id) for event in events):
        message = client.read(timeout_seconds=remaining())
        if "id" in message:
            raise BenchmarkError(f"{spec.label} received an unsupported server request")
        events.append(message)
    turn_wall_time_ms = (time.perf_counter() - turn_started) * 1000

    artifact_events = [safe for event in events if (safe := _artifact_event(event)) is not None]
    stdout = b"".join(
        (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for event in artifact_events
    )
    stderr = client.stderr_bytes()
    stderr_delta = stderr[stderr_before:] if len(stderr) >= stderr_before else stderr
    return Invocation(
        stdout=stdout,
        stderr=stderr_delta,
        returncode=0,
        wall_time_ms=(time.perf_counter() - started) * 1000,
        thread_start_wall_time_ms=thread_start_wall_time_ms,
        turn_wall_time_ms=turn_wall_time_ms,
    )


def _turn_identity(events: Sequence[Mapping[str, Any]], label: str) -> tuple[str, str]:
    completed = [event for event in events if event.get("method") == "turn/completed"]
    if len(completed) != 1:
        raise BenchmarkError(f"{label} expected exactly one turn/completed notification")
    params = completed[0].get("params")
    if not isinstance(params, Mapping) or not isinstance(params.get("threadId"), str):
        raise BenchmarkError(f"{label} turn/completed omitted its thread id")
    turn = params.get("turn")
    if not isinstance(turn, Mapping) or not isinstance(turn.get("id"), str):
        raise BenchmarkError(f"{label} turn/completed omitted its turn id")
    if turn.get("status") != "completed" or turn.get("error") is not None:
        raise BenchmarkError(f"{label} Codex turn did not complete successfully")
    return str(params["threadId"]), str(turn["id"])


def _matches_turn(event: Mapping[str, Any], thread_id: str, turn_id: str) -> bool:
    params = event.get("params")
    return (
        isinstance(params, Mapping)
        and params.get("threadId") == thread_id
        and params.get("turnId") == turn_id
    )


def _complete_usage(
    events: Sequence[Mapping[str, Any]],
    label: str,
    *,
    thread_id: str,
    turn_id: str,
) -> tuple[dict[str, int], list[str]]:
    completed = [
        event
        for event in events
        if event.get("method") == "rawResponse/completed"
        and _matches_turn(event, thread_id, turn_id)
    ]
    if not completed:
        raise BenchmarkError(f"{label} reported no matching rawResponse/completed provider usage")
    provider_names = {
        "inputTokens": "input_tokens",
        "cachedInputTokens": "cached_input_tokens",
        "cacheWriteInputTokens": "cache_write_input_tokens",
        "outputTokens": "output_tokens",
        "reasoningOutputTokens": "reasoning_output_tokens",
        "totalTokens": "total_tokens",
    }
    totals = {name: 0 for name in provider_names.values()}
    response_ids: list[str] = []
    for event in completed:
        params = event["params"]
        assert isinstance(params, Mapping)
        response_id = params.get("responseId")
        raw = params.get("usage")
        if not isinstance(response_id, str) or not response_id or response_id in response_ids:
            raise BenchmarkError(f"{label} raw provider response id is missing or duplicated")
        required_usage = set(provider_names) - {"cacheWriteInputTokens"}
        if not isinstance(raw, Mapping) or any(name not in raw for name in required_usage):
            raise BenchmarkError(f"{label} did not report complete raw provider token usage")
        values: dict[str, int] = {}
        for provider_name, report_name in provider_names.items():
            value = (
                raw.get(provider_name, 0)
                if provider_name == "cacheWriteInputTokens"
                else raw[provider_name]
            )
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BenchmarkError(
                    f"{label} raw usage.{provider_name} must be a non-negative integer"
                )
            values[report_name] = value
        components = values["cached_input_tokens"] + values["cache_write_input_tokens"]
        if components > values["input_tokens"]:
            raise BenchmarkError(f"{label} cached/cache-write input exceeds total input")
        if values["reasoning_output_tokens"] > values["output_tokens"]:
            raise BenchmarkError(f"{label} reasoning output exceeds total output")
        if values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
            raise BenchmarkError(f"{label} raw provider total token accounting is inconsistent")
        if values["cache_write_input_tokens"] != 0:
            raise BenchmarkError(
                f"{label} reported cache-write input; the installed-plugin gate "
                "requires zero so its estimate does not depend on an undocumented rate"
            )
        response_ids.append(response_id)
        for name, value in values.items():
            totals[name] += value
    components = totals["cached_input_tokens"] + totals["cache_write_input_tokens"]
    totals["uncached_input_tokens"] = totals["input_tokens"] - components
    return totals, response_ids


def _answer(
    events: Sequence[Mapping[str, Any]],
    label: str,
    *,
    thread_id: str,
    turn_id: str,
) -> str:
    messages: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("method") != "item/completed" or not _matches_turn(event, thread_id, turn_id):
            continue
        params = event.get("params")
        assert isinstance(params, Mapping)
        item = params.get("item")
        if isinstance(item, Mapping) and item.get("type") == "agentMessage":
            messages.append(item)
    if not messages:
        raise BenchmarkError(f"{label} returned no completed agent message")
    final = [item for item in messages if item.get("phase") == "final_answer"]
    selected = final[-1] if final else messages[-1]
    text = selected.get("text")
    if not isinstance(text, str):
        raise BenchmarkError(f"{label} completed agent message omitted text")
    return text.strip()


_APP_SERVER_TOOL_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "webSearch",
    "imageView",
    "imageGeneration",
    "sleep",
}
_RAW_TOOL_ITEM_TYPES = {
    "function_call",
    "local_shell_call",
    "custom_tool_call",
    "tool_search_call",
    "web_search_call",
    "computer_call",
    "file_search_call",
    "code_interpreter_call",
}
_DELEGATION_TOOLS = {
    "spawn_agent",
    "send_message",
    "followup_task",
    "wait_agent",
    "interrupt_agent",
}


def _tool_calls(
    events: Sequence[Mapping[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for event in events:
        method = event.get("method")
        if method not in {"item/started", "item/completed", "rawResponseItem/completed"}:
            continue
        if not _matches_turn(event, thread_id, turn_id):
            continue
        params = event.get("params")
        assert isinstance(params, Mapping)
        item = params.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        is_tool = item_type in _APP_SERVER_TOOL_ITEM_TYPES or item_type in _RAW_TOOL_ITEM_TYPES
        if not is_tool:
            continue
        identity = str(item.get("id") or item.get("call_id") or f"anonymous-{len(calls)}")
        calls[identity] = {
            "kind": item_type,
            "server": item.get("server") or item.get("namespace"),
            "tool": item.get("tool") or item.get("name"),
            "status": item.get("status"),
            "last_event": method,
        }
    return list(calls.values())


def _delegation_evidence(
    events: Sequence[Mapping[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
) -> list[str]:
    evidence: set[str] = set()
    for event in events:
        method = str(event.get("method", ""))
        if "subagent" in method.casefold() and _matches_turn(event, thread_id, turn_id):
            evidence.add(f"event:{method}")
        if method not in {"item/started", "item/completed", "rawResponseItem/completed"}:
            continue
        if not _matches_turn(event, thread_id, turn_id):
            continue
        params = event.get("params")
        assert isinstance(params, Mapping)
        item = params.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        if item_type in {"collabAgentToolCall", "subAgentActivity"}:
            evidence.add(f"item:{item_type}")
        tool = str(item.get("tool") or item.get("name") or "").casefold()
        if tool in _DELEGATION_TOOLS:
            evidence.add(f"tool:{tool}")
    return sorted(evidence)


def _reroute_evidence(
    events: Sequence[Mapping[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for event in events:
        if event.get("method") != "model/rerouted" or not _matches_turn(event, thread_id, turn_id):
            continue
        params = event.get("params")
        assert isinstance(params, Mapping)
        evidence.append(
            {
                "from_model": params.get("fromModel"),
                "to_model": params.get("toModel"),
                "reason": params.get("reason"),
            }
        )
    return evidence


def _receipt_delta(spec: RunSpec) -> dict[str, Any]:
    try:
        with spec.receipt_path.open("rb") as handle:
            handle.seek(spec.receipt_offset)
            raw = handle.read()
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
        if len(lines) != 1:
            raise BenchmarkError(f"{spec.label} emitted {len(lines)} new hook receipts, expected 1")
        receipt = json.loads(lines[0])
    except BenchmarkError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"{spec.label} post-trust hook receipt is missing or invalid") from exc
    if not isinstance(receipt, dict):
        raise BenchmarkError(f"{spec.label} hook receipt was not an object")
    return receipt


def _receipt_violations(spec: RunSpec, receipt: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    if receipt.get("prompt_sha256") != _sha256_bytes(spec.prompt.encode("utf-8")):
        violations.append("hook receipt prompt hash mismatch")
    if receipt.get("backend") != "local":
        violations.append("hook did not use the installed local runtime")
    if receipt.get("retrieval_degraded") is not False:
        violations.append("hook retrieval was degraded or undisclosed")
    if receipt.get("candidate_window_complete") is not True:
        violations.append("hook candidate window was incomplete")
    if receipt.get("graph_search_complete") is not True:
        violations.append("hook graph search was incomplete")
    if spec.mode == "candidate":
        if receipt.get("status") != "injected" or receipt.get("injected") is not True:
            violations.append("candidate hook did not inject memory")
        count = receipt.get("memory_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            violations.append("candidate hook injected no memories")
        tokens = receipt.get("token_estimate")
        if (
            isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or not 1 <= tokens <= MAX_CONTEXT_TOKENS
        ):
            violations.append("candidate hook context exceeded or omitted its token budget")
        score = receipt.get("top_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or score < HOOK_MIN_SCORE:
            violations.append("candidate hook did not clear the relevance threshold")
        if receipt.get("query_source") != "explicit_tag":
            violations.append("candidate hook did not use the explicit bounded query")
    else:
        if receipt.get("status") != "no_match" or receipt.get("injected") is not False:
            violations.append("baseline project memory was not empty")
        if receipt.get("memory_count") != 0:
            violations.append("baseline hook observed project memories")
    return violations


def _write_raw(raw_dir: Path, spec: RunSpec, value: bytes) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{spec.label}.stdout.jsonl"
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise BenchmarkError(f"refusing to overwrite raw artifact: {path}") from exc
    # Never publish an operator username or absolute local path in the report.
    return path.name


def _parse_run(
    spec: RunSpec,
    invocation: Invocation,
    *,
    gold: str,
    raw_dir: Path | None,
) -> dict[str, Any]:
    if invocation.returncode != 0:
        raise BenchmarkError(
            f"{spec.label} exited {invocation.returncode}: {_stderr_tail(invocation.stderr)}"
        )
    events = _parse_events(invocation.stdout, spec.label)
    thread_id, turn_id = _turn_identity(events, spec.label)
    usage, response_ids = _complete_usage(
        events,
        spec.label,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    answer = _answer(events, spec.label, thread_id=thread_id, turn_id=turn_id)
    calls = _tool_calls(events, thread_id=thread_id, turn_id=turn_id)
    delegation = _delegation_evidence(events, thread_id=thread_id, turn_id=turn_id)
    reroutes = _reroute_evidence(events, thread_id=thread_id, turn_id=turn_id)
    receipt = _receipt_delta(spec)
    receipt_violations = _receipt_violations(spec, receipt)
    violations = list(receipt_violations)
    if calls:
        violations.append("model used a tool")
    if delegation:
        violations.append("model delegated")
    if reroutes:
        violations.append("model or provider rerouted")
    exact = answer == gold
    return {
        "sequence": spec.sequence,
        "label": spec.label,
        "mode": spec.mode,
        "repetition": spec.repetition,
        "answer": answer,
        "gold_answer": gold,
        "exact_answer_match": exact,
        "usage_complete": True,
        "usage": usage,
        "usage_source": "matching rawResponse/completed provider notifications",
        "provider_response_count": len(response_ids),
        "provider_response_id_sha256": [
            _sha256_bytes(response_id.encode("utf-8")) for response_id in response_ids
        ],
        "estimated_sol_credits": estimate_sol_credits(usage),
        "estimated_sol_credits_all_input_uncached": estimate_sol_credits_all_input_uncached(usage),
        "estimated_not_provider_reported": True,
        "wall_time_ms": round(invocation.wall_time_ms, 3),
        "thread_start_wall_time_ms": (
            round(invocation.thread_start_wall_time_ms, 3)
            if invocation.thread_start_wall_time_ms is not None
            else None
        ),
        "turn_wall_time_ms": (
            round(invocation.turn_wall_time_ms, 3)
            if invocation.turn_wall_time_ms is not None
            else None
        ),
        "tool_calls": calls,
        "delegation_evidence": delegation,
        "reroute_evidence": reroutes,
        "hook_receipt": receipt,
        "post_trust_hook_receipt": True,
        "hook_contract_valid": not receipt_violations,
        "violations": violations,
        "valid": exact and not violations,
        "raw_stdout_sha256": _sha256_bytes(invocation.stdout),
        "raw_stdout_artifact": _write_raw(raw_dir, spec, invocation.stdout) if raw_dir else None,
        "stderr_sha256": _sha256_bytes(invocation.stderr),
        "stderr_diagnostics_redacted": bool(invocation.stderr),
    }


def _profile(plugin: InstalledPlugin, config: BenchmarkConfig) -> dict[str, Any]:
    order = EXECUTION_ORDERS[config.order]
    return {
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "service_tier": SERVICE_TIER,
        "execution_order": [f"{mode}:{repeat}" for mode, repeat in order],
        "paid_call_count": len(order),
        "transport": "persistent codex app-server stdio JSON-RPC",
        "app_server_strict_config": True,
        "experimental_raw_events": True,
        "usage_source": "rawResponse/completed exact provider usage",
        "raw_artifacts_path_sanitized": True,
        "normal_user_config_loaded": True,
        "normal_enabled_plugins_loaded": True,
        "ignore_user_config": False,
        "hook_trust_bypass": False,
        "installed_plugin": {
            "plugin_id": plugin.plugin_id,
            "version": plugin.version,
            # The absolute install root can contain an operator's username and
            # is not needed to reproduce the artifact. The content hashes below
            # bind the measured installation without publishing local identity.
            "root_kind": "local_install",
            "manifest_sha256": plugin.manifest_sha256,
            "bootstrap_sha256": plugin.bootstrap_sha256,
            "hook_sha256": plugin.hook_sha256,
        },
        "baseline": {
            "context": "complete LOCOMO conversation",
            "project_memory": "fresh and empty",
        },
        "candidate": {
            "context": "short question plus installed pre-prompt hook injection",
            "seed_runtime": "installed frozen plugin runtime",
            "embedding_provider": "bge-onnx",
            "encrypted_project_scope_required": True,
            "maximum_injected_context_tokens": MAX_CONTEXT_TOKENS,
        },
    }


def run_benchmark(
    config: BenchmarkConfig,
    *,
    dry_run: bool,
    preflight_only: bool = False,
    app_server_factory: AppServerFactory = open_app_server,
    plugin_discoverer: PluginDiscoverer = discover_installed_plugin,
    project_preparer: ProjectPreparer = prepare_projects,
) -> dict[str, Any]:
    if dry_run and preflight_only:
        raise BenchmarkError("dry-run and preflight-only modes are mutually exclusive")
    if config.order not in EXECUTION_ORDERS:
        raise BenchmarkError(f"unsupported execution order: {config.order}")
    if config.timeout_seconds <= 0:
        raise BenchmarkError("timeout must be positive")
    if not 0 < config.max_suite_estimated_credits <= DEFAULT_SUITE_CREDIT_CAP:
        raise BenchmarkError("suite estimated-credit cap must be in (0, 10]")
    if not config.dataset_file.is_file():
        raise BenchmarkError(f"missing LOCOMO dataset: {config.dataset_file}")
    if not config.codex_exe.is_file():
        raise BenchmarkError(f"missing Codex executable: {config.codex_exe}")
    plugin = plugin_discoverer(config.codex_exe)
    case = _load_case(config)
    report: dict[str, Any] = {
        "schema_version": "lians.codex-sol-installed-plugin-ab.v2",
        "provider": "OpenAI Codex",
        "workload": "LOCOMO full context versus installed Lians pre-prompt recall",
        "dry_run": dry_run,
        "preflight_only": preflight_only,
        "question_id": case["question_id"],
        "question": case["question"],
        "ground_truth_answer": case["gold"],
        "quality_rule": "trim outer whitespace, then require exact gold string",
        "profile": _profile(plugin, config),
        "target": {
            "usage_extension_percent": TARGET_USAGE_EXTENSION_PERCENT,
            "same_budget_usage_multiplier": TARGET_USAGE_MULTIPLIER,
            "maximum_candidate_cost_ratio": round(1 / TARGET_USAGE_MULTIPLIER, 9),
            "suite_estimated_credit_cap": config.max_suite_estimated_credits,
            "per_run_estimated_credit_reserve": round(
                config.max_suite_estimated_credits / len(EXECUTION_ORDERS[config.order]), 9
            ),
        },
        "estimated_credit_accounting": {
            "label": "estimated Sol credits; not a provider-reported per-turn debit",
            "rates_per_million_tokens": dict(SOL_CREDIT_RATES),
            "reasoning_output_tokens_are_already_in_output_tokens": True,
        },
        "source_artifacts": {
            "frozen_case_sha256": _sha256_bytes(
                json.dumps(FROZEN_CASE, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ),
            "dataset_sha256": _sha256_file(config.dataset_file),
        },
        "prompt_sizes": {
            "baseline_utf8_bytes": len(case["baseline_prompt"].encode("utf-8")),
            "candidate_utf8_bytes": len(case["candidate_prompt"].encode("utf-8")),
        },
        "seed_plan": {
            "record_count": len(case["seed_records"]),
            "evidence_ids": [item["metadata"]["dia_id"] for item in case["seed_records"]],
            "copies_gold_answer": False,
        },
    }
    order = EXECUTION_ORDERS[config.order]
    if dry_run:
        report["planned_runs"] = [
            {"sequence": index, "mode": mode, "repetition": repetition, "isolated_project": True}
            for index, (mode, repetition) in enumerate(order, start=1)
        ]
        report["planned_preflight"] = {
            "transport": "persistent codex app-server stdio JSON-RPC",
            "sequence": [
                "initialize",
                "initialized",
                "hooks/list",
                "thread/start session hook probe (baseline)",
                "thread/start session hook probe (candidate)",
            ],
            "working_directory_count": 2,
            "required_lians_handlers_per_working_directory": 2,
            "required_session_start_dispatches": 2,
            "model_calls": 0,
            "before_paid_turns": True,
        }
        report["verdict"] = {
            "status": "dry_run_only",
            "qualified_target_met": None,
            "statement": "No model call, seeding inference, or usage claim was made.",
        }
        return report

    with tempfile.TemporaryDirectory(prefix="lians-installed-sol-") as temporary:
        root = Path(temporary).resolve()
        baseline_root = root / "baseline"
        candidate_root = root / "candidate"
        baseline_root.mkdir()
        candidate_root.mkdir()
        prepared = project_preparer(plugin, baseline_root, candidate_root, case["seed_records"])
        if prepared.baseline_root.resolve() == prepared.candidate_root.resolve():
            raise BenchmarkError("baseline and candidate projects must be isolated")
        report["seed"] = dict(prepared.seed_report)
        runs: list[dict[str, Any]] = []
        running_credits = 0.0
        per_run_reserve = config.max_suite_estimated_credits / len(order)
        client = app_server_factory(config)
        try:
            report["hook_preflight"] = _preflight_app_server(
                client,
                prepared=prepared,
                plugin=plugin,
                timeout_seconds=config.timeout_seconds,
            )
            if preflight_only:
                report["runs"] = []
                report["verdict"] = {
                    "status": "preflight_only",
                    "qualified_target_met": None,
                    "statement": (
                        "Installed hook discovery and SessionStart dispatch passed with "
                        "zero model calls; no usage claim was made."
                    ),
                }
                return report
            for sequence, (mode, repetition) in enumerate(order, start=1):
                prompt = (
                    case["candidate_prompt"] if mode == "candidate" else case["baseline_prompt"]
                )
                spec = _make_spec(
                    config,
                    prepared,
                    sequence=sequence,
                    mode=mode,
                    repetition=repetition,
                    prompt=prompt,
                )
                invocation = _run_app_server_turn(client, spec)
                parsed = _parse_run(spec, invocation, gold=case["gold"], raw_dir=config.raw_dir)
                runs.append(parsed)
                if float(parsed["estimated_sol_credits"]) > per_run_reserve + 1e-12:
                    raise BenchmarkError(
                        f"{spec.label} exceeded its {per_run_reserve:g} estimated-credit reserve; "
                        "no further calls will run"
                    )
                running_credits += float(parsed["estimated_sol_credits"])
                if running_credits > config.max_suite_estimated_credits + 1e-12:
                    raise BenchmarkError(
                        f"suite exceeded the {config.max_suite_estimated_credits:g} estimated-credit cap "
                        f"after {spec.label}; no further calls will run"
                    )
        finally:
            client.close()

    baseline_runs = [run for run in runs if run["mode"] == "baseline"]
    candidate_runs = [run for run in runs if run["mode"] == "candidate"]
    baseline_cost = sum(float(run["estimated_sol_credits"]) for run in baseline_runs)
    candidate_cost = sum(float(run["estimated_sol_credits"]) for run in candidate_runs)
    neutral_baseline = sum(
        float(run["estimated_sol_credits_all_input_uncached"]) for run in baseline_runs
    )
    neutral_candidate = sum(
        float(run["estimated_sol_credits_all_input_uncached"]) for run in candidate_runs
    )
    if min(baseline_cost, candidate_cost, neutral_baseline, neutral_candidate) <= 0:
        raise BenchmarkError("estimated credits must be greater than zero")
    ratio = candidate_cost / baseline_cost
    neutral_ratio = neutral_candidate / neutral_baseline
    target_ratio = 1 / TARGET_USAGE_MULTIPLIER
    quality = all(bool(run["exact_answer_match"]) for run in runs)
    contracts = all(bool(run["valid"]) for run in runs)
    economics = ratio <= target_ratio + 1e-12 and neutral_ratio <= target_ratio + 1e-12
    total = baseline_cost + candidate_cost
    cap_passed = total <= config.max_suite_estimated_credits + 1e-12
    two_call_gate = len(runs) == 2
    qualified = quality and contracts and economics and cap_passed and two_call_gate
    report["runs"] = runs
    report["quality_gate"] = {"all_runs_exact_gold": quality, "passed": quality}
    report["observed"] = {
        "baseline_estimated_sol_credits": round(baseline_cost, 9),
        "candidate_estimated_sol_credits": round(candidate_cost, 9),
        "suite_estimated_sol_credits": round(total, 9),
        "candidate_cost_ratio": round(ratio, 9),
        "same_budget_usage_multiplier": round(1 / ratio, 9),
        "same_budget_usage_extension_percent": round((1 / ratio - 1) * 100, 9),
        "cache_neutral_candidate_cost_ratio": round(neutral_ratio, 9),
        "cache_neutral_usage_extension_percent": round((1 / neutral_ratio - 1) * 100, 9),
    }
    report["verdict"] = {
        "protected_quality_passed": quality,
        "all_runs_complete_usage": all(bool(run["usage_complete"]) for run in runs),
        "all_runs_no_tools_or_delegation": all(
            not run["tool_calls"] and not run["delegation_evidence"] for run in runs
        ),
        "all_runs_no_model_or_provider_reroute": all(not run["reroute_evidence"] for run in runs),
        "all_installed_hook_receipts_valid": all(bool(run["hook_contract_valid"]) for run in runs),
        "exactly_two_paid_turns": two_call_gate,
        "economic_target_met": economics,
        "suite_estimated_credit_cap_met": cap_passed,
        "qualified_target_met": qualified,
        "status": "qualified_target_met" if qualified else "target_not_qualified",
        "statement": (
            "This proves only the measured installed-plugin LOCOMO workload and uses "
            "estimated, not provider-reported, Sol credits."
        ),
    }
    return report


def _discover_codex() -> Path:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates = list((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
    found = shutil.which("codex")
    return Path(found) if found else Path("codex")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-exe", type=Path, default=_discover_codex())
    parser.add_argument("--dataset", dest="dataset_file", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--order", choices=tuple(EXECUTION_ORDERS), default="ba")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--max-suite-estimated-credits",
        type=float,
        default=DEFAULT_SUITE_CREDIT_CAP,
        help="post-turn estimated-credit gate; may not exceed 10",
    )
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--live", action="store_true", help="opt in to paid Codex calls")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="exercise installed hook discovery/dispatch without a model call",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live and args.preflight_only:
        print(
            "benchmark error: --live and --preflight-only are mutually exclusive", file=sys.stderr
        )
        return 2
    config = BenchmarkConfig(
        codex_exe=args.codex_exe.expanduser().resolve(),
        dataset_file=args.dataset_file.expanduser().resolve(),
        order=args.order,
        timeout_seconds=args.timeout_seconds,
        max_suite_estimated_credits=args.max_suite_estimated_credits,
        raw_dir=args.raw_dir.expanduser().resolve() if args.raw_dir else None,
    )
    try:
        report = run_benchmark(
            config,
            dry_run=not args.live and not args.preflight_only,
            preflight_only=args.preflight_only,
        )
    except BenchmarkError as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    if args.live and not report["verdict"]["qualified_target_met"]:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
