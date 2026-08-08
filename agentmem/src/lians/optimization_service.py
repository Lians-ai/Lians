"""Exact token compilation, traceable context selection, and tool shortlisting."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from .improvement_service import barrier_scope, canonical_json, sha256_json
from .optimization_models import ContextBundle, ToolRegistryVersion, ToolSelectionDecision
from .optimization_schemas import (
    ContextCompileOut,
    ContextCompileRequest,
    ContextLineageItem,
    SelectedTool,
    TokenizerSpec,
    ToolRegistryCreate,
    ToolRegistryOut,
    ToolSelectOut,
    ToolSelectRequest,
)
from .secret_storage import seal_text, unseal_text

_TOKEN_WORD = re.compile(r"[A-Za-z0-9_:-]+")
_CONTEXT_PURPOSE = "improvement-context-bundle"


class OptimizationContractError(ValueError):
    """The optimizer cannot satisfy an exactness, lineage, or safety contract."""


class ExactTokenizer(Protocol):
    spec: TokenizerSpec
    definition_hash: str

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class _TokenizerAdapter:
    spec: TokenizerSpec
    definition_hash: str
    _encode: Any

    def count(self, text: str) -> int:
        return len(self._encode(text))


def load_exact_tokenizer(spec: TokenizerSpec) -> ExactTokenizer:
    """Load only a named, deterministic tokenizer; never fall back to estimates."""

    if spec.engine == "tiktoken":
        try:
            import tiktoken
        except ImportError as exc:
            raise OptimizationContractError(
                "tiktoken is not installed; exact OpenAI token counting is unavailable"
            ) from exc
        try:
            encoding = tiktoken.get_encoding(spec.name)
        except ValueError:
            try:
                encoding = tiktoken.encoding_for_model(spec.name)
            except KeyError as exc:
                raise OptimizationContractError(
                    f"no exact tiktoken encoding is registered for {spec.name}"
                ) from exc
        try:
            package_version = importlib.metadata.version("tiktoken")
        except importlib.metadata.PackageNotFoundError:
            package_version = "unknown"
        definition_hash = sha256_json(
            {
                "engine": "tiktoken",
                "requested_name": spec.name,
                "resolved_name": encoding.name,
                "package_version": package_version,
            }
        )
        return _TokenizerAdapter(
            spec=spec,
            definition_hash=definition_hash,
            _encode=lambda text: encoding.encode(text, disallowed_special=()),
        )

    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise OptimizationContractError(
            "tokenizers is not installed; exact JSON tokenizer counting is unavailable"
        ) from exc
    try:
        definition = json.loads(spec.definition or "")
        canonical_definition = canonical_json(definition)
        tokenizer = Tokenizer.from_str(canonical_definition)
    except Exception as exc:
        raise OptimizationContractError(
            "tokenizer definition is not valid tokenizers JSON"
        ) from exc
    return _TokenizerAdapter(
        spec=spec,
        definition_hash=hashlib.sha256(canonical_definition.encode("utf-8")).hexdigest(),
        _encode=lambda text: tokenizer.encode(text).ids,
    )


def _context_storage_context(row_id: uuid.UUID, namespace: str, content_hash: str) -> str:
    return f"{namespace}:{row_id}:{content_hash}"


def _context_out(row: ContextBundle) -> ContextCompileOut:
    context = unseal_text(
        row.compiled_context_encrypted,
        purpose=_CONTEXT_PURPOSE,
        context=_context_storage_context(row.id, row.namespace, row.compiled_context_hash),
    )
    tokenizer = dict(row.analysis.get("tokenizer") or {})
    return ContextCompileOut(
        id=row.id,
        provider=row.provider,
        model=row.model,
        tokenizer=tokenizer,
        tokenizer_hash=row.tokenizer_hash,
        exact_token_count=True,
        max_tokens=row.max_tokens,
        original_tokens=row.original_tokens,
        compiled_tokens=row.compiled_tokens,
        token_reduction=max(0, row.original_tokens - row.compiled_tokens),
        reduction_ratio=(
            max(0, row.original_tokens - row.compiled_tokens) / row.original_tokens
            if row.original_tokens
            else 0.0
        ),
        compiled_context=context,
        compiled_context_hash=row.compiled_context_hash,
        lineage=row.lineage,
        analysis={key: value for key, value in row.analysis.items() if key != "tokenizer"},
        bundle_hash=row.bundle_hash,
    )


async def compile_context(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ContextCompileRequest,
) -> ContextBundle:
    tokenizer = load_exact_tokenizer(body.tokenizer)
    prepared: list[dict[str, Any]] = []
    seen_hashes: dict[str, str] = {}
    redundant: list[dict[str, str]] = []
    compression_rejected: list[dict[str, str]] = []
    for item in body.items:
        original_hash = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        if item.content_hash is not None and item.content_hash != original_hash:
            raise OptimizationContractError(
                f"context item {item.id} content_hash does not match its content"
            )
        if original_hash in seen_hashes:
            redundant.append({"item_id": item.id, "duplicate_of": seen_hashes[original_hash]})
            if not item.mandatory:
                continue
        else:
            seen_hashes[original_hash] = item.id
        retained_content = item.content
        compression = None
        if item.compression is not None:
            original_count = tokenizer.count(item.content)
            compressed_count = tokenizer.count(item.compression.content)
            if compressed_count < original_count:
                retained_content = item.compression.content
                compression = item.compression.model_dump(mode="json", exclude={"content"})
            else:
                compression_rejected.append(
                    {"item_id": item.id, "reason": "compression_did_not_reduce_exact_tokens"}
                )
        retained_hash = hashlib.sha256(retained_content.encode("utf-8")).hexdigest()
        prepared.append(
            {
                "item": item,
                "content": retained_content,
                "original_hash": original_hash,
                "retained_hash": retained_hash,
                "tokens": tokenizer.count(retained_content),
                "compression": compression,
                "score": (0.65 * item.relevance) + (0.25 * item.freshness) + 0.10,
            }
        )

    prepared.sort(
        key=lambda entry: (
            bool(entry["item"].mandatory),
            float(entry["score"]),
            entry["item"].id,
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    unresolved_contradictions: list[dict[str, str]] = []
    for entry in prepared:
        item = entry["item"]
        conflicts = sorted(
            selected_ids.intersection(item.contradicts)
            | {
                selected_entry["item"].id
                for selected_entry in selected
                if item.id in selected_entry["item"].contradicts
            }
        )
        if conflicts and not item.mandatory:
            excluded.append(
                {"item_id": item.id, "reason": "contradiction_with_higher_priority_item"}
            )
            continue
        if conflicts:
            for conflict in conflicts:
                unresolved_contradictions.append({"left": conflict, "right": item.id})
        candidate_context = body.separator.join(
            [*[selected_entry["content"] for selected_entry in selected], entry["content"]]
        )
        candidate_tokens = tokenizer.count(candidate_context)
        if candidate_tokens > body.max_tokens:
            if item.mandatory:
                raise OptimizationContractError(
                    f"mandatory context item {item.id} cannot fit the exact token budget"
                )
            excluded.append({"item_id": item.id, "reason": "exact_token_budget"})
            continue
        selected.append(entry)
        selected_ids.add(item.id)

    compiled_context = body.separator.join(entry["content"] for entry in selected)
    compiled_tokens = tokenizer.count(compiled_context)
    original_context = body.separator.join(item.content for item in body.items)
    original_tokens = tokenizer.count(original_context)
    content_hash = hashlib.sha256(compiled_context.encode("utf-8")).hexdigest()
    lineage = [
        ContextLineageItem(
            id=entry["item"].id,
            original_content_hash=entry["original_hash"],
            retained_content_hash=entry["retained_hash"],
            evidence_refs=entry["item"].evidence_refs,
            token_count=entry["tokens"],
            compressed=entry["compression"] is not None,
            compression=entry["compression"],
        ).model_dump(mode="json")
        for entry in selected
    ]
    evidence_refs_available = {reference for item in body.items for reference in item.evidence_refs}
    evidence_refs_selected = {
        reference for entry in selected for reference in entry["item"].evidence_refs
    }
    tokenizer_descriptor = {
        "engine": body.tokenizer.engine,
        "name": body.tokenizer.name,
        "definition_hash": tokenizer.definition_hash,
    }
    analysis = {
        "tokenizer": tokenizer_descriptor,
        "selected_item_ids": [entry["item"].id for entry in selected],
        "excluded": excluded,
        "redundancy": redundant,
        "compression_rejected": compression_rejected,
        "unresolved_contradictions": unresolved_contradictions,
        "evidence_coverage": (
            len(evidence_refs_selected) / len(evidence_refs_available)
            if evidence_refs_available
            else 1.0
        ),
        "freshness_mean": (
            sum(entry["item"].freshness for entry in selected) / len(selected) if selected else 0.0
        ),
    }
    row_id = uuid.uuid4()
    bundle_document = {
        "schema": "lians.context-bundle.v1",
        "id": str(row_id),
        "provider": body.provider,
        "model": body.model,
        "tokenizer_hash": tokenizer.definition_hash,
        "max_tokens": body.max_tokens,
        "original_tokens": original_tokens,
        "compiled_tokens": compiled_tokens,
        "compiled_context_hash": content_hash,
        "lineage": lineage,
        "analysis": analysis,
    }
    row = ContextBundle(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        provider=body.provider,
        model=body.model,
        tokenizer_engine=body.tokenizer.engine,
        tokenizer_name=body.tokenizer.name,
        tokenizer_hash=tokenizer.definition_hash,
        max_tokens=body.max_tokens,
        original_tokens=original_tokens,
        compiled_tokens=compiled_tokens,
        compiled_context_encrypted=seal_text(
            compiled_context,
            purpose=_CONTEXT_PURPOSE,
            context=_context_storage_context(row_id, namespace, content_hash),
        ),
        compiled_context_hash=content_hash,
        lineage=lineage,
        analysis=analysis,
        bundle_hash=sha256_json(bundle_document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def context_bundle_out(row: ContextBundle) -> ContextCompileOut:
    return _context_out(row)


def tool_registry_out(row: ToolRegistryVersion) -> ToolRegistryOut:
    return ToolRegistryOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        version=row.version,
        tools=row.tools,
        registry_hash=row.registry_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_tool_registry(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ToolRegistryCreate,
) -> ToolRegistryVersion:
    tools = [tool.model_dump(mode="json") for tool in body.tools]
    document = {
        "schema": "lians.tool-registry.v1",
        "name": body.name,
        "version": body.version,
        "tools": tools,
    }
    row = ToolRegistryVersion(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        name=body.name,
        version=body.version,
        tools=tools,
        registry_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def _slim_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_slim_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    removable = {"description", "examples", "example", "default", "title", "$comment"}
    return {key: _slim_schema(item) for key, item in value.items() if key not in removable}


def _terms(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_WORD.findall(value)}


def tool_selection_out(
    row: ToolSelectionDecision,
    *,
    registry_hash: str,
) -> ToolSelectOut:
    return ToolSelectOut(
        id=row.id,
        registry_version_id=row.registry_version_id,
        registry_hash=registry_hash,
        selected_tools=row.selected_tools,
        excluded_tools=row.excluded_tools,
        failed_loops=row.failed_loops,
        selected_schema_tokens=row.selected_schema_tokens,
        schema_token_budget=row.token_budget,
        tokenizer_hash=row.tokenizer["hash"],
        exact_token_count=True,
        advisory_only=True,
        selection_hash=row.selection_hash,
    )


async def select_tools(
    db: AsyncSession,
    *,
    registry: ToolRegistryVersion,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ToolSelectRequest,
) -> ToolSelectionDecision:
    tokenizer = load_exact_tokenizer(body.tokenizer)
    query_terms = _terms(body.query)
    required_capabilities = set(body.required_capabilities)
    granted = set(body.granted_permission_scopes)
    failed_loops = [
        observation.model_dump(mode="json")
        for observation in body.recent_failures
        if observation.consecutive_count >= body.failed_loop_threshold
    ]
    failed_names = {item["tool_name"] for item in failed_loops}
    excluded: list[dict[str, Any]] = []
    eligible: list[tuple[float, dict[str, Any], list[str]]] = []
    for tool in registry.tools:
        name = str(tool["name"])
        tool_permissions = set(tool.get("required_permission_scopes") or [])
        tool_capabilities = set(tool.get("capabilities") or [])
        reasons: list[str] = []
        if name in failed_names:
            excluded.append({"name": name, "reason": "failed_loop_threshold"})
            continue
        missing_permissions = sorted(tool_permissions - granted)
        if missing_permissions:
            excluded.append(
                {"name": name, "reason": "permission_scope", "missing": missing_permissions}
            )
            continue
        if tool.get("consequential") and not body.allow_consequential:
            excluded.append({"name": name, "reason": "consequential_tool_not_allowed"})
            continue
        required_matches = sorted(required_capabilities.intersection(tool_capabilities))
        if required_matches:
            reasons.append("required_capability:" + ",".join(required_matches))
        tool_terms = _terms(
            " ".join([name, str(tool.get("description") or ""), *tool_capabilities])
        )
        lexical_matches = query_terms.intersection(tool_terms)
        lexical_score = len(lexical_matches) / max(1, len(query_terms))
        capability_score = (
            len(required_matches) / len(required_capabilities) if required_capabilities else 0.0
        )
        score = (0.7 * lexical_score) + (0.3 * capability_score)
        if lexical_matches:
            reasons.append("query_terms:" + ",".join(sorted(lexical_matches)[:20]))
        if not reasons:
            excluded.append({"name": name, "reason": "no_relevance_signal"})
            continue
        eligible.append((score, tool, reasons))
    eligible.sort(key=lambda item: (item[0], str(item[1]["name"])), reverse=True)

    selected: list[dict[str, Any]] = []
    selected_tokens = 0
    covered_capabilities: set[str] = set()
    for score, tool, reasons in eligible:
        if len(selected) >= body.max_tools:
            excluded.append({"name": tool["name"], "reason": "max_tools"})
            continue
        slimmed = _slim_schema(tool.get("input_schema") or {})
        provider_definition = {
            "name": tool["name"],
            "description": tool.get("description"),
            "input_schema": slimmed,
        }
        schema_tokens = tokenizer.count(canonical_json(provider_definition))
        if selected_tokens + schema_tokens > body.schema_token_budget:
            excluded.append({"name": tool["name"], "reason": "schema_token_budget"})
            continue
        selected.append(
            SelectedTool(
                name=tool["name"],
                relevance_score=score,
                selection_reasons=reasons,
                slimmed_input_schema=slimmed,
                original_schema_hash=sha256_json(tool.get("input_schema") or {}),
                slimmed_schema_hash=sha256_json(slimmed),
                schema_tokens=schema_tokens,
                consequential=bool(tool.get("consequential")),
            ).model_dump(mode="json")
        )
        selected_tokens += schema_tokens
        covered_capabilities.update(tool.get("capabilities") or [])
    missing_capabilities = sorted(required_capabilities - covered_capabilities)
    if missing_capabilities:
        raise OptimizationContractError(
            "tool shortlist cannot cover required capabilities inside permission and token constraints: "
            + ", ".join(missing_capabilities)
        )
    query_hash = hashlib.sha256(body.query.encode("utf-8")).hexdigest()
    row_id = uuid.uuid4()
    document = {
        "schema": "lians.tool-selection.v1",
        "id": str(row_id),
        "registry_hash": registry.registry_hash,
        "query_hash": query_hash,
        "tokenizer_hash": tokenizer.definition_hash,
        "token_budget": body.schema_token_budget,
        "selected_tools": selected,
        "excluded_tools": excluded,
        "failed_loops": failed_loops,
        "advisory_only": True,
    }
    row = ToolSelectionDecision(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        registry_version_id=registry.id,
        query_hash=query_hash,
        tokenizer={
            "engine": body.tokenizer.engine,
            "name": body.tokenizer.name,
            "hash": tokenizer.definition_hash,
        },
        token_budget=body.schema_token_budget,
        selected_tools=selected,
        excluded_tools=excluded,
        failed_loops=failed_loops,
        selected_schema_tokens=selected_tokens,
        selection_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


__all__ = [
    "OptimizationContractError",
    "compile_context",
    "context_bundle_out",
    "create_tool_registry",
    "load_exact_tokenizer",
    "select_tools",
    "tool_registry_out",
    "tool_selection_out",
]
