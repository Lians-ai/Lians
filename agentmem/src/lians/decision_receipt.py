"""Portable Lians Decision Receipt v0.1 construction and verification.

The receipt is deliberately provider-neutral.  It binds a consequential
decision to the model, policy, authorization, tools, sources, and review state
that formed its recorded boundary.  The protected payload is canonical JSON;
its SHA-256 digest is always present and can optionally be signed with an
Ed25519 key controlled by the deployment owner.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

RECEIPT_VERSION = "0.1"
RECEIPT_SCHEMA = "https://lians.ai/specs/decision-receipt/v0.1/schema.json"
EVIDENCE_GRAPH_MANIFEST_SCHEMA = "lians.evidence-graph-manifest.v1"
_EVIDENCE_GRAPH_MANIFEST_LIMIT = 10_000
_VERIFICATION_ERROR_LIMIT = 64
_VERIFICATION_ERROR_LENGTH = 2_048
_EVIDENCE_KINDS = (
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
)
_PORTABLE_ARTIFACT_METADATA_KEYS = {
    "memory_id",
    "source",
    "protocol",
    "event_kind",
    "hash_role",
    "tool_name",
    "tool_call_id",
    "provider",
}
_PRINCIPAL_TYPE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_AUTHORIZATION_SCOPE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_AUTH_METHOD = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_AUTHORIZATION_ROLES = frozenset({"owner", "analyst", "compliance", "readonly"})


class _VerificationErrors(list[str]):
    """A deterministic, deduplicated error report with a hard memory bound."""

    def __init__(self) -> None:
        super().__init__()
        self._seen: set[str] = set()
        self._overflowed = False

    def append(self, message: str) -> None:
        normalized = str(message)
        if len(normalized) > _VERIFICATION_ERROR_LENGTH:
            normalized = normalized[: _VERIFICATION_ERROR_LENGTH - 3] + "..."
        if normalized in self._seen:
            return
        if len(self) < _VERIFICATION_ERROR_LIMIT - 1:
            self._seen.add(normalized)
            super().append(normalized)
            return
        if not self._overflowed:
            self._overflowed = True
            super().append("Additional verification errors were omitted")


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by the receipt contract."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
    ):
        return False
    return all(character in "0123456789abcdef" for character in value)


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (TypeError, ValueError):
        return False
    return str(parsed) == value


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Expected a mapping or Pydantic model, got {type(value)!r}")


def _decode_raw_key(value: str, *, expected_length: int) -> bytes:
    """Decode a raw Ed25519 key supplied as base64 or hexadecimal."""
    candidate = value.strip()
    if len(candidate) == expected_length * 2:
        try:
            raw = bytes.fromhex(candidate)
        except ValueError:
            raw = b""
        if len(raw) == expected_length:
            return raw
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Ed25519 key must be raw base64 or hexadecimal") from exc
    if len(raw) != expected_length:
        raise ValueError(f"Ed25519 key must decode to {expected_length} bytes")
    return raw


def receipt_signing_public_key(signing_private_key: str) -> str:
    """Validate a raw Ed25519 private key and return its raw public key as base64.

    Startup validation and trust-registry bootstrapping use this helper so a
    malformed production key is rejected before the first receipt request.
    The private key never leaves the caller and is never persisted here.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(
        _decode_raw_key(signing_private_key, expected_length=32)
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public_raw).decode("ascii")


def _check(
    check_id: str,
    label: str,
    weight: int,
    passed: bool,
    evidence: str,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "weight": weight,
        "status": "present" if passed else "missing",
        "evidence": evidence,
    }


def _normalized_evidence_entries(
    audit_chain: Mapping[str, Any],
    *,
    kind: str | None = None,
    direct_only: bool = False,
) -> list[Mapping[str, Any]]:
    manifest = audit_chain.get("lians_evidence_graph")
    if not isinstance(manifest, Mapping):
        return []
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return []
    result: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        artifact = entry.get("artifact")
        if not isinstance(artifact, Mapping):
            continue
        if direct_only and entry.get("relation") != "direct":
            continue
        if kind is not None and artifact.get("kind") != kind:
            continue
        result.append(entry)
    return result


def _normalization_complete(audit_chain: Mapping[str, Any]) -> bool:
    manifest = audit_chain.get("lians_evidence_graph")
    if not isinstance(manifest, Mapping) or manifest.get("complete") is not True:
        return False
    normalization = manifest.get("normalization")
    return bool(
        isinstance(normalization, Mapping)
        and normalization.get("normalized_complete") is True
    )


def _decision_authorization_snapshot(
    decision: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a canonical v3 write snapshot, never caller-declared metadata."""

    if (
        decision.get("record_hash_version") != 3
        or decision.get("record_integrity_status") != "verified"
    ):
        return None
    principal_ref = decision.get("recorded_by_principal_ref")
    auth_method = decision.get("recorded_by_auth_method")
    credential_ref = decision.get("recorded_by_credential_ref")
    principal_type = decision.get("recorded_by_principal_type")
    role = decision.get("recorded_by_role")
    raw_scopes = decision.get("recorded_by_scopes")
    if (
        not isinstance(principal_ref, str)
        or not principal_ref.startswith("lians:principal:v1:")
        or len(principal_ref) <= 20
        or principal_ref == "lians:principal:v1:legacy-unverified"
        or not isinstance(auth_method, str)
        or not _AUTH_METHOD.fullmatch(auth_method)
        or auth_method == "legacy_unverified"
        or not isinstance(credential_ref, str)
        or not credential_ref.startswith("lians:credential:v1:sha256:")
        or not _is_sha256(credential_ref.rsplit(":", 1)[-1])
        or not isinstance(principal_type, str)
        or not _PRINCIPAL_TYPE.fullmatch(principal_type)
        or (role is not None and role not in _AUTHORIZATION_ROLES)
        or not isinstance(raw_scopes, list)
    ):
        return None
    if any(
        not isinstance(scope, str) or not _AUTHORIZATION_SCOPE.fullmatch(scope)
        for scope in raw_scopes
    ):
        return None
    scopes = sorted(set(raw_scopes))
    if (
        len(scopes) != len(raw_scopes)
        or not 1 <= len(scopes) <= 50
        or "write" not in scopes
    ):
        return None
    return {
        "verified": True,
        "decision": "allowed",
        "action": "decision.record",
        "principal_ref": principal_ref,
        "principal_type": principal_type,
        "role": role,
        "scopes": scopes,
        "auth_method": auth_method,
        "credential_ref": credential_ref,
    }


def assess_completeness(
    decision: Mapping[str, Any],
    cited_evidence: Sequence[Mapping[str, Any]],
    audit_chain: Mapping[str, Any],
    *,
    will_sign: bool,
    tools_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score the explicit evidence boundary without pretending missing data exists."""
    metadata = dict(decision.get("metadata") or {})
    principal_ref = decision.get("recorded_by_principal_ref")
    authorization_snapshot = _decision_authorization_snapshot(decision)
    policy_evaluation = metadata.get("policy_evaluation")
    tools = list(
        tools_evidence
        if tools_evidence is not None
        else metadata.get("tools") or metadata.get("tool_calls") or []
    )

    sources_complete = bool(cited_evidence) and all(
        item.get("source") and _is_sha256(item.get("content_hash")) and item.get("valid_from")
        for item in cited_evidence
    )
    tools_complete = bool(tools) and all(
        isinstance(item, Mapping)
        and (item.get("name") or item.get("tool_id"))
        and _is_sha256(item.get("definition_hash"))
        and _is_sha256(item.get("result_hash"))
        for item in tools
    )
    authorization_complete = bool(principal_ref) and authorization_snapshot is not None

    checks = [
        _check(
            "decision.identity",
            "Decision and agent identity",
            8,
            bool(
                decision.get("id")
                and decision.get("agent_id")
                and decision.get("decision_type")
                and decision.get("record_integrity_status") == "verified"
                and decision.get("record_hash_version") in {2, 3}
                and _is_sha256(decision.get("record_hash"))
                and principal_ref
            ),
            "decision.id, claimed agent, authenticated recorder, decision type",
        ),
        _check(
            "decision.time",
            "Decision-time boundary",
            7,
            bool(
                decision.get("decided_at")
                and decision.get("knowledge_as_of")
                and decision.get("knowledge_recorded_as_of")
            ),
            "decision.decided_at, decision.knowledge_as_of, decision.knowledge_recorded_as_of",
        ),
        _check(
            "model.identity",
            "Model identity and version",
            10,
            bool(decision.get("model_id") and decision.get("model_version")),
            "model.id, model.version",
        ),
        _check(
            "instructions.hash",
            "System instruction/configuration hash",
            7,
            _is_sha256(metadata.get("system_instruction_hash") or metadata.get("configuration_hash")),
            "model.system_instruction_hash",
        ),
        _check(
            "artifacts.hashes",
            "Input and output hashes",
            10,
            _is_sha256(decision.get("input_hash")) and _is_sha256(decision.get("output_hash")),
            "artifacts.input_hash, artifacts.output_hash",
        ),
        _check(
            "sources.provenance",
            "Versioned cited sources",
            12,
            sources_complete,
            "sources[].source, content_hash, valid_from",
        ),
        _check(
            "policy.evaluation",
            "Policy version and evaluation",
            10,
            bool(decision.get("policy_version") and policy_evaluation),
            "policy.version, policy.evaluation",
        ),
        _check(
            "authorization.context",
            "Principal and authorization context",
            10,
            authorization_complete,
            "actor.recorded_by, authorization.recording_write",
        ),
        _check(
            "tools.provenance",
            "Tool definitions and results",
            8,
            tools_complete,
            "tools[].definition_hash, tools[].result_hash",
        ),
        _check(
            "review.status",
            "Human-review status",
            6,
            bool(decision.get("human_review_status")),
            "human_review.status",
        ),
        _check(
            "integrity.audit_chain",
            "Audit-chain verification",
            7,
            audit_chain.get("status") == "ok",
            "audit_chain.status",
        ),
        _check(
            "evidence.normalization",
            "Normalized evidence graph coverage",
            0,
            _normalization_complete(audit_chain),
            "audit_chain.lians_evidence_graph.normalization.normalized_complete",
        ),
        _check(
            "integrity.signature",
            "Deployment signature",
            5,
            will_sign,
            "integrity.signature",
        ),
    ]
    score = sum(item["weight"] for item in checks if item["status"] == "present")
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    missing = [item["id"] for item in checks if item["status"] == "missing"]
    return {
        "score": score,
        "grade": grade,
        "status": "complete" if not missing else "incomplete",
        "checks": checks,
        "missing": missing,
    }


def _build_decision_receipt_envelope(
    *,
    decision: Mapping[str, Any] | Any,
    knowledge_snapshot: Sequence[Mapping[str, Any] | Any],
    cited_evidence: Sequence[Mapping[str, Any] | Any],
    audit_chain: Mapping[str, Any],
    signature_expected: bool,
    signing_key_id: str,
    include_source_content: bool = False,
) -> dict[str, Any]:
    """Build the protected envelope before any provider performs signing."""
    decision_data = _as_dict(decision)
    snapshot_data = [_as_dict(item) for item in knowledge_snapshot]
    cited_data = [_as_dict(item) for item in cited_evidence]
    metadata = dict(decision_data.get("metadata") or {})
    will_sign = signature_expected
    direct_entries = _normalized_evidence_entries(
        audit_chain, direct_only=True
    )

    def artifacts_of_kind(kind: str) -> list[Mapping[str, Any]]:
        return [
            artifact
            for entry in direct_entries
            if isinstance((artifact := entry.get("artifact")), Mapping)
            and artifact.get("kind") == kind
        ]

    def entries_of_kind(kind: str) -> list[Mapping[str, Any]]:
        return [
            entry
            for entry in direct_entries
            if isinstance((artifact := entry.get("artifact")), Mapping)
            and artifact.get("kind") == kind
        ]

    model_artifacts = artifacts_of_kind("model")
    instruction_artifacts = artifacts_of_kind("instruction")
    input_entries = entries_of_kind("input")
    output_entries = entries_of_kind("output")
    policy_artifacts = artifacts_of_kind("policy")

    def first_sha256(artifacts: Sequence[Mapping[str, Any]]) -> str | None:
        values = sorted(
            {
                str(artifact.get("artifact_hash"))
                for artifact in artifacts
                if _is_sha256(artifact.get("artifact_hash"))
            }
        )
        return values[0] if len(values) == 1 else None

    def decision_boundary_sha256(
        entries: Sequence[Mapping[str, Any]],
        *,
        hash_role: str,
        match_basis: str,
    ) -> str | None:
        values: set[str] = set()
        for entry in entries:
            artifact = entry.get("artifact")
            if not isinstance(artifact, Mapping):
                continue
            artifact_metadata = artifact.get("metadata")
            if not isinstance(artifact_metadata, Mapping):
                artifact_metadata = {}
            bases = entry.get("match_basis")
            is_decision_boundary = artifact_metadata.get("hash_role") == hash_role or (
                isinstance(bases, list) and match_basis in bases
            )
            if is_decision_boundary and _is_sha256(artifact.get("artifact_hash")):
                values.add(str(artifact["artifact_hash"]))
        return next(iter(values)) if len(values) == 1 else None

    selected_model = next(
        (
            artifact
            for artifact in model_artifacts
            if artifact.get("identifier") == decision_data.get("model_id")
        ),
        model_artifacts[0] if len(model_artifacts) == 1 else {},
    )
    selected_policy = next(
        (
            artifact
            for artifact in policy_artifacts
            if artifact.get("version") == decision_data.get("policy_version")
            or artifact.get("identifier") == decision_data.get("policy_version")
        ),
        policy_artifacts[0] if len(policy_artifacts) == 1 else {},
    )
    model_id = decision_data.get("model_id") or selected_model.get("identifier")
    model_version = decision_data.get("model_version") or selected_model.get("version")
    input_hash = decision_data.get("input_hash") or decision_boundary_sha256(
        input_entries,
        hash_role="decision_input",
        match_basis="decision.input_hash",
    )
    output_hash = decision_data.get("output_hash") or decision_boundary_sha256(
        output_entries,
        hash_role="decision_output",
        match_basis="decision.output_hash",
    )
    system_instruction_hash = (
        metadata.get("system_instruction_hash") or first_sha256(instruction_artifacts)
    )
    policy_version = (
        decision_data.get("policy_version")
        or selected_policy.get("version")
        or selected_policy.get("identifier")
    )
    authorization_snapshot = _decision_authorization_snapshot(decision_data)
    recording_write = authorization_snapshot or {
        "verified": False,
        "decision": "unverified",
        "action": "decision.record",
        "principal_ref": decision_data.get("recorded_by_principal_ref"),
        "principal_type": None,
        "role": None,
        "scopes": [],
        "auth_method": decision_data.get("recorded_by_auth_method"),
        "credential_ref": decision_data.get("recorded_by_credential_ref"),
    }
    declared_fields = {
        key: metadata[key]
        for key in ("authorization", "permissions")
        if key in metadata
    }
    declared_workflow_context = (
        {
            "verified": False,
            "source": "caller_supplied_decision_metadata",
            **declared_fields,
        }
        if declared_fields
        else None
    )

    sources = [
        {
            "memory_id": str(item.get("id")),
            "source": item.get("source"),
            "source_version": (item.get("metadata") or {}).get("source_version"),
            # Hash-only is the safe portable default. Full evidence content can
            # be explicitly requested by an authorized exporter when the
            # receiving party and retention boundary are known.
            "content": item.get("content") if include_source_content else None,
            "content_hash": item.get("content_hash"),
            "valid_from": item.get("valid_from"),
            "valid_to": item.get("valid_to"),
            "recorded_at": item.get("ingestion_time"),
            "erased_at": item.get("erased_at"),
        }
        for item in cited_data
    ]
    source_keys = {
        (item["memory_id"], item["content_hash"], item["source"])
        for item in sources
    }
    for artifact in artifacts_of_kind("source"):
        artifact_metadata = artifact.get("metadata")
        if not isinstance(artifact_metadata, Mapping):
            artifact_metadata = {}
        memory_id = str(artifact_metadata.get("memory_id") or artifact.get("id"))
        source = artifact_metadata.get("source") or artifact.get("identifier")
        content_hash = (
            str(artifact.get("artifact_hash"))
            if _is_sha256(artifact.get("artifact_hash"))
            else None
        )
        key = (memory_id, content_hash, source)
        if key in source_keys:
            continue
        source_keys.add(key)
        sources.append(
            {
                "memory_id": memory_id,
                "source": source,
                "source_version": artifact.get("version"),
                "content": None,
                "content_hash": content_hash,
                "valid_from": None,
                "valid_to": None,
                "recorded_at": artifact.get("recorded_at"),
                "erased_at": None,
            }
        )

    tools = [
        dict(item)
        for item in (metadata.get("tools") or metadata.get("tool_calls") or [])
        if isinstance(item, Mapping)
    ]
    tools_by_key: dict[str, dict[str, Any]] = {}
    for tool in tools:
        for key in (tool.get("tool_id"), tool.get("call_id"), tool.get("name")):
            if isinstance(key, str) and key:
                tools_by_key.setdefault(key, tool)

    def merge_tool_hash(tool: dict[str, Any], field: str, value: Any) -> None:
        if not _is_sha256(value):
            return
        raw_candidates = tool.get(f"{field}_candidates")
        candidates = {
            candidate
            for candidate in raw_candidates
            if _is_sha256(candidate)
        } if isinstance(raw_candidates, list) else set()
        if _is_sha256(tool.get(field)):
            candidates.add(str(tool[field]))
        candidates.add(str(value))
        if len(candidates) == 1:
            tool[field] = next(iter(candidates))
            tool.pop(f"{field}_candidates", None)
        else:
            tool.pop(field, None)
            tool[f"{field}_candidates"] = sorted(candidates)

    for artifact in artifacts_of_kind("tool"):
        artifact_id = str(artifact.get("id"))
        artifact_metadata = artifact.get("metadata")
        if not isinstance(artifact_metadata, Mapping):
            artifact_metadata = {}
        tool_name = artifact_metadata.get("tool_name") or artifact.get("identifier")
        tool_call_id = artifact_metadata.get("tool_call_id")
        tool = next(
            (
                tools_by_key[key]
                for key in (tool_call_id, tool_name, artifact_id)
                if isinstance(key, str) and key in tools_by_key
            ),
            None,
        )
        if tool is None:
            tool = {
                "name": tool_name,
                "tool_id": tool_call_id or artifact_id,
                "version": artifact.get("version"),
            }
            tools.append(tool)
        for key in (tool_call_id, tool_name, artifact_id, tool.get("tool_id")):
            if isinstance(key, str) and key:
                tools_by_key.setdefault(key, tool)
        evidence_hash = artifact.get("artifact_hash")
        evidence_ref = {
            "artifact_id": artifact_id,
            "artifact_hash": evidence_hash,
            "relation": "direct",
            "protocol": artifact_metadata.get("protocol"),
            "event_kind": artifact_metadata.get("event_kind"),
            "hash_role": artifact_metadata.get("hash_role"),
        }
        evidence_refs = tool.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = []
            tool["evidence_refs"] = evidence_refs
        if evidence_ref not in evidence_refs:
            evidence_refs.append(evidence_ref)
        hash_role = artifact_metadata.get("hash_role")
        if hash_role == "definition":
            merge_tool_hash(tool, "definition_hash", evidence_hash)
        if hash_role == "result":
            merge_tool_hash(tool, "result_hash", evidence_hash)

    completeness_metadata = dict(metadata)
    completeness_metadata["system_instruction_hash"] = system_instruction_hash
    completeness_decision = dict(decision_data)
    completeness_decision.update(
        {
            "model_id": model_id,
            "model_version": model_version,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "policy_version": policy_version,
            "metadata": completeness_metadata,
        }
    )
    completeness = assess_completeness(
        completeness_decision,
        sources,
        audit_chain,
        will_sign=will_sign,
        tools_evidence=tools,
    )
    protected: dict[str, Any] = {
        "$schema": RECEIPT_SCHEMA,
        "receipt_version": RECEIPT_VERSION,
        "receipt_id": f"urn:lians:decision-receipt:{decision_data['id']}",
        "issued_at": decision_data.get("recorded_at"),
        "issuer": {
            "name": "Lians",
            "category": "decision_evidence_infrastructure",
            "key_id": signing_key_id if will_sign else None,
        },
        "decision": {
            "id": str(decision_data.get("id")),
            "namespace": decision_data.get("namespace"),
            "type": decision_data.get("decision_type"),
            "outcome": decision_data.get("outcome"),
            "reason_codes": list(decision_data.get("reason_codes") or []),
            "regime": decision_data.get("regime"),
            "subject_id": decision_data.get("subject_id"),
            "decided_at": decision_data.get("decided_at"),
            "recorded_at": decision_data.get("recorded_at"),
            "knowledge_as_of": decision_data.get("knowledge_as_of"),
            "knowledge_recorded_as_of": decision_data.get("knowledge_recorded_as_of"),
            "record_hash": decision_data.get("record_hash"),
            "record_hash_version": decision_data.get("record_hash_version"),
            "record_integrity_status": decision_data.get("record_integrity_status"),
            "supersedes_id": (
                str(decision_data["supersedes_id"])
                if decision_data.get("supersedes_id")
                else None
            ),
        },
        "actor": {
            # This value was supplied by the workload and is not an
            # authentication identity. Keep the old key for v0.1 consumers and
            # name the trust distinction explicitly alongside it.
            "agent_id": decision_data.get("agent_id"),
            "claimed_agent_id": decision_data.get("agent_id"),
            "principal": (
                {
                    "id": decision_data.get("recorded_by_principal_ref"),
                    "auth_method": decision_data.get("recorded_by_auth_method"),
                    "credential_ref": decision_data.get("recorded_by_credential_ref"),
                    "type": recording_write["principal_type"],
                    "role": recording_write["role"],
                    "scopes": recording_write["scopes"],
                }
                if decision_data.get("recorded_by_principal_ref")
                else None
            ),
            "recorded_by": {
                "principal_ref": decision_data.get("recorded_by_principal_ref"),
                "auth_method": decision_data.get("recorded_by_auth_method"),
                "credential_ref": decision_data.get("recorded_by_credential_ref"),
                "principal_type": recording_write["principal_type"],
                "role": recording_write["role"],
                "scopes": recording_write["scopes"],
                "authorization_snapshot_verified": authorization_snapshot is not None,
            },
        },
        "model": {
            "provider": metadata.get("model_provider"),
            "id": model_id,
            "version": model_version,
            "system_instruction_hash": system_instruction_hash,
            "configuration_hash": metadata.get("configuration_hash"),
        },
        "artifacts": {
            "input_hash": input_hash,
            "output_hash": output_hash,
        },
        "tools": tools,
        "sources": sources,
        "policy": {
            "version": policy_version,
            "evaluation": metadata.get("policy_evaluation"),
        },
        "authorization": {
            "recording_write": recording_write,
            "declared_workflow_context": declared_workflow_context,
        },
        "human_review": {
            "status": decision_data.get("human_review_status"),
            "reviewer": decision_data.get("human_reviewer"),
            "reviewed_at": decision_data.get("human_reviewed_at"),
        },
        "correlation": {
            "session_id": decision_data.get("session_id"),
            "trace_id": metadata.get("trace_id"),
            "span_id": metadata.get("span_id"),
        },
        "reconstruction": {
            "knowledge_as_of": decision_data.get("knowledge_as_of"),
            "knowledge_recorded_as_of": decision_data.get("knowledge_recorded_as_of"),
            "snapshot_count": len(snapshot_data),
            "cited_source_count": len(sources),
            "snapshot_manifest": [
                {
                    "memory_id": str(item.get("id")),
                    "content_hash": item.get("content_hash"),
                    "valid_from": item.get("valid_from"),
                    "valid_to": item.get("valid_to"),
                }
                for item in snapshot_data
            ],
        },
        "audit_chain": dict(audit_chain),
        "completeness": completeness,
    }

    receipt_hash = sha256_hex(protected)
    integrity: dict[str, Any] = {
        "hash_algorithm": "sha-256",
        "canonicalization": "json-sort-keys-utf8-v1",
        "receipt_hash": receipt_hash,
        "signature": None,
    }
    return protected | {"integrity": integrity}


def attach_decision_receipt_signature(
    receipt: Mapping[str, Any],
    *,
    signing_key_id: str,
    signing_public_key: str,
    signature_value: str,
) -> dict[str, Any]:
    """Attach a v0.1 Ed25519 signature after verifying it locally.

    Remote providers cannot influence the protected payload or embedded trust
    metadata.  This function rechecks the receipt hash, key ID, public key, and
    signature before returning an externally visible signed envelope.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    integrity = receipt.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("signature") is not None:
        raise ValueError("Decision Receipt must contain one empty integrity.signature slot")
    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    expected_hash = sha256_hex(protected)
    if integrity.get("receipt_hash") != expected_hash:
        raise ValueError("Decision Receipt hash changed before signing")
    issuer = receipt.get("issuer")
    if not isinstance(issuer, Mapping) or issuer.get("key_id") != signing_key_id:
        raise ValueError("Decision Receipt signing key does not match issuer.key_id")

    public_raw = _decode_raw_key(signing_public_key, expected_length=32)
    try:
        signature_raw = base64.b64decode(signature_value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Ed25519 signature must be canonical base64") from exc
    if (
        len(signature_raw) != 64
        or base64.b64encode(signature_raw).decode("ascii") != signature_value
    ):
        raise ValueError("Ed25519 signature must be canonical base64 for exactly 64 bytes")
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature_raw, bytes.fromhex(expected_hash)
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Ed25519 signature failed local verification") from exc

    signed_integrity = dict(integrity)
    signed_integrity["signature"] = {
        "algorithm": "ed25519",
        "key_id": signing_key_id,
        "public_key": base64.b64encode(public_raw).decode("ascii"),
        "value": signature_value,
    }
    return dict(receipt) | {"integrity": signed_integrity}


def build_decision_receipt_for_signing(
    *,
    decision: Mapping[str, Any] | Any,
    knowledge_snapshot: Sequence[Mapping[str, Any] | Any],
    cited_evidence: Sequence[Mapping[str, Any] | Any],
    audit_chain: Mapping[str, Any],
    signing_key_id: str,
    include_source_content: bool = False,
) -> dict[str, Any]:
    """Build a signature-graded envelope for an already validated signer."""
    return _build_decision_receipt_envelope(
        decision=decision,
        knowledge_snapshot=knowledge_snapshot,
        cited_evidence=cited_evidence,
        audit_chain=audit_chain,
        signature_expected=True,
        signing_key_id=signing_key_id,
        include_source_content=include_source_content,
    )


def build_decision_receipt(
    *,
    decision: Mapping[str, Any] | Any,
    knowledge_snapshot: Sequence[Mapping[str, Any] | Any],
    cited_evidence: Sequence[Mapping[str, Any] | Any],
    audit_chain: Mapping[str, Any],
    signing_private_key: str = "",
    signing_key_id: str = "lians-receipt-key",
    include_source_content: bool = False,
) -> dict[str, Any]:
    """Build a receipt with the backwards-compatible local raw-key signer."""
    will_sign = bool(signing_private_key.strip())
    receipt = _build_decision_receipt_envelope(
        decision=decision,
        knowledge_snapshot=knowledge_snapshot,
        cited_evidence=cited_evidence,
        audit_chain=audit_chain,
        signature_expected=will_sign,
        signing_key_id=signing_key_id,
        include_source_content=include_source_content,
    )
    if not will_sign:
        return receipt

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(
        _decode_raw_key(signing_private_key, expected_length=32)
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    receipt_hash = receipt["integrity"]["receipt_hash"]
    signature = private_key.sign(bytes.fromhex(receipt_hash))
    return attach_decision_receipt_signature(
        receipt,
        signing_key_id=signing_key_id,
        signing_public_key=base64.b64encode(public_raw).decode("ascii"),
        signature_value=base64.b64encode(signature).decode("ascii"),
    )


def _summarize_names(values: Sequence[str], *, limit: int = 16) -> str:
    selected = sorted(str(value) for value in values)[:limit]
    suffix = ", ..." if len(values) > limit else ""
    return ", ".join(selected) + suffix


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_evidence_graph_manifest(
    manifest: Mapping[str, Any],
    *,
    decision_id: Any,
    errors: _VerificationErrors,
) -> None:
    """Validate the bounded, independently hashable evidence-graph extension."""

    expected_fields = {
        "schema",
        "decision_id",
        "snapshot_max_link_sequence",
        "entries",
        "links_total",
        "artifacts_total",
        "direct_count",
        "reachable_count",
        "complete",
        "normalization",
        "manifest_hash",
    }
    missing_fields = expected_fields - set(manifest)
    unknown_fields = set(manifest) - expected_fields
    if missing_fields:
        errors.append(
            "evidence graph manifest is missing fields: "
            + _summarize_names(list(missing_fields))
        )
    if unknown_fields:
        errors.append(
            "evidence graph manifest has unknown fields: "
            + _summarize_names(list(unknown_fields))
        )
    if manifest.get("schema") != EVIDENCE_GRAPH_MANIFEST_SCHEMA:
        errors.append("evidence graph manifest schema is unsupported")
    manifest_decision_id = manifest.get("decision_id")
    if not _is_uuid(manifest_decision_id) or manifest_decision_id != decision_id:
        errors.append("evidence graph manifest must bind to decision.id")
    snapshot_sequence = manifest.get("snapshot_max_link_sequence")
    if not _is_nonnegative_integer(snapshot_sequence):
        errors.append("evidence graph snapshot_max_link_sequence is invalid")
    if manifest.get("complete") is not True:
        errors.append("evidence graph manifest must explicitly be complete")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        errors.append("evidence graph manifest entries must be an array")
        entries_for_validation: list[Any] = []
        entries_are_bounded = False
    else:
        entries_are_bounded = len(entries) <= _EVIDENCE_GRAPH_MANIFEST_LIMIT
        if not entries_are_bounded:
            errors.append(
                "evidence graph manifest exceeds the 10000-entry verification limit"
            )
        entries_for_validation = entries[:_EVIDENCE_GRAPH_MANIFEST_LIMIT]

    relation_counts = {"direct": 0, "reachable": 0}
    artifact_ids: set[str] = set()
    link_ids: set[str] = set()
    edge_ids: set[tuple[str, str]] = set()
    prior_sort_key: tuple[str, str] | None = None
    artifact_fields = {
        "id",
        "kind",
        "identifier",
        "version",
        "hash_algorithm",
        "artifact_hash",
        "identity_hash",
        "recorded_at",
        "metadata",
    }
    for entry in entries_for_validation:
        if not isinstance(entry, Mapping):
            errors.append("evidence graph manifest entry must be an object")
            continue
        entry_fields = {"link_id", "relation", "match_basis", "artifact"}
        if set(entry) != entry_fields:
            errors.append("evidence graph manifest entry fields are invalid")
        relation = entry.get("relation")
        if relation not in relation_counts:
            errors.append("evidence graph manifest relation is invalid")
        else:
            relation_counts[relation] += 1
        link_id = entry.get("link_id")
        if not _is_uuid(link_id) or link_id in link_ids:
            errors.append("evidence graph manifest link IDs must be unique UUIDs")
        else:
            link_ids.add(link_id)
        if isinstance(relation, str) and isinstance(link_id, str):
            sort_key = (relation, link_id)
            if prior_sort_key is not None and sort_key <= prior_sort_key:
                errors.append(
                    "evidence graph manifest entries must use canonical relation/link order"
                )
            prior_sort_key = sort_key

        match_basis = entry.get("match_basis")
        if not isinstance(match_basis, list) or len(match_basis) > 100:
            errors.append("evidence graph match_basis is invalid")
        elif (
            any(
                not isinstance(value, str)
                or not value
                or len(value) > 512
                for value in match_basis
            )
            or match_basis != sorted(set(match_basis))
        ):
            errors.append("evidence graph match_basis must be unique canonical strings")

        artifact = entry.get("artifact")
        if not isinstance(artifact, Mapping):
            errors.append("evidence graph manifest artifact must be an object")
            continue
        if set(artifact) != artifact_fields:
            errors.append("evidence graph manifest artifact fields are invalid")
        artifact_id = artifact.get("id")
        if not _is_uuid(artifact_id):
            errors.append("evidence graph artifact id must be a UUID")
        else:
            artifact_ids.add(artifact_id)
            if isinstance(relation, str):
                edge_id = (artifact_id, relation)
                if edge_id in edge_ids:
                    errors.append("evidence graph artifact/relation edges must be unique")
                edge_ids.add(edge_id)
        if artifact.get("kind") not in _EVIDENCE_KINDS:
            errors.append("evidence graph artifact kind is invalid")
        identifier = artifact.get("identifier")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 1_024:
            errors.append("evidence graph artifact identifier is invalid")
        version = artifact.get("version")
        if version is not None and (
            not isinstance(version, str) or not version or len(version) > 512
        ):
            errors.append("evidence graph artifact version is invalid")
        hash_algorithm = artifact.get("hash_algorithm")
        if (
            not isinstance(hash_algorithm, str)
            or not hash_algorithm
            or len(hash_algorithm) > 32
        ):
            errors.append("evidence graph artifact hash_algorithm is invalid")
        artifact_hash = artifact.get("artifact_hash")
        if artifact_hash is not None and (
            not isinstance(artifact_hash, str)
            or not artifact_hash
            or len(artifact_hash) > 256
        ):
            errors.append("evidence graph artifact hash is invalid")
        if hash_algorithm == "sha256" and artifact_hash is not None and not _is_sha256(
            artifact_hash
        ):
            errors.append("evidence graph sha256 artifact hash is invalid")
        if not _is_sha256(artifact.get("identity_hash")):
            errors.append("evidence graph artifact identity_hash is invalid")
        if not _is_timestamp(artifact.get("recorded_at")):
            errors.append("evidence graph artifact recorded_at is invalid")
        metadata = artifact.get("metadata")
        if not isinstance(metadata, Mapping):
            errors.append("evidence graph artifact metadata must be an object")
        else:
            if len(metadata) > len(_PORTABLE_ARTIFACT_METADATA_KEYS) or any(
                key not in _PORTABLE_ARTIFACT_METADATA_KEYS for key in metadata
            ):
                errors.append("evidence graph artifact metadata fields are invalid")
            if any(
                not isinstance(value, (str, bool, int))
                or (isinstance(value, str) and len(value) > 2_048)
                for value in metadata.values()
            ):
                errors.append("evidence graph artifact metadata values are invalid")

    if entries_are_bounded:
        if manifest.get("links_total") != len(entries_for_validation):
            errors.append("evidence graph links_total does not match entries")
        if manifest.get("artifacts_total") != len(artifact_ids):
            errors.append("evidence graph artifacts_total is inconsistent")
        if manifest.get("direct_count") != relation_counts["direct"]:
            errors.append("evidence graph direct_count is inconsistent")
        if manifest.get("reachable_count") != relation_counts["reachable"]:
            errors.append("evidence graph reachable_count is inconsistent")
    for field in ("links_total", "artifacts_total", "direct_count", "reachable_count"):
        if not _is_nonnegative_integer(manifest.get(field)):
            errors.append(f"evidence graph {field} must be a non-negative integer")
    if entries_for_validation and snapshot_sequence == 0:
        errors.append("evidence graph non-empty snapshot must have a registration sequence")

    normalization = manifest.get("normalization")
    normalization_fields = {
        "decision_id",
        "namespace",
        "coverage_sequence",
        "overall_status",
        "normalized_complete",
        "kinds",
        "disclosure",
    }
    if not isinstance(normalization, Mapping):
        errors.append("evidence graph normalization must be an object")
    else:
        if set(normalization) != normalization_fields:
            errors.append("evidence graph normalization fields are invalid")
        if normalization.get("decision_id") != manifest_decision_id:
            errors.append("evidence graph normalization must bind to decision.id")
        namespace = normalization.get("namespace")
        if not isinstance(namespace, str) or not namespace or len(namespace) > 255:
            errors.append("evidence graph normalization namespace is invalid")
        if not _is_nonnegative_integer(normalization.get("coverage_sequence")):
            errors.append("evidence graph normalization coverage_sequence is invalid")
        overall_status = normalization.get("overall_status")
        normalized_complete = normalization.get("normalized_complete")
        if overall_status not in {"unknown", "partial", "complete"}:
            errors.append("evidence graph normalization overall_status is invalid")
        if not isinstance(normalized_complete, bool) or normalized_complete != (
            overall_status == "complete"
        ):
            errors.append("evidence graph normalized_complete is inconsistent")
        disclosure = normalization.get("disclosure")
        if not isinstance(disclosure, str) or not disclosure or len(disclosure) > 4_096:
            errors.append("evidence graph normalization disclosure is invalid")

        kinds = normalization.get("kinds")
        statuses: list[str] = []
        seen_kinds: set[str] = set()
        if not isinstance(kinds, list) or len(kinds) != len(_EVIDENCE_KINDS):
            errors.append("evidence graph normalization must describe all evidence kinds")
            kinds_for_validation: list[Any] = []
        else:
            kinds_for_validation = kinds
        expected_kind_fields = {
            "kind",
            "status",
            "indexer_version",
            "normalization_scope",
            "source_watermark",
            "gap_codes",
            "indexed_artifact_count",
            "assessed_at",
        }
        for kind_row in kinds_for_validation:
            if not isinstance(kind_row, Mapping):
                errors.append("evidence graph normalization kind must be an object")
                continue
            if set(kind_row) != expected_kind_fields:
                errors.append("evidence graph normalization kind fields are invalid")
            kind = kind_row.get("kind")
            if kind not in _EVIDENCE_KINDS or kind in seen_kinds:
                errors.append("evidence graph normalization kinds must be unique")
            elif isinstance(kind, str):
                seen_kinds.add(kind)
            status = kind_row.get("status")
            if status not in {"unknown", "partial", "complete"}:
                errors.append("evidence graph normalization kind status is invalid")
            elif isinstance(status, str):
                statuses.append(status)
            for field in ("indexer_version", "normalization_scope"):
                value = kind_row.get(field)
                if not isinstance(value, str) or not value or len(value) > 255:
                    errors.append(f"evidence graph normalization {field} is invalid")
            source_watermark = kind_row.get("source_watermark")
            if source_watermark is not None and (
                not isinstance(source_watermark, str)
                or not source_watermark
                or len(source_watermark) > 2_048
            ):
                errors.append("evidence graph normalization source_watermark is invalid")
            gap_codes = kind_row.get("gap_codes")
            if (
                not isinstance(gap_codes, list)
                or len(gap_codes) > 32
                or any(
                    not isinstance(code, str) or not code or len(code) > 255
                    for code in gap_codes[:32]
                )
            ):
                errors.append("evidence graph normalization gap_codes are invalid")
            if not _is_nonnegative_integer(kind_row.get("indexed_artifact_count")):
                errors.append(
                    "evidence graph normalization indexed_artifact_count is invalid"
                )
            assessed_at = kind_row.get("assessed_at")
            if assessed_at is not None and not _is_timestamp(assessed_at):
                errors.append("evidence graph normalization assessed_at is invalid")
        if seen_kinds != set(_EVIDENCE_KINDS):
            errors.append("evidence graph normalization kinds are incomplete")
        if len(statuses) == len(_EVIDENCE_KINDS):
            computed_overall = (
                "complete"
                if all(status == "complete" for status in statuses)
                else "partial"
                if any(status == "partial" for status in statuses)
                else "unknown"
            )
            if overall_status != computed_overall:
                errors.append("evidence graph normalization overall_status is inconsistent")

    manifest_core = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    try:
        manifest_hash = sha256_hex(manifest_core)
    except (TypeError, ValueError):
        manifest_hash = None
    if manifest_hash is None or manifest.get("manifest_hash") != manifest_hash:
        errors.append("evidence graph manifest_hash is invalid")


def verify_decision_receipt(
    receipt: Mapping[str, Any],
    *,
    trusted_public_key: str | None = None,
    require_signature: bool = False,
) -> dict[str, Any]:
    """Verify the v0.1 envelope, protected-payload hash, and optional signature."""
    errors = _VerificationErrors()
    required = {
        "$schema",
        "receipt_version",
        "receipt_id",
        "issued_at",
        "issuer",
        "decision",
        "actor",
        "model",
        "artifacts",
        "tools",
        "sources",
        "policy",
        "authorization",
        "human_review",
        "correlation",
        "reconstruction",
        "audit_chain",
        "completeness",
        "integrity",
    }
    missing = sorted(required - set(receipt))
    if missing:
        errors.append("Missing required fields: " + _summarize_names(missing))
    unknown = sorted(set(receipt) - required)
    if unknown:
        errors.append("Unknown v0.1 fields: " + _summarize_names(unknown))
    if receipt.get("$schema") != RECEIPT_SCHEMA:
        errors.append(f"$schema must be {RECEIPT_SCHEMA!r}")
    if receipt.get("receipt_version") != RECEIPT_VERSION:
        errors.append(f"receipt_version must be {RECEIPT_VERSION!r}")

    decision = receipt.get("decision")
    record_hash_version: Any = None
    if not isinstance(decision, Mapping):
        errors.append("decision must be an object")
    else:
        record_hash_version = decision.get("record_hash_version")
        decision_id = decision.get("id")
        expected_receipt_id = f"urn:lians:decision-receipt:{decision_id}"
        if (
            not isinstance(decision_id, str)
            or not decision_id
            or receipt.get("receipt_id") != expected_receipt_id
        ):
            errors.append("receipt_id must bind exactly to decision.id")
        recorded_at = decision.get("recorded_at")
        if (
            not isinstance(recorded_at, str)
            or not recorded_at
            or receipt.get("issued_at") != recorded_at
        ):
            errors.append("issued_at must match decision.recorded_at")
        if record_hash_version not in {2, 3}:
            errors.append("decision.record_hash_version must be 2 or 3")
        if decision.get("record_integrity_status") != "verified":
            errors.append("decision.record_integrity_status must be 'verified'")
        if not _is_sha256(decision.get("record_hash")):
            errors.append("decision.record_hash must be a lowercase SHA-256 digest")

    actor = receipt.get("actor")
    if not isinstance(actor, Mapping):
        errors.append("actor must be an object")
    else:
        claimed_agent_id = actor.get("claimed_agent_id")
        if (
            not isinstance(claimed_agent_id, str)
            or not claimed_agent_id
            or actor.get("agent_id") != claimed_agent_id
        ):
            errors.append("actor.claimed_agent_id must match actor.agent_id")
        recorded_by = actor.get("recorded_by")
        if not isinstance(recorded_by, Mapping):
            errors.append("actor.recorded_by must be an object")
        else:
            principal_ref = recorded_by.get("principal_ref")
            credential_ref = recorded_by.get("credential_ref")
            auth_method = recorded_by.get("auth_method")
            if (
                not isinstance(principal_ref, str)
                or not principal_ref.startswith("lians:principal:v1:")
                or len(principal_ref) <= 20
                or principal_ref == "lians:principal:v1:legacy-unverified"
            ):
                errors.append(
                    "actor.recorded_by.principal_ref must be an authenticated principal"
                )
            if (
                not isinstance(credential_ref, str)
                or not credential_ref.startswith("lians:credential:v1:sha256:")
                or not _is_sha256(credential_ref.rsplit(":", 1)[-1])
            ):
                errors.append(
                    "actor.recorded_by.credential_ref must be a canonical credential reference"
                )
            if (
                not isinstance(auth_method, str)
                or not _AUTH_METHOD.fullmatch(auth_method)
                or auth_method == "legacy_unverified"
            ):
                errors.append("actor.recorded_by.auth_method is invalid")
            principal = actor.get("principal")
            if (
                not isinstance(principal, Mapping)
                or principal.get("id") != principal_ref
                or principal.get("auth_method") != auth_method
                or principal.get("credential_ref") != credential_ref
            ):
                errors.append("actor.principal must match actor.recorded_by")
            if record_hash_version == 3:
                snapshot = _decision_authorization_snapshot(
                    {
                        "record_hash_version": 3,
                        "record_integrity_status": "verified",
                        "recorded_by_principal_ref": principal_ref,
                        "recorded_by_auth_method": recorded_by.get("auth_method"),
                        "recorded_by_credential_ref": credential_ref,
                        "recorded_by_principal_type": recorded_by.get("principal_type"),
                        "recorded_by_role": recorded_by.get("role"),
                        "recorded_by_scopes": recorded_by.get("scopes"),
                    }
                )
                if (
                    snapshot is None
                    or recorded_by.get("authorization_snapshot_verified") is not True
                    or recorded_by.get("scopes") != snapshot["scopes"]
                ):
                    errors.append(
                        "DecisionRecord v3 actor authorization snapshot is invalid"
                    )
                if (
                    snapshot is not None
                    and (
                        not isinstance(principal, Mapping)
                        or principal.get("type") != snapshot["principal_type"]
                        or principal.get("role") != snapshot["role"]
                        or principal.get("scopes") != snapshot["scopes"]
                    )
                ):
                    errors.append(
                        "actor.principal must match the v3 authorization snapshot"
                    )
                authorization = receipt.get("authorization")
                recording_write = (
                    authorization.get("recording_write")
                    if isinstance(authorization, Mapping)
                    else None
                )
                if snapshot is None or recording_write != snapshot:
                    errors.append(
                        "authorization.recording_write must match the v3 record snapshot"
                    )
                declared_context = (
                    authorization.get("declared_workflow_context")
                    if isinstance(authorization, Mapping)
                    else None
                )
                if declared_context is not None and (
                    not isinstance(declared_context, Mapping)
                    or declared_context.get("verified") is not False
                    or declared_context.get("source")
                    != "caller_supplied_decision_metadata"
                ):
                    errors.append(
                        "declared workflow authorization context must be explicitly unverified"
                    )
            elif (
                "authorization_snapshot_verified" in recorded_by
                and recorded_by.get("authorization_snapshot_verified") is not False
            ):
                errors.append(
                    "DecisionRecord v2 cannot claim a verified authorization snapshot"
                )

    if record_hash_version == 3:
        completeness = receipt.get("completeness")
        checks = completeness.get("checks") if isinstance(completeness, Mapping) else None
        missing_checks = (
            completeness.get("missing") if isinstance(completeness, Mapping) else None
        )
        authorization_checks = (
            [
                check
                for check in checks
                if isinstance(check, Mapping)
                and check.get("id") == "authorization.context"
            ]
            if isinstance(checks, list)
            else []
        )
        if (
            len(authorization_checks) != 1
            or authorization_checks[0].get("status") != "present"
            or not isinstance(missing_checks, list)
            or "authorization.context" in missing_checks
        ):
            errors.append(
                "DecisionRecord v3 completeness must report authorization.context present"
            )

    audit_chain = receipt.get("audit_chain")
    if isinstance(audit_chain, Mapping):
        manifest = audit_chain.get("lians_evidence_graph")
        if manifest is not None:
            if not isinstance(manifest, Mapping):
                errors.append("audit_chain.lians_evidence_graph must be an object")
            else:
                _validate_evidence_graph_manifest(
                    manifest,
                    decision_id=(
                        decision.get("id") if isinstance(decision, Mapping) else None
                    ),
                    errors=errors,
                )

    integrity = receipt.get("integrity")
    if not isinstance(integrity, Mapping):
        return {
            "valid": False,
            "hash_valid": False,
            "signature_present": False,
            "signature_valid": False,
            "trusted_key": False,
            "errors": errors + ["integrity must be an object"],
        }

    if integrity.get("hash_algorithm") != "sha-256":
        errors.append("integrity.hash_algorithm must be 'sha-256'")
    if integrity.get("canonicalization") != "json-sort-keys-utf8-v1":
        errors.append("integrity.canonicalization must be 'json-sort-keys-utf8-v1'")

    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    expected_hash: str | None = None
    try:
        expected_hash = sha256_hex(protected)
    except (TypeError, ValueError):
        errors.append("protected payload is not valid canonical JSON")
    hash_valid = expected_hash is not None and integrity.get("receipt_hash") == expected_hash
    if not hash_valid:
        errors.append("receipt_hash does not match the protected payload")

    signature = integrity.get("signature")
    signature_present = isinstance(signature, Mapping)
    signature_shape_valid = signature is None or signature_present
    signature_valid = False
    trusted_key = False
    if not signature_shape_valid:
        errors.append("integrity.signature must be an object or null")
    elif signature_present:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if signature.get("algorithm") != "ed25519":
            errors.append("integrity.signature.algorithm must be 'ed25519'")
        else:
            public_raw: bytes | None = None
            if expected_hash is not None:
                try:
                    public_raw = _decode_raw_key(
                        str(signature.get("public_key", "")), expected_length=32
                    )
                    signature_raw = base64.b64decode(
                        str(signature.get("value", "")), validate=True
                    )
                    Ed25519PublicKey.from_public_bytes(public_raw).verify(
                        signature_raw, bytes.fromhex(expected_hash)
                    )
                    signature_valid = True
                except (binascii.Error, InvalidSignature, TypeError, ValueError):
                    errors.append("Ed25519 signature verification failed")

            if trusted_public_key is not None:
                try:
                    trusted_raw = _decode_raw_key(trusted_public_key, expected_length=32)
                except ValueError:
                    errors.append("trusted public key is not valid raw base64 or hexadecimal")
                else:
                    trusted_key = public_raw is not None and public_raw == trusted_raw
                    if not trusted_key:
                        errors.append("receipt signature does not use the trusted public key")

        issuer = receipt.get("issuer")
        issuer_key_id = issuer.get("key_id") if isinstance(issuer, Mapping) else None
        signature_key_id = signature.get("key_id")
        if not isinstance(signature_key_id, str) or not signature_key_id:
            errors.append("integrity.signature.key_id must be a non-empty string")
        if (
            not isinstance(issuer_key_id, str)
            or not issuer_key_id
            or signature_key_id != issuer_key_id
        ):
            errors.append("integrity.signature.key_id must match issuer.key_id")
    elif require_signature:
        errors.append("a signature is required but the receipt is unsigned")
    if trusted_public_key is not None and not signature_present:
        errors.append("a trusted public key cannot authenticate an unsigned receipt")

    valid = (
        not errors
        and hash_valid
        and signature_shape_valid
        and (not signature_present or signature_valid)
        and (not require_signature or signature_valid)
        and (trusted_public_key is None or trusted_key)
    )
    return {
        "valid": valid,
        "hash_valid": hash_valid,
        "signature_present": signature_present,
        "signature_valid": signature_valid,
        "trusted_key": trusted_key if trusted_public_key is not None else None,
        "receipt_hash": expected_hash,
        "errors": errors,
    }
