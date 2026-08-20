from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lians_easy.continuity import build_continuity_graph
from lians_easy.mcp import MCPServer
from lians_easy.project import Project
from lians_easy.state_integrity import StateIntegrityService
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService
from lians_easy.verification import VerificationError, VerificationService


def _run(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _repository(tmp_path: Path) -> tuple[Path, Project]:
    root = tmp_path / "repository"
    root.mkdir()
    _run(root, "init")
    _run(root, "config", "user.email", "tests@lians.ai")
    _run(root, "config", "user.name", "Lians Tests")
    _run(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 41\n", encoding="utf-8", newline="\n"
    )
    (root / "README.md").write_text("# Example\n", encoding="utf-8", newline="\n")
    _run(root, "add", ".")
    _run(root, "commit", "-m", "initial")
    project = Project(
        id="project-verification",
        name="repository",
        root=str(root),
        origin="github.com/lians/example",
        trusted_root=root,
    )
    return root, project


def _ready_task(store: MemoryStore, *, project_id: str) -> None:
    tasks = TaskContractService(store)
    tasks.start(
        "Correct the answer and preserve repository safety.",
        ["The answer returns 42", "Verification checks pass"],
        project_id=project_id,
        constraints=["Do not expose credentials"],
        task_id="answer-fix",
    )
    tasks._checkpoint_trusted(
        "answer-fix",
        "The answer and checks are complete.",
        issuer="local_verification",
        project_id=project_id,
        evidence=[
            {
                "criterion_id": "criterion-1",
                "evidence": "src/app.py returns 42",
                "trust_class": "measured_local",
                "source": "repository inspection",
            },
            {
                "criterion_id": "criterion-2",
                "evidence": "pytest passed",
                "trust_class": "measured_local",
                "source": "test runner",
            },
        ],
        constraint_checks=[
            {
                "constraint_id": "constraint-1",
                "status": "passed",
                "evidence": "Diff credential scan passed",
                "trust_class": "measured_local",
                "source": "diff credential scan",
            }
        ],
        artifacts=["src/app.py"],
    )


def _configured_service(store: MemoryStore, project: Project) -> VerificationService:
    _ready_task(store, project_id=project.id)
    service = VerificationService(store)
    service.configure(
        "answer-fix",
        project_id=project.id,
        allowed_paths=["src/**"],
        criterion_paths={
            "criterion-1": ["src/**"],
            "criterion-2": ["src/**"],
        },
        required_checks=["tests"],
        forbidden_terms=["chip"],
        max_advisories=1,
    )
    return service


def _passed_check() -> list[dict]:
    return [
        {
            "name": "tests",
            "status": "passed",
            "evidence": "286 tests passed",
            "command": "python -m pytest -q",
            "exit_code": 0,
            "output_sha256": "a" * 64,
        }
    ]


def _write_proof_manifest(root: Path, *, valid: bool = True) -> None:
    (root / "proofs").mkdir(exist_ok=True)
    manifest = {
        "schema": "https://lians.ai/schemas/finite-proof/v0.1",
        "scope": "The formal successor operation always returns a larger integer.",
        "variables": {"input": list(range(-10, 11))},
        "definitions": {
            "output": {
                "op": "add",
                "args": [{"var": "input"}, {"const": 1 if valid else -1}],
            }
        },
        "assumptions": {"const": True},
        "claims": [
            {
                "id": "successor-increases",
                "description": "The modeled successor is greater than its input.",
                "expression": {
                    "op": "gt",
                    "left": {"var": "output"},
                    "right": {"var": "input"},
                },
            }
        ],
        "source_bindings": ["src/app.py"],
    }
    (root / "proofs" / "answer.proof.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_python_answer_proof(root: Path) -> None:
    (root / "proofs").mkdir(exist_ok=True)
    manifest = {
        "schema": "https://lians.ai/schemas/python-function-proof/v0.1",
        "scope": "The actual bounded answer function returns 42 for every declared input.",
        "source": "src/app.py",
        "function": "answer",
        "variables": {"seed": list(range(-20, 21))},
        "assumptions": {"const": True},
        "claims": [
            {
                "id": "answer-is-42",
                "description": "The actual function result equals 42.",
                "expression": {
                    "op": "eq",
                    "left": {"var": "result"},
                    "right": {"const": 42},
                },
            }
        ],
    }
    (root / "proofs" / "answer.python-proof.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _mcp_call(server: MCPServer, request_id: int, name: str, arguments: dict) -> dict:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert "error" not in response, response
    return response["result"]["structuredContent"]


def test_signed_verification_receipt_binds_intent_diff_scope_and_evidence(tmp_path) -> None:
    root, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    service = _configured_service(store, project)
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8", newline="\n"
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary="Corrected the answer. Tests pass. Ready for review.",
        check_results=_passed_check(),
        client="codex",
    )

    assert result["verdict"] == "ready_for_human_ship_review", result["blockers"]
    assert result["may_claim_completion"] is True
    assert result["may_claim_safe_to_ship"] is False
    assert result["blockers"] == []
    receipt = result["receipt"]
    assert receipt["repository"]["diff_sha256"]
    assert receipt["intent"]["task_contract_sha256"] == hashlib.sha256(
        json.dumps(
            service.tasks.status("answer-fix", project_id=project.id)["contract"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert receipt["intent"]["task_state_sha256"]
    assert receipt["intent"]["verification_policy_sha256"]
    assert receipt["changed_files"] == [
        {
            "path": "src/app.py",
            "previous_path": None,
            "status": "M",
            "tracked": True,
            "additions": 1,
            "deletions": 1,
            "criteria": ["criterion-1", "criterion-2"],
        }
    ]
    assert receipt["checks"][0]["trust"] == "caller_attested"
    assert receipt["trust"]["human_ship_decision_required"] is True
    signature = receipt["signature"]
    protected = {key: value for key, value in receipt.items() if key != "signature"}
    canonical = json.dumps(
        protected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    Ed25519PublicKey.from_public_bytes(
        base64.b64decode(signature["public_key"], validate=True)
    ).verify(base64.b64decode(signature["value"], validate=True), canonical)
    assert service.status("answer-fix", project_id=project.id)["receipt"]["id"] == receipt["id"]
    graph = build_continuity_graph(store, project_id=project.id)
    assert graph["summary"]["verification_policy_count"] == 1
    assert graph["summary"]["verification_receipt_count"] == 1
    assert any(node["type"] == "verification_receipt" for node in graph["nodes"])
    assert any(edge["relation"] == "verified_by" for edge in graph["edges"])


def test_scope_and_traceability_violations_block_completion(tmp_path) -> None:
    root, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    service = _configured_service(store, project)
    (root / "README.md").write_text(
        "# Expanded project\n", encoding="utf-8", newline="\n"
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary="Updated the project documentation.",
        check_results=_passed_check(),
    )

    codes = {item["code"] for item in result["blockers"]}
    assert result["verdict"] == "blocked"
    assert {"scope_violation", "unmapped_changes"} <= codes
    assert result["receipt"]["changed_files"][0]["path"] == "README.md"


def test_signed_receipt_carries_exhaustive_formal_model_proof(tmp_path) -> None:
    root, project = _repository(tmp_path)
    _write_proof_manifest(root)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    _ready_task(store, project_id=project.id)
    service = VerificationService(store)
    service.configure(
        "answer-fix",
        project_id=project.id,
        allowed_paths=["src/**", "proofs/**"],
        criterion_paths={
            "criterion-1": ["src/**", "proofs/**"],
            "criterion-2": ["src/**", "proofs/**"],
        },
        required_checks=["tests"],
        formal_proofs=[
            {
                "id": "successor-proof",
                "backend": "finite-model-v1",
                "manifest": "proofs/answer.proof.json",
            }
        ],
    )
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8", newline="\n"
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary="Corrected the answer and supplied the bounded formal model.",
        check_results=_passed_check(),
    )

    assert result["verdict"] == "ready_for_human_ship_review", result["blockers"]
    assert result["may_claim_declared_model_proved"] is True
    assert result["may_claim_implementation_correct"] is False
    proof = result["receipt"]["formal_proofs"][0]
    assert proof["id"] == "successor-proof"
    assert proof["status"] == "proved"
    assert proof["model"]["state_space"] == 21
    assert proof["trust"]["project_code_executed"] is False
    assert proof["trust"]["source_to_model_refinement_proven"] is False
    assert result["receipt"]["trust"]["formal_model"] == (
        "proved_by_exhaustive_enumeration"
    )
    assert result["receipt"]["trust"]["implementation_correctness_formally_proven"] is False
    assert len(json.dumps(result["receipt"]).encode()) < 20_000
    graph = build_continuity_graph(store, project_id=project.id)
    assert any(
        node["type"] == "verification_receipt"
        and node["label"] == "Proof-backed ship review"
        for node in graph["nodes"]
    )

    _write_proof_manifest(root, valid=False)
    failed = service.verify(
        "answer-fix",
        project=project,
        agent_summary="The bounded model has been checked again.",
        check_results=_passed_check(),
    )
    assert failed["verdict"] == "blocked"
    assert failed["may_claim_declared_model_proved"] is False
    assert "formal_proof_disproved" in {item["code"] for item in failed["blockers"]}


def test_signed_receipt_proves_bounded_restricted_python_implementation(tmp_path) -> None:
    root, project = _repository(tmp_path)
    _write_python_answer_proof(root)
    (root / "src" / "app.py").write_text(
        "def answer(seed):\n    return 42\n", encoding="utf-8", newline="\n"
    )
    store = MemoryStore(tmp_path / "memory.sqlite3")
    _ready_task(store, project_id=project.id)
    service = VerificationService(store)
    service.configure(
        "answer-fix",
        project_id=project.id,
        allowed_paths=["src/**", "proofs/**"],
        criterion_paths={
            "criterion-1": ["src/**", "proofs/**"],
            "criterion-2": ["src/**", "proofs/**"],
        },
        required_checks=["tests"],
        formal_proofs=[
            {
                "id": "answer-source-proof",
                "backend": "python-finite-function-v1",
                "manifest": "proofs/answer.python-proof.json",
            }
        ],
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary="Corrected the answer and checked every declared input.",
        check_results=_passed_check(),
    )

    assert result["verdict"] == "ready_for_human_ship_review", result["blockers"]
    assert result["may_claim_declared_model_proved"] is True
    assert result["may_claim_bounded_implementation_proved"] is True
    assert result["may_claim_implementation_correct"] is False
    proof = result["receipt"]["formal_proofs"][0]
    assert proof["backend"] == "python-finite-function-v1"
    assert proof["status"] == "proved"
    assert proof["source_bindings"][0]["path"] == "src/app.py"
    assert result["receipt"]["trust"]["source_to_model_refinement_proven"] is True
    assert result["receipt"]["trust"]["bounded_implementation_correctness_proven"] is True
    assert result["receipt"]["trust"]["implementation_correctness_formally_proven"] is False


def test_secret_language_and_advisory_overload_fail_closed_without_secret_echo(tmp_path) -> None:
    root, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    service = _configured_service(store, project)
    exposed = "sk-ant-api03-" + ("N" * 40)
    (root / "src" / "app.py").write_text(
        f"API_KEY = '{exposed}'\n",
        encoding="utf-8",
        newline="\n",
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary=(
            "The multi-select chip works. One thing I noticed but did not touch is CSS. "
            "One thing still on your side: cleanup."
        ),
        check_results=_passed_check(),
    )

    codes = {item["code"] for item in result["blockers"]}
    assert {"secret_detected", "forbidden_language", "advisory_overload"} <= codes
    encoded = json.dumps(result)
    assert exposed not in encoded
    assert "anthropic_key" in encoded


def test_missing_task_evidence_checks_and_current_state_block_verification(tmp_path) -> None:
    root, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    tasks = TaskContractService(store)
    tasks.start(
        "Change the answer.",
        ["The answer returns 42"],
        project_id=project.id,
        constraints=["No stale decisions"],
        task_id="answer-fix",
    )
    service = VerificationService(store)
    service.configure(
        "answer-fix",
        project_id=project.id,
        allowed_paths=["src/**"],
        criterion_paths={"criterion-1": ["src/**"]},
        required_checks=["tests"],
    )
    state = store.set_current("release/platform", "Windows", project_id=project.id)
    StateIntegrityService(store).link(
        state["id"],
        "src/app.py",
        dependent_type="artifact",
        project_id=project.id,
    )
    store.set_current(
        "release/platform",
        "Windows and macOS",
        project_id=project.id,
        reason="release expanded",
    )
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8", newline="\n"
    )

    result = service.verify(
        "answer-fix",
        project=project,
        agent_summary="Changed the answer.",
        check_results=[],
    )

    codes = {item["code"] for item in result["blockers"]}
    assert {"missing_evidence", "unknown_constraint", "missing_check", "stale_state"} <= codes


def test_policy_and_attestation_inputs_reject_unsafe_or_ambiguous_values(tmp_path) -> None:
    _, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    _ready_task(store, project_id=project.id)
    service = VerificationService(store)

    with pytest.raises(VerificationError, match="repository-relative"):
        service.configure(
            "answer-fix",
            project_id=project.id,
            allowed_paths=["../outside/**"],
            criterion_paths={"criterion-1": ["src/**"]},
        )
    with pytest.raises(VerificationError, match="Unsupported formal proof backend"):
        service.configure(
            "answer-fix",
            project_id=project.id,
            allowed_paths=["src/**"],
            criterion_paths={"criterion-1": ["src/**"]},
            formal_proofs=[
                {"id": "proof", "backend": "arbitrary-shell", "manifest": "proof.json"}
            ],
        )
    with pytest.raises(VerificationError, match="concrete files"):
        service.configure(
            "answer-fix",
            project_id=project.id,
            allowed_paths=["src/**"],
            criterion_paths={"criterion-1": ["src/**"]},
            formal_proofs=[
                {
                    "id": "proof",
                    "backend": "finite-model-v1",
                    "manifest": "proofs/*.json",
                }
            ],
        )
    service.configure(
        "answer-fix",
        project_id=project.id,
        allowed_paths=["src/**"],
        criterion_paths={"criterion-1": ["src/**"]},
        required_checks=["tests"],
    )
    with pytest.raises(VerificationError, match="nonzero"):
        service.verify(
            "answer-fix",
            project=project,
            agent_summary="Done.",
            check_results=[
                {
                    "name": "tests",
                    "status": "passed",
                    "evidence": "claimed success",
                    "exit_code": 1,
                }
            ],
        )
    with pytest.raises(VerificationError, match="credential material"):
        service.verify(
            "answer-fix",
            project=project,
            agent_summary="Done.",
            check_results=[
                {
                    "name": "tests",
                    "status": "passed",
                    "evidence": "log contained " + "sk-ant-api03-" + ("a" * 30),
                    "exit_code": 0,
                }
            ],
        )


def test_verification_policy_and_receipt_are_encrypted_at_rest(tmp_path) -> None:
    root, project = _repository(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    service = _configured_service(store, project)
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8", newline="\n"
    )
    service.verify(
        "answer-fix",
        project=project,
        agent_summary="Corrected the answer.",
        check_results=_passed_check(),
    )

    with sqlite3.connect(store.path) as database:
        rows = database.execute(
            "SELECT content, content_cipher FROM memories WHERE kind LIKE 'verification_%'"
        ).fetchall()
    assert len(rows) == 2
    assert all(row[0] is None and row[1] is not None for row in rows)
    raw = store.path.read_bytes()
    assert b"Corrected the answer" not in raw
    assert b"src/**" not in raw


def test_mcp_cross_agent_verification_flow_uses_the_same_signed_receipt(
    tmp_path, monkeypatch
) -> None:
    root, project = _repository(tmp_path)
    _write_proof_manifest(root)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    _ready_task(store, project_id=project.id)
    monkeypatch.setattr("lians_easy.mcp.detect_project", lambda _value: project)
    server = MCPServer(store)
    configured = _mcp_call(
        server,
        1,
        "configure_verification",
        {
            "task_id": "answer-fix",
            "allowed_paths": ["src/**", "proofs/**"],
            "criterion_paths": {
                "criterion-1": ["src/**", "proofs/**"],
                "criterion-2": ["src/**", "proofs/**"],
            },
            "required_checks": ["tests"],
            "formal_proofs": [
                {
                    "id": "successor-proof",
                    "backend": "finite-model-v1",
                    "manifest": "proofs/answer.proof.json",
                }
            ],
            "client": "claude",
        },
    )
    assert configured["policy"]["external_check_trust"] == "caller_attested"
    (root / "src" / "app.py").write_text(
        "def answer():\n    return 42\n", encoding="utf-8", newline="\n"
    )
    verified = _mcp_call(
        server,
        2,
        "verify_work",
        {
            "task_id": "answer-fix",
            "agent_summary": "Corrected the answer and ran the tests.",
            "check_results": _passed_check(),
            "client": "codex",
        },
    )
    status = _mcp_call(
        server,
        3,
        "verification_status",
        {"task_id": "answer-fix"},
    )
    assert verified["verdict"] == "ready_for_human_ship_review"
    assert verified["may_claim_declared_model_proved"] is True
    assert verified["receipt"]["formal_proofs"][0]["status"] == "proved"
    assert status["receipt"]["id"] == verified["receipt"]["id"]
