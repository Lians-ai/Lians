from __future__ import annotations

import json

import pytest
from lians_easy.formal_proof import (
    FiniteModelProofChecker,
    FormalProofError,
    PythonFiniteFunctionProofChecker,
)


def _manifest() -> dict:
    return {
        "schema": "https://lians.ai/schemas/finite-proof/v0.1",
        "scope": "Authorization decisions and absolute-value behavior.",
        "variables": {
            "x": [-2, -1, 0, 1, 2],
            "authorized": [False, True],
        },
        "definitions": {
            "absolute": {
                "op": "if",
                "condition": {
                    "op": "ge",
                    "left": {"var": "x"},
                    "right": {"const": 0},
                },
                "then": {"var": "x"},
                "else": {"op": "neg", "arg": {"var": "x"}},
            },
            "may_ship": {"var": "authorized"},
        },
        "assumptions": {"const": True},
        "claims": [
            {
                "id": "absolute-nonnegative",
                "description": "The modeled absolute value is never negative.",
                "expression": {
                    "op": "ge",
                    "left": {"var": "absolute"},
                    "right": {"const": 0},
                },
            },
            {
                "id": "authorization-required",
                "description": "An unauthorized state can never ship.",
                "expression": {
                    "op": "implies",
                    "if": {"op": "not", "arg": {"var": "authorized"}},
                    "then": {"op": "not", "arg": {"var": "may_ship"}},
                },
            },
        ],
        "source_bindings": ["src/policy.py"],
    }


def _repository(tmp_path):
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "proofs").mkdir()
    (root / "src" / "policy.py").write_text(
        "def may_ship(authorized):\n    return authorized\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def _write(root, manifest: dict) -> None:
    (root / "proofs" / "policy.proof.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_exhaustive_finite_model_proves_every_satisfying_assignment(tmp_path) -> None:
    root = _repository(tmp_path)
    _write(root, _manifest())

    result = FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")

    assert result["status"] == "proved"
    assert result["model"]["state_space"] == 10
    assert result["model"]["satisfying_assignments"] == 10
    assert result["model"]["enumeration_complete"] is True
    assert all(item["status"] == "proved" for item in result["claims"])
    assert result["source_bindings"][0]["path"] == "src/policy.py"
    assert result["source_bindings"][0]["sha256"]
    assert result["proof_sha256"]
    assert result["trust"]["project_code_executed"] is False
    assert result["trust"]["source_to_model_refinement_proven"] is False


def test_false_claim_returns_a_concrete_counterexample(tmp_path) -> None:
    root = _repository(tmp_path)
    manifest = _manifest()
    manifest["definitions"]["may_ship"] = {"const": True}
    _write(root, manifest)

    result = FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")

    assert result["status"] == "disproved"
    failed = next(item for item in result["claims"] if item["status"] == "disproved")
    assert failed["id"] == "authorization-required"
    assert failed["counterexample"]["authorized"] is False
    assert failed["counterexample"]["may_ship"] is True


def test_vacuous_or_excessive_models_fail_closed(tmp_path) -> None:
    root = _repository(tmp_path)
    manifest = _manifest()
    manifest["assumptions"] = {
        "op": "eq",
        "left": {"var": "x"},
        "right": {"const": 999},
    }
    _write(root, manifest)
    with pytest.raises(FormalProofError, match="vacuous"):
        FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")

    manifest = _manifest()
    manifest["variables"] = {
        "x": list(range(100)),
        "y": list(range(100)),
        "z": list(range(100)),
    }
    manifest["definitions"] = {}
    manifest["claims"] = [
        {
            "id": "reflexive",
            "description": "Every modeled value equals itself.",
            "expression": {
                "op": "eq",
                "left": {"var": "x"},
                "right": {"var": "x"},
            },
        }
    ]
    _write(root, manifest)
    with pytest.raises(FormalProofError, match="state space"):
        FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")


def test_manifest_rejects_unsafe_bindings_secrets_and_unknown_operations(tmp_path) -> None:
    root = _repository(tmp_path)
    manifest = _manifest()
    manifest["source_bindings"] = ["../outside.py"]
    _write(root, manifest)
    with pytest.raises(FormalProofError, match="repository-relative"):
        FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")

    manifest = _manifest()
    manifest["variables"]["credential"] = ["sk-ant-api03-" + ("x" * 30)]
    _write(root, manifest)
    with pytest.raises(FormalProofError, match="credential material"):
        FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")

    manifest = _manifest()
    manifest["claims"][0]["expression"] = {"op": "python-eval", "arg": {"const": True}}
    _write(root, manifest)
    with pytest.raises(FormalProofError, match="Unsupported proof operation"):
        FiniteModelProofChecker().verify(root, "proofs/policy.proof.json")


def _write_python_proof(root, *, broken: bool = False) -> None:
    (root / "src" / "clamp.py").write_text(
        (
            "def clamp(value, lower, upper):\n"
            "    if value < lower:\n"
            "        return lower\n"
            "    if value > upper:\n"
            f"        return upper{' + 1' if broken else ''}\n"
            "    return value\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "schema": "https://lians.ai/schemas/python-function-proof/v0.1",
        "scope": "The actual restricted clamp function always returns inside its bounds.",
        "source": "src/clamp.py",
        "function": "clamp",
        "variables": {
            "value": list(range(-3, 4)),
            "lower": [-2, 0],
            "upper": [0, 2],
        },
        "assumptions": {
            "op": "le",
            "left": {"var": "lower"},
            "right": {"var": "upper"},
        },
        "claims": [
            {
                "id": "lower-bound",
                "description": "The actual return value is never below lower.",
                "expression": {
                    "op": "ge",
                    "left": {"var": "result"},
                    "right": {"var": "lower"},
                },
            },
            {
                "id": "upper-bound",
                "description": "The actual return value is never above upper.",
                "expression": {
                    "op": "le",
                    "left": {"var": "result"},
                    "right": {"var": "upper"},
                },
            },
        ],
    }
    (root / "proofs" / "clamp.python-proof.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_restricted_python_backend_proves_actual_function_source(tmp_path) -> None:
    root = _repository(tmp_path)
    _write_python_proof(root)

    result = PythonFiniteFunctionProofChecker().verify(
        root, "proofs/clamp.python-proof.json"
    )

    assert result["status"] == "proved"
    assert result["model"]["state_space"] == 28
    assert result["source_bindings"][0]["function"] == "clamp"
    assert result["trust"]["project_code_executed"] is False
    assert result["trust"]["source_to_model_refinement_proven"] is True
    assert result["trust"]["bounded_implementation_correctness_proven"] is True
    assert all(item["status"] == "proved" for item in result["claims"])


def test_restricted_python_backend_finds_source_counterexample(tmp_path) -> None:
    root = _repository(tmp_path)
    _write_python_proof(root, broken=True)

    result = PythonFiniteFunctionProofChecker().verify(
        root, "proofs/clamp.python-proof.json"
    )

    assert result["status"] == "disproved"
    failed = next(item for item in result["claims"] if item["status"] == "disproved")
    assert failed["id"] == "upper-bound"
    assert failed["counterexample"]["result"] > failed["counterexample"]["upper"]
    assert result["trust"]["bounded_implementation_correctness_proven"] is False


def test_restricted_python_backend_rejects_calls_instead_of_executing_them(tmp_path) -> None:
    root = _repository(tmp_path)
    _write_python_proof(root)
    (root / "src" / "clamp.py").write_text(
        "def clamp(value, lower, upper):\n    return max(lower, min(value, upper))\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(FormalProofError, match="supported pure subset"):
        PythonFiniteFunctionProofChecker().verify(
            root, "proofs/clamp.python-proof.json"
        )
