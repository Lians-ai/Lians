"""Add normalized, indexed evidence artifacts and decision links.

Revision ID: 0026_evidence_graph
Revises: 0025a_system_time_backfill
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_evidence_graph"
down_revision = "0025a_system_time_backfill"
branch_labels = None
depends_on = None


_KINDS = {
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
}
_ASCII_TRIM = " \t\n\r\f\v"
_ASCII_FOLD = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)
_BACKFILL_BATCH_SIZE = 25
_MAX_JSON_TEXT_BYTES = 1_000_000
_MAX_EVIDENCE_MEMORY_IDS = 256
_MAX_SPECS_PER_FIELD = 256
_MAX_CANDIDATES_PER_DECISION = 256
_PROGRESS_TABLE = "lians_migration_0026_evidence_progress"


def _normalized(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip(_ASCII_TRIM).translate(_ASCII_FOLD)
    return normalized or None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_JSON_TEXT_BYTES:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value[:_MAX_EVIDENCE_MEMORY_IDS]
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_JSON_TEXT_BYTES:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed[:_MAX_EVIDENCE_MEMORY_IDS] if isinstance(parsed, list) else []
    return []


def _risk(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in ("risk_level", "risk_score", "criticality", "impact", "risk_tier")
        if key in metadata
    }


def _declared_risk(metadata: dict[str, Any]) -> tuple[int | None, str | None]:
    levels = {"critical": 88, "high": 74, "medium": 55, "low": 35}
    scores: list[int] = []
    for key in ("risk_level", "criticality", "risk_tier"):
        level = str(metadata.get(key) or "").translate(_ASCII_FOLD)
        if level in levels:
            scores.append(levels[level])
    raw_score = metadata.get("risk_score")
    if isinstance(raw_score, (int, float)):
        scores.append(max(0, min(100, int(raw_score))))
    if not scores:
        return None, None
    score = max(scores)
    level = (
        "critical"
        if score >= 85
        else "high"
        if score >= 70
        else "medium"
        if score >= 45
        else "low"
    )
    return score, level


def _hash_algorithm_for(value: Any) -> str:
    if value is None:
        return "sha256"
    normalized = _normalized(value)
    if normalized and len(normalized) == 64 and all(
        char in "0123456789abcdef" for char in normalized
    ):
        return "sha256"
    return "opaque"


def _lookup_hash(normalized_value: str | None) -> str | None:
    if normalized_value is None:
        return None
    return hashlib.sha256(normalized_value.encode()).hexdigest()


def _normalized_artifact_hash(value: Any, algorithm: str) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip(_ASCII_TRIM)
    if not stripped:
        return None
    normalized_algorithm = algorithm.translate(_ASCII_FOLD).replace("-", "")
    if (
        normalized_algorithm.startswith("sha")
        or normalized_algorithm.startswith("blake")
        or normalized_algorithm in {"md5", "xxh32", "xxh64"}
    ):
        return stripped.translate(_ASCII_FOLD)
    return stripped


def _normalized_hash_algorithm(value: str) -> str:
    normalized = value.strip(_ASCII_TRIM).translate(_ASCII_FOLD)
    compacted = normalized.replace("-", "")
    if compacted.startswith("sha") or compacted.startswith("blake"):
        return compacted
    return normalized


def _identity_hash(
    barrier_group: str | None,
    kind: str,
    identifier: str,
    version: str | None,
    hash_algorithm: str,
    artifact_hash: str | None,
) -> str:
    normalized_algorithm = _normalized_hash_algorithm(hash_algorithm)
    normalized_hash = _normalized_artifact_hash(
        artifact_hash, normalized_algorithm
    )
    canonical = json.dumps(
        {
            "barrier_group": barrier_group,
            "kind": kind.translate(_ASCII_FOLD),
            "identifier": _normalized(identifier),
            "version": _normalized(version),
            "hash_algorithm": (
                normalized_algorithm if normalized_hash is not None else None
            ),
            "artifact_hash": normalized_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe_named_specs(
    kind: str,
    node: Any,
    *,
    limit: int = _MAX_SPECS_PER_FIELD,
    depth: int = 0,
) -> list[dict[str, Any]]:
    if limit <= 0 or depth > 16:
        return []
    if node is None:
        return []
    if isinstance(node, str):
        value = node.strip(_ASCII_TRIM)
        return [{"kind": kind, "identifier": value}] if value else []
    if isinstance(node, list):
        specs: list[dict[str, Any]] = []
        for item in node[:limit]:
            specs.extend(
                _safe_named_specs(
                    kind,
                    item,
                    limit=limit - len(specs),
                    depth=depth + 1,
                )
            )
            if len(specs) >= limit:
                break
        return specs
    if not isinstance(node, dict):
        return []

    if kind == "tool" and isinstance(node.get("function"), dict):
        identifier = node["function"].get("name") or node["function"].get("id")
    else:
        identifier = (
            node.get("identifier")
            or node.get("name")
            or node.get("id")
            or node.get("role")
            or node.get("scope")
        )
    if identifier is not None:
        return [
            {
                "kind": kind,
                "identifier": str(identifier),
                "version": (
                    str(node["version"]) if node.get("version") is not None else None
                ),
                "artifact_hash": node.get("artifact_hash") or node.get("hash"),
                "risk_metadata": _risk(node),
            }
        ]
    if kind == "permission":
        return _safe_named_specs(
            kind,
            node.get("scopes") or node.get("permissions"),
            limit=limit,
            depth=depth + 1,
        )
    return []


def _reachable_specs(
    node: Any,
    *,
    limit: int = _MAX_SPECS_PER_FIELD,
    depth: int = 0,
) -> list[dict[str, Any]]:
    if limit <= 0 or depth > 16:
        return []
    if node is None:
        return []
    if isinstance(node, list):
        specs: list[dict[str, Any]] = []
        for item in node[:limit]:
            specs.extend(
                _reachable_specs(
                    item,
                    limit=limit - len(specs),
                    depth=depth + 1,
                )
            )
            if len(specs) >= limit:
                break
        return specs
    if not isinstance(node, dict):
        return []

    kind = str(node.get("kind") or node.get("type") or "").translate(
        _ASCII_FOLD
    ).rstrip("s")
    if kind in _KINDS:
        identifier = node.get("identifier") or node.get("value") or node.get("name")
        if identifier is None:
            return []
        return [
            {
                "kind": kind,
                "identifier": str(identifier),
                "version": (
                    str(node["version"]) if node.get("version") is not None else None
                ),
                "artifact_hash": node.get("artifact_hash") or node.get("hash"),
                "risk_metadata": _risk(node),
            }
        ]
    specs: list[dict[str, Any]] = []
    for key, value in node.items():
        mapped = key.translate(_ASCII_FOLD).rstrip("s")
        if mapped in _KINDS:
            specs.extend(
                _safe_named_specs(
                    mapped,
                    value,
                    limit=limit - len(specs),
                    depth=depth + 1,
                )
            )
            if len(specs) >= limit:
                break
    return specs


def _backfill() -> None:
    """Backfill one immutable revision-boundary snapshot in committed pages."""
    bind = op.get_bind()
    bind.execute(sa.text("SELECT set_config('app.current_namespace', '__admin__', false)"))
    bind.execute(sa.text("SELECT set_config('agentmem.barrier_group', '', false)"))
    metadata = sa.MetaData()
    decisions = sa.Table("decision_records", metadata, autoload_with=bind)
    memories = sa.Table("memories", metadata, autoload_with=bind)
    artifacts = sa.Table("evidence_artifacts", metadata, autoload_with=bind)
    links = sa.Table("decision_evidence_links", metadata, autoload_with=bind)

    def bounded_json(column: sa.Column[Any], empty_value: str) -> Any:
        """Avoid materializing an unbounded legacy JSON value in the migrator."""
        empty_json = sa.cast(sa.literal(empty_value), column.type)
        return sa.case(
            (
                sa.func.pg_column_size(column) <= _MAX_JSON_TEXT_BYTES,
                sa.case(
                    (
                        sa.func.octet_length(sa.cast(column, sa.Text))
                        <= _MAX_JSON_TEXT_BYTES,
                        column,
                    ),
                    else_=empty_json,
                ),
            ),
            else_=empty_json,
        ).label(column.name)

    progress = bind.execute(
        sa.text(
            f"""SELECT snapshot_max_decision_id, last_decision_id
                FROM public.{_PROGRESS_TABLE}
                WHERE singleton = true"""
        )
    ).mappings().one()
    snapshot_max_decision_id = progress["snapshot_max_decision_id"]
    last_decision_id: uuid.UUID | None = progress["last_decision_id"]
    if snapshot_max_decision_id is None:
        return

    decision_columns = (
        decisions.c.id,
        decisions.c.namespace,
        decisions.c.agent_id,
        decisions.c.barrier_group,
        decisions.c.recorded_at,
        bounded_json(decisions.c.evidence_memory_ids, "[]"),
        decisions.c.model_id,
        decisions.c.model_version,
        decisions.c.policy_version,
        decisions.c.input_hash,
        decisions.c.output_hash,
        bounded_json(decisions.c["metadata"], "{}"),
    )
    memory_columns = (
        memories.c.id,
        memories.c.namespace,
        memories.c.barrier_group,
        memories.c.source,
        memories.c.content_hash,
        bounded_json(memories.c["metadata"], "{}"),
    )
    while True:
        decision_query = (
            sa.select(*decision_columns)
            .where(decisions.c.id <= snapshot_max_decision_id)
            .order_by(decisions.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        )
        if last_decision_id is not None:
            decision_query = decision_query.where(decisions.c.id > last_decision_id)
        batch = bind.execute(decision_query).mappings().all()
        if not batch:
            break
        last_decision_id = batch[-1]["id"]

        referenced_ids: set[uuid.UUID] = set()
        for decision in batch:
            for raw_id in _json_list(decision.get("evidence_memory_ids")):
                try:
                    referenced_ids.add(uuid.UUID(str(raw_id)))
                except (TypeError, ValueError):
                    continue
        memory_rows: dict[str, dict[str, Any]] = {}
        if referenced_ids:
            rows = bind.execute(
                sa.select(*memory_columns).where(memories.c.id.in_(referenced_ids))
            ).mappings()
            memory_rows = {str(row["id"]): dict(row) for row in rows}

        artifact_inserts: list[dict[str, Any]] = []
        link_inserts: list[dict[str, Any]] = []
        link_keys: dict[tuple[str, str, str], int] = {}
        batch_artifact_ids: dict[tuple[str, str], uuid.UUID] = {}
        artifact_identity_by_id: dict[uuid.UUID, tuple[str, str]] = {}

        def artifact_id_for(
            *,
            namespace: str,
            barrier_group: str | None,
            spec: dict[str, Any],
            agent_id: str,
            recorded_at: datetime,
            source_metadata: dict[str, Any] | None = None,
        ) -> uuid.UUID:
            kind = str(spec["kind"]).translate(_ASCII_FOLD)
            identifier = str(spec["identifier"]).strip(_ASCII_TRIM)
            version = (
                str(spec["version"]).strip(_ASCII_TRIM) or None
                if spec.get("version")
                else None
            )
            if version is not None and len(version) > 512:
                version = None
            hash_algorithm = _normalized_hash_algorithm(
                str(
                    spec.get("hash_algorithm")
                    or _hash_algorithm_for(spec.get("artifact_hash"))
                )
            )[:32]
            artifact_hash = _normalized_artifact_hash(
                spec.get("artifact_hash"), hash_algorithm
            )
            if artifact_hash is not None and len(artifact_hash) > 256:
                artifact_hash = None
            identity_hash = _identity_hash(
                barrier_group,
                kind,
                identifier,
                version,
                hash_algorithm,
                artifact_hash,
            )
            cache_key = (namespace, identity_hash)
            artifact_id = batch_artifact_ids.get(cache_key)
            if artifact_id is not None:
                return artifact_id
            # The table is introduced by this migration, so a deterministic
            # identity is safe and lets each bounded keyset page refer to an
            # artifact created by an earlier page without retaining a global
            # in-process cache. It also makes a future resumable backfill
            # idempotent if this migration is split operationally.
            artifact_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"lians:evidence-artifact:{namespace}:{identity_hash}",
            )
            batch_artifact_ids[cache_key] = artifact_id
            artifact_identity_by_id[artifact_id] = cache_key
            identifier_normalized = _normalized(identifier) or ""
            version_normalized = _normalized(version)
            coordinate = (
                f"{identifier_normalized}:{version_normalized}"
                if version_normalized
                else identifier_normalized
            )
            artifact_inserts.append(
                {
                    "id": artifact_id,
                    "namespace": namespace,
                    "barrier_group": barrier_group,
                    "kind": kind,
                    "identifier": identifier,
                    "identifier_normalized": identifier_normalized,
                    "identifier_lookup_hash": _lookup_hash(identifier_normalized),
                    "version": version,
                    "version_normalized": version_normalized,
                    "version_lookup_hash": _lookup_hash(version_normalized),
                    "coordinate": coordinate,
                    "coordinate_lookup_hash": _lookup_hash(coordinate),
                    "hash_algorithm": hash_algorithm,
                    "artifact_hash": artifact_hash,
                    "identity_hash": identity_hash,
                    "metadata": {
                        "backfilled_from": "decision_records",
                        **(source_metadata or {}),
                    },
                    "risk_metadata": dict(spec.get("risk_metadata") or {}),
                    "created_by_agent_id": agent_id,
                    "recorded_at": recorded_at,
                }
            )
            return artifact_id

        for decision in batch:
            namespace = decision["namespace"]
            decision_id = decision["id"]
            decision_barrier = decision.get("barrier_group")
            agent_id = decision["agent_id"]
            recorded_at = decision.get("recorded_at") or datetime.now(timezone.utc)
            decision_metadata = _json_object(decision.get("metadata"))
            decision_risk = _risk(decision_metadata)
            candidates: list[tuple[dict[str, Any], str, list[str], str | None, dict]] = []

            for raw_id in _json_list(decision.get("evidence_memory_ids")):
                try:
                    memory_key = str(uuid.UUID(str(raw_id)))
                except (TypeError, ValueError):
                    continue
                memory = memory_rows.get(memory_key)
                if memory is None or memory.get("namespace") != namespace:
                    continue
                memory_metadata = _json_object(memory.get("metadata"))
                source_metadata = {
                    "memory_id": str(memory["id"]),
                    "source": memory.get("source"),
                }
                for identifier, basis in (
                    (memory.get("source") or f"memory:{memory['id']}", "source.identity"),
                    (str(memory["id"]), "decision.evidence_memory_ids"),
                ):
                    candidates.append(
                        (
                            {
                                "kind": "source",
                                "identifier": identifier,
                                "version": memory_metadata.get("source_version"),
                                "artifact_hash": memory.get("content_hash"),
                                "risk_metadata": _risk(memory_metadata),
                            },
                            "direct",
                            [basis],
                            memory.get("barrier_group"),
                            source_metadata,
                        )
                    )

            if decision.get("model_id"):
                candidates.append(
                    (
                        {
                            "kind": "model",
                            "identifier": decision["model_id"],
                            "version": decision.get("model_version"),
                            "risk_metadata": decision_risk,
                        },
                        "direct",
                        ["decision.model"],
                        decision_barrier,
                        {},
                    )
                )
            if decision.get("policy_version"):
                candidates.append(
                    (
                        {
                            "kind": "policy",
                            "identifier": decision_metadata.get("policy_id")
                            or "decision-policy",
                            "version": decision["policy_version"],
                            "risk_metadata": decision_risk,
                        },
                        "direct",
                        ["decision.policy_version"],
                        decision_barrier,
                        {},
                    )
                )
            for spec in _safe_named_specs(
                "policy", decision_metadata.get("policy_evaluation")
            ):
                candidates.append(
                    (
                        spec,
                        "direct",
                        ["metadata.policy_evaluation"],
                        decision_barrier,
                        {},
                    )
                )
            for kind in ("input", "output"):
                value = decision.get(f"{kind}_hash")
                if value:
                    candidates.append(
                        (
                            {
                                "kind": kind,
                                "identifier": f"decision:{decision_id}:{kind}",
                                "artifact_hash": value,
                                "risk_metadata": decision_risk,
                            },
                            "direct",
                            [f"decision.{kind}_hash"],
                            decision_barrier,
                            {},
                        )
                    )

            for spec in _safe_named_specs(
                "tool", decision_metadata.get("tools") or decision_metadata.get("tool_calls")
            ):
                candidates.append(
                    (spec, "direct", ["metadata.tools"], decision_barrier, {})
                )
            permission_node = (
                decision_metadata.get("permissions")
                or decision_metadata.get("authorization")
                or decision_metadata.get("principal")
            )
            for spec in _safe_named_specs("permission", permission_node):
                candidates.append(
                    (spec, "direct", ["metadata.authorization"], decision_barrier, {})
                )
            instruction_hash = decision_metadata.get(
                "system_instruction_hash"
            ) or decision_metadata.get("instruction_hash")
            if instruction_hash:
                candidates.append(
                    (
                        {
                            "kind": "instruction",
                            "identifier": decision_metadata.get("instruction_id")
                            or "system-instruction",
                            "version": decision_metadata.get("instruction_version"),
                            "artifact_hash": instruction_hash,
                            "risk_metadata": decision_risk,
                        },
                        "direct",
                        ["metadata.system_instruction_hash"],
                        decision_barrier,
                        {},
                    )
                )
            dependency_node = decision_metadata.get(
                "reachable_dependencies"
            ) or decision_metadata.get("dependencies")
            for spec in _reachable_specs(dependency_node):
                candidates.append(
                    (
                        spec,
                        "reachable",
                        ["metadata.reachable_dependencies"],
                        decision_barrier,
                        {},
                    )
                )

            for spec, relation, basis, artifact_barrier, source_metadata in candidates[
                :_MAX_CANDIDATES_PER_DECISION
            ]:
                identifier = str(spec.get("identifier") or "").strip(_ASCII_TRIM)
                if not identifier or len(identifier) > 1024:
                    continue
                if (
                    decision_barrier is not None
                    and artifact_barrier is not None
                    and decision_barrier != artifact_barrier
                ):
                    continue
                artifact_id = artifact_id_for(
                    namespace=namespace,
                    barrier_group=artifact_barrier,
                    spec=spec,
                    agent_id=agent_id,
                    recorded_at=recorded_at,
                    source_metadata=source_metadata,
                )
                link_key = (str(decision_id), str(artifact_id), relation)
                if link_key in link_keys:
                    existing_link = link_inserts[link_keys[link_key]]
                    existing_link["match_basis"] = sorted(
                        {*existing_link["match_basis"], *basis}
                    )
                    existing_link["risk_metadata"] = {
                        **existing_link["risk_metadata"],
                        **decision_risk,
                        **dict(spec.get("risk_metadata") or {}),
                    }
                    risk_score, risk_level = _declared_risk(
                        existing_link["risk_metadata"]
                    )
                    existing_link["risk_score"] = risk_score
                    existing_link["risk_level"] = risk_level
                    continue
                link_keys[link_key] = len(link_inserts)
                link_risk = {**decision_risk, **dict(spec.get("risk_metadata") or {})}
                risk_score, risk_level = _declared_risk(link_risk)
                link_inserts.append(
                    {
                        "id": uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "lians:decision-evidence-link:"
                            f"{namespace}:{decision_id}:{artifact_id}:{relation}",
                        ),
                        "namespace": namespace,
                        "barrier_group": decision_barrier or artifact_barrier,
                        "decision_id": decision_id,
                        "artifact_id": artifact_id,
                        "relation": relation,
                        "match_basis": basis,
                        "risk_metadata": link_risk,
                        "risk_score": risk_score,
                        "risk_level": risk_level,
                        "recorded_at": recorded_at,
                    }
                )

        if artifact_inserts:
            if bind.dialect.name == "postgresql":
                # Use executemany rather than a single multi-VALUES statement:
                # a worst-case bounded page can still exceed PostgreSQL's bind
                # parameter count if every row is compiled into one statement.
                statement = postgresql.insert(artifacts).on_conflict_do_nothing()
                bind.execute(statement, artifact_inserts)
            else:
                bind.execute(artifacts.insert(), artifact_inserts)
        resolved_artifact_ids: dict[tuple[str, str], uuid.UUID] = {}
        identity_pairs = list(batch_artifact_ids)
        if identity_pairs:
            rows = bind.execute(
                sa.select(
                    artifacts.c.id,
                    artifacts.c.namespace,
                    artifacts.c.identity_hash,
                ).where(
                    sa.tuple_(
                        artifacts.c.namespace,
                        artifacts.c.identity_hash,
                    ).in_(identity_pairs)
                )
            ).mappings()
            resolved_artifact_ids = {
                (row["namespace"], row["identity_hash"]): row["id"] for row in rows
            }
            if len(resolved_artifact_ids) != len(identity_pairs):
                raise RuntimeError(
                    "0026a evidence backfill could not resolve every "
                    "conflict-safe artifact identity"
                )
        for link in link_inserts:
            identity = artifact_identity_by_id[link["artifact_id"]]
            actual_artifact_id = resolved_artifact_ids[identity]
            link["artifact_id"] = actual_artifact_id
            link["id"] = uuid.uuid5(
                uuid.NAMESPACE_URL,
                "lians:decision-evidence-link:"
                f"{link['namespace']}:{link['decision_id']}:"
                f"{actual_artifact_id}:{link['relation']}",
            )
        if link_inserts:
            if bind.dialect.name == "postgresql":
                statement = postgresql.insert(links).on_conflict_do_nothing()
                bind.execute(statement, link_inserts)
            else:
                bind.execute(links.insert(), link_inserts)
        bind.execute(
            sa.text(
                f"""UPDATE public.{_PROGRESS_TABLE}
                    SET last_decision_id = :last_decision_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton = true"""
            ),
            {"last_decision_id": last_decision_id},
        )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("evidence_artifacts", "decision_evidence_links"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON {table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )
        op.execute(
            f"""CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )
            WITH CHECK (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )


def upgrade() -> None:
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("identifier", sa.String(length=1024), nullable=False),
        sa.Column("identifier_normalized", sa.Text(), nullable=False),
        sa.Column("identifier_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=512), nullable=True),
        sa.Column("version_normalized", sa.Text(), nullable=True),
        sa.Column("version_lookup_hash", sa.String(length=64), nullable=True),
        sa.Column("coordinate", sa.Text(), nullable=False),
        sa.Column("coordinate_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "hash_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default="sha256",
        ),
        sa.Column("artifact_hash", sa.String(length=256), nullable=True),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("risk_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by_agent_id", sa.String(length=255), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_evidence_artifact_kind",
        ),
        sa.UniqueConstraint(
            "namespace",
            "identity_hash",
            name="uq_evidence_artifact_namespace_identity",
        ),
    )
    op.create_table(
        "decision_evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("match_basis", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("risk_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "relation IN ('direct','reachable')",
            name="ck_decision_evidence_relation",
        ),
        sa.CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_decision_evidence_risk_score",
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('critical','high','medium','low')",
            name="ck_decision_evidence_risk_level",
        ),
        sa.UniqueConstraint(
            "namespace",
            "decision_id",
            "artifact_id",
            "relation",
            name="uq_decision_evidence_edge",
        ),
    )
    for name, table, columns in (
        ("ix_evidence_artifacts_namespace", "evidence_artifacts", ["namespace"]),
        ("ix_evidence_artifacts_barrier_group", "evidence_artifacts", ["barrier_group"]),
        ("ix_evidence_artifacts_kind", "evidence_artifacts", ["kind"]),
        (
            "ix_evidence_artifact_identifier",
            "evidence_artifacts",
            ["namespace", "kind", "identifier_lookup_hash"],
        ),
        (
            "ix_evidence_artifact_version",
            "evidence_artifacts",
            ["namespace", "kind", "version_lookup_hash"],
        ),
        (
            "ix_evidence_artifact_coordinate",
            "evidence_artifacts",
            ["namespace", "kind", "coordinate_lookup_hash"],
        ),
        (
            "ix_evidence_artifact_hash",
            "evidence_artifacts",
            ["namespace", "kind", "artifact_hash"],
        ),
        (
            "ix_evidence_artifact_recent",
            "evidence_artifacts",
            ["namespace", "recorded_at"],
        ),
        (
            "ix_decision_evidence_links_namespace",
            "decision_evidence_links",
            ["namespace"],
        ),
        (
            "ix_decision_evidence_links_barrier_group",
            "decision_evidence_links",
            ["barrier_group"],
        ),
        (
            "ix_decision_evidence_impact",
            "decision_evidence_links",
            ["namespace", "artifact_id", "relation", "risk_score", "decision_id"],
        ),
        (
            "ix_decision_evidence_graph",
            "decision_evidence_links",
            ["namespace", "decision_id", "relation"],
        ),
    ):
        op.create_index(name, table, columns)

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', true)")
        )
        op.create_table(
            _PROGRESS_TABLE,
            sa.Column("singleton", sa.Boolean(), primary_key=True),
            sa.Column(
                "snapshot_max_decision_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "last_decision_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint("singleton", name="ck_0026_progress_singleton"),
        )
        op.execute(
            f"""INSERT INTO public.{_PROGRESS_TABLE} (
                    singleton,
                    snapshot_max_decision_id,
                    last_decision_id
                )
                SELECT true,
                       (
                           SELECT id
                           FROM public.decision_records
                           ORDER BY id DESC
                           LIMIT 1
                       ),
                       NULL"""
        )
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TABLE IF EXISTS public.{_PROGRESS_TABLE}")
        for table in ("decision_evidence_links", "evidence_artifacts"):
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.drop_table("decision_evidence_links")
    op.drop_table("evidence_artifacts")
