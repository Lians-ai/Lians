"""Dependency-aware state invalidation and selective repair for Lians.

Memory retrieval answers what might be relevant. State integrity answers a
different question: when a current fact changes, which dependent memories and
artifacts are no longer safe to reuse? The implementation is deterministic,
local, and model-free. Sensitive dependency references, labels, and repair
evidence are encrypted with the same local key as memory content.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

_DEPENDENT_TYPE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_RELATION = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VALID_RESOLUTIONS = {"repaired", "dismissed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _clean(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    rendered = " ".join(value.strip().split())
    if not rendered:
        raise ValueError(f"{field} cannot be blank")
    if len(rendered) > maximum:
        raise ValueError(f"{field} must be {maximum} characters or fewer")
    return rendered


def _token_estimate(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def initialize_schema(db: sqlite3.Connection) -> None:
    """Install additive state-integrity tables in an existing Lians store."""

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS state_dependencies (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            project_id TEXT,
            upstream_memory_id TEXT NOT NULL REFERENCES memories(id),
            downstream_memory_id TEXT REFERENCES memories(id),
            downstream_type TEXT NOT NULL,
            downstream_ref_cipher BLOB NOT NULL,
            downstream_ref_nonce BLOB NOT NULL,
            downstream_ref_hash TEXT NOT NULL,
            label_cipher BLOB NOT NULL,
            label_nonce BLOB NOT NULL,
            relation TEXT NOT NULL,
            provenance TEXT NOT NULL,
            created_at TEXT NOT NULL,
            retired_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_state_dependencies_upstream
            ON state_dependencies(profile, upstream_memory_id, retired_at);
        CREATE INDEX IF NOT EXISTS idx_state_dependencies_downstream
            ON state_dependencies(profile, downstream_memory_id, retired_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_state_dependencies_active_unique
            ON state_dependencies(profile, upstream_memory_id, downstream_ref_hash, relation)
            WHERE retired_at IS NULL;

        CREATE TABLE IF NOT EXISTS state_invalidations (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            project_id TEXT,
            dependency_id TEXT NOT NULL REFERENCES state_dependencies(id),
            root_trigger_memory_id TEXT NOT NULL REFERENCES memories(id),
            replacement_memory_id TEXT REFERENCES memories(id),
            reason_cipher BLOB NOT NULL,
            reason_nonce BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            evidence_cipher BLOB,
            evidence_nonce BLOB,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_state_invalidations_status
            ON state_invalidations(profile, status, project_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_state_invalidations_open_dependency
            ON state_invalidations(profile, dependency_id)
            WHERE status = 'open';
        """
    )


def _aad(profile: str, record_type: str, record_id: str, field: str) -> bytes:
    return f"lians-state-integrity-v1\0{profile}\0{record_type}\0{record_id}\0{field}".encode()


def _seal(store: Any, record_type: str, record_id: str, field: str, value: str) -> tuple[bytes, bytes]:
    return store.cipher.seal(
        value,
        associated_data=_aad(store.profile, record_type, record_id, field),
    )


def _open(
    store: Any,
    record_type: str,
    record_id: str,
    field: str,
    ciphertext: bytes | None,
    nonce: bytes | None,
) -> str | None:
    if ciphertext is None or nonce is None:
        return None
    return store.cipher.open(
        ciphertext,
        nonce,
        associated_data=_aad(store.profile, record_type, record_id, field),
    )


def _ref_hash(store: Any, dependent_type: str, reference: str) -> str:
    digest = hmac.new(store._search_key, digestmod=hashlib.sha256)
    digest.update(f"{dependent_type}\0{reference}".encode())
    return digest.hexdigest()


def open_invalidated_memory_ids(db: sqlite3.Connection, profile: str) -> set[str]:
    rows = db.execute(
        """SELECT DISTINCT dependencies.downstream_memory_id
           FROM state_invalidations AS invalidations
           JOIN state_dependencies AS dependencies
             ON dependencies.id = invalidations.dependency_id
           WHERE invalidations.profile = ? AND invalidations.status = 'open'
             AND dependencies.downstream_memory_id IS NOT NULL""",
        (profile,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def propagate_invalidation(
    db: sqlite3.Connection,
    store: Any,
    *,
    trigger_memory_id: str,
    replacement_memory_id: str,
    project_id: str | None,
    reason: str,
) -> dict[str, int]:
    """Invalidate every active downstream dependency inside the caller's transaction."""

    queue: deque[tuple[str, int]] = deque([(trigger_memory_id, 0)])
    visited_memories: set[str] = set()
    visited_dependencies: set[str] = set()
    created = 0
    maximum_depth = 0
    while queue:
        upstream_id, depth = queue.popleft()
        if upstream_id in visited_memories:
            continue
        visited_memories.add(upstream_id)
        maximum_depth = max(maximum_depth, depth)
        dependencies = db.execute(
            """SELECT * FROM state_dependencies
               WHERE profile = ? AND upstream_memory_id = ? AND retired_at IS NULL
               ORDER BY created_at ASC, id ASC""",
            (store.profile, upstream_id),
        ).fetchall()
        for dependency in dependencies:
            dependency_id = str(dependency["id"])
            if dependency_id in visited_dependencies:
                continue
            visited_dependencies.add(dependency_id)
            invalidation_id = str(uuid.uuid4())
            reason_cipher, reason_nonce = _seal(
                store,
                "invalidation",
                invalidation_id,
                "reason",
                reason,
            )
            cursor = db.execute(
                """INSERT OR IGNORE INTO state_invalidations
                   (id, profile, project_id, dependency_id, root_trigger_memory_id,
                    replacement_memory_id, reason_cipher, reason_nonce, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    invalidation_id,
                    store.profile,
                    project_id or dependency["project_id"],
                    dependency_id,
                    trigger_memory_id,
                    replacement_memory_id,
                    reason_cipher,
                    reason_nonce,
                    _now(),
                ),
            )
            if cursor.rowcount:
                created += 1
            downstream_memory_id = dependency["downstream_memory_id"]
            if downstream_memory_id:
                queue.append((str(downstream_memory_id), depth + 1))
    return {
        "invalidations_created": created,
        "dependencies_visited": len(visited_dependencies),
        "memory_depth": maximum_depth,
    }


class StateIntegrityService:
    """Manage explicit state dependencies, blast radius, and repair receipts."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def link(
        self,
        upstream_memory_id: str,
        dependent_ref: str,
        *,
        dependent_type: str = "artifact",
        downstream_memory_id: str | None = None,
        project_id: str | None = None,
        label: str | None = None,
        relation: str = "depends_on",
        provenance: str = "explicit",
    ) -> dict[str, Any]:
        [result] = self.link_many(
            upstream_memory_id,
            [
                {
                    "ref": dependent_ref,
                    "type": dependent_type,
                    "downstream_memory_id": downstream_memory_id,
                    "label": label,
                    "relation": relation,
                    "provenance": provenance,
                }
            ],
            project_id=project_id,
        )
        return result

    def link_many(
        self,
        upstream_memory_id: str,
        dependents: Iterable[Mapping[str, Any]],
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        upstream_id = _clean(upstream_memory_id, field="upstream_memory_id", maximum=128)
        prepared: list[dict[str, Any]] = []
        for index, dependent in enumerate(dependents):
            if not isinstance(dependent, Mapping):
                raise TypeError(f"dependents[{index}] must be an object")
            dependent_type = _clean(
                dependent.get("type", "artifact"),
                field=f"dependents[{index}].type",
                maximum=32,
            ).lower()
            if not _DEPENDENT_TYPE.fullmatch(dependent_type):
                raise ValueError("dependent type must use lowercase letters, numbers, _ or -")
            downstream_memory_id = dependent.get("downstream_memory_id")
            if downstream_memory_id is not None:
                downstream_memory_id = _clean(
                    downstream_memory_id,
                    field=f"dependents[{index}].downstream_memory_id",
                    maximum=128,
                )
            reference = _clean(
                dependent.get("ref") or downstream_memory_id,
                field=f"dependents[{index}].ref",
                maximum=1_000,
            )
            label = _clean(
                dependent.get("label") or reference,
                field=f"dependents[{index}].label",
                maximum=500,
            )
            relation = _clean(
                dependent.get("relation", "depends_on"),
                field=f"dependents[{index}].relation",
                maximum=64,
            ).lower()
            if not _RELATION.fullmatch(relation):
                raise ValueError("relation must use lowercase letters, numbers, _ or -")
            provenance = _clean(
                dependent.get("provenance", "explicit"),
                field=f"dependents[{index}].provenance",
                maximum=80,
            )
            prepared.append(
                {
                    "type": dependent_type,
                    "ref": reference,
                    "label": label,
                    "relation": relation,
                    "provenance": provenance,
                    "downstream_memory_id": downstream_memory_id,
                }
            )
        if not prepared:
            raise ValueError("dependents must contain at least one item")
        if len(prepared) > 20_000:
            raise ValueError("dependents must contain 20,000 items or fewer")

        results: list[dict[str, Any]] = []
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            upstream = db.execute(
                "SELECT * FROM memories WHERE id = ? AND profile = ? AND forgotten_at IS NULL",
                (upstream_id, self.store.profile),
            ).fetchone()
            if upstream is None:
                raise LookupError("Upstream memory was not found")
            effective_project_id = project_id or upstream["project_id"]
            if project_id and upstream["project_id"] and project_id != upstream["project_id"]:
                raise ValueError("project_id does not match the upstream memory")
            for item in prepared:
                downstream_id = item["downstream_memory_id"]
                if downstream_id is not None:
                    if downstream_id == upstream_id:
                        raise ValueError("A memory cannot depend on itself")
                    downstream = db.execute(
                        """SELECT id, project_id FROM memories
                           WHERE id = ? AND profile = ? AND forgotten_at IS NULL""",
                        (downstream_id, self.store.profile),
                    ).fetchone()
                    if downstream is None:
                        raise LookupError("Downstream memory was not found")
                    if (
                        effective_project_id
                        and downstream["project_id"]
                        and downstream["project_id"] != effective_project_id
                    ):
                        raise ValueError("Dependency cannot cross project boundaries")
                    cycle = db.execute(
                        """WITH RECURSIVE reachable(memory_id) AS (
                               SELECT ?
                               UNION
                               SELECT dependencies.downstream_memory_id
                               FROM state_dependencies AS dependencies
                               JOIN reachable
                                 ON dependencies.upstream_memory_id = reachable.memory_id
                               WHERE dependencies.profile = ?
                                 AND dependencies.retired_at IS NULL
                                 AND dependencies.downstream_memory_id IS NOT NULL
                           )
                           SELECT 1 FROM reachable WHERE memory_id = ? LIMIT 1""",
                        (downstream_id, self.store.profile, upstream_id),
                    ).fetchone()
                    if cycle is not None:
                        raise ValueError("Dependency would create a memory cycle")
                reference_hash = _ref_hash(self.store, item["type"], item["ref"])
                existing = db.execute(
                    """SELECT * FROM state_dependencies
                       WHERE profile = ? AND upstream_memory_id = ?
                         AND downstream_ref_hash = ? AND relation = ? AND retired_at IS NULL""",
                    (self.store.profile, upstream_id, reference_hash, item["relation"]),
                ).fetchone()
                if existing is not None:
                    results.append(self._public_dependency(existing))
                    continue
                dependency_id = str(uuid.uuid4())
                ref_cipher, ref_nonce = _seal(
                    self.store, "dependency", dependency_id, "ref", item["ref"]
                )
                label_cipher, label_nonce = _seal(
                    self.store, "dependency", dependency_id, "label", item["label"]
                )
                db.execute(
                    """INSERT INTO state_dependencies
                       (id, profile, project_id, upstream_memory_id, downstream_memory_id,
                        downstream_type, downstream_ref_cipher, downstream_ref_nonce,
                        downstream_ref_hash, label_cipher, label_nonce, relation, provenance,
                        created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dependency_id,
                        self.store.profile,
                        effective_project_id,
                        upstream_id,
                        downstream_id,
                        item["type"],
                        ref_cipher,
                        ref_nonce,
                        reference_hash,
                        label_cipher,
                        label_nonce,
                        item["relation"],
                        item["provenance"],
                        _now(),
                    ),
                )
                row = db.execute(
                    "SELECT * FROM state_dependencies WHERE id = ?", (dependency_id,)
                ).fetchone()
                results.append(self._public_dependency(row))
            self.store._activity(
                db,
                "state_dependencies_linked",
                memory_id=upstream_id,
                project_id=effective_project_id,
                details={"count": len(results)},
            )
        return results

    def _public_dependency(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "upstream_memory_id": row["upstream_memory_id"],
            "downstream_memory_id": row["downstream_memory_id"],
            "dependent_type": row["downstream_type"],
            "dependent_ref": _open(
                self.store,
                "dependency",
                row["id"],
                "ref",
                row["downstream_ref_cipher"],
                row["downstream_ref_nonce"],
            ),
            "label": _open(
                self.store,
                "dependency",
                row["id"],
                "label",
                row["label_cipher"],
                row["label_nonce"],
            ),
            "relation": row["relation"],
            "provenance": row["provenance"],
            "created_at": row["created_at"],
            "retired_at": row["retired_at"],
        }

    def invalidations(
        self,
        *,
        status: str = "open",
        project_id: str | None = None,
        root_trigger_memory_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if status not in {"open", "repaired", "dismissed", "all"}:
            raise ValueError("status must be open, repaired, dismissed, or all")
        clauses = ["invalidations.profile = ?"]
        parameters: list[Any] = [self.store.profile]
        if status != "all":
            clauses.append("invalidations.status = ?")
            parameters.append(status)
        if project_id is not None:
            clauses.append("invalidations.project_id = ?")
            parameters.append(project_id)
        if root_trigger_memory_id is not None:
            clauses.append("invalidations.root_trigger_memory_id = ?")
            parameters.append(root_trigger_memory_id)
        parameters.append(max(1, min(int(limit), 20_000)))
        with self.store._connect() as db:
            rows = db.execute(
                f"""SELECT invalidations.*, dependencies.downstream_memory_id,
                           dependencies.downstream_type, dependencies.downstream_ref_cipher,
                           dependencies.downstream_ref_nonce, dependencies.label_cipher,
                           dependencies.label_nonce, dependencies.relation,
                           dependencies.provenance
                    FROM state_invalidations AS invalidations
                    JOIN state_dependencies AS dependencies
                      ON dependencies.id = invalidations.dependency_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY invalidations.created_at DESC LIMIT ?""",
                parameters,
            ).fetchall()
        return [self._public_invalidation(row) for row in rows]

    def invalidation(self, invalidation_id: str, *, status: str = "all") -> dict[str, Any]:
        """Return one invalidation without decrypting an entire blast radius."""

        rendered_id = _clean(invalidation_id, field="invalidation_id", maximum=128)
        if status not in {"open", "repaired", "dismissed", "all"}:
            raise ValueError("status must be open, repaired, dismissed, or all")
        clauses = ["invalidations.id = ?", "invalidations.profile = ?"]
        parameters: list[Any] = [rendered_id, self.store.profile]
        if status != "all":
            clauses.append("invalidations.status = ?")
            parameters.append(status)
        with self.store._connect() as db:
            row = db.execute(
                f"""SELECT invalidations.*, dependencies.downstream_memory_id,
                           dependencies.downstream_type, dependencies.downstream_ref_cipher,
                           dependencies.downstream_ref_nonce, dependencies.label_cipher,
                           dependencies.label_nonce, dependencies.relation,
                           dependencies.provenance
                    FROM state_invalidations AS invalidations
                    JOIN state_dependencies AS dependencies
                      ON dependencies.id = invalidations.dependency_id
                    WHERE {' AND '.join(clauses)}""",
                parameters,
            ).fetchone()
        if row is None:
            raise LookupError("Invalidation was not found")
        return self._public_invalidation(row)

    def invalidation_count(
        self,
        *,
        project_id: str | None = None,
        root_trigger_memory_id: str | None = None,
    ) -> int:
        clauses = ["profile = ?", "status = 'open'"]
        parameters: list[Any] = [self.store.profile]
        if project_id is not None:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        if root_trigger_memory_id is not None:
            clauses.append("root_trigger_memory_id = ?")
            parameters.append(root_trigger_memory_id)
        with self.store._connect() as db:
            row = db.execute(
                f"SELECT COUNT(*) FROM state_invalidations WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
        return int(row[0] or 0)

    def _public_invalidation(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "dependency_id": row["dependency_id"],
            "root_trigger_memory_id": row["root_trigger_memory_id"],
            "replacement_memory_id": row["replacement_memory_id"],
            "downstream_memory_id": row["downstream_memory_id"],
            "dependent_type": row["downstream_type"],
            "dependent_ref": _open(
                self.store,
                "dependency",
                row["dependency_id"],
                "ref",
                row["downstream_ref_cipher"],
                row["downstream_ref_nonce"],
            ),
            "label": _open(
                self.store,
                "dependency",
                row["dependency_id"],
                "label",
                row["label_cipher"],
                row["label_nonce"],
            ),
            "relation": row["relation"],
            "provenance": row["provenance"],
            "reason": _open(
                self.store,
                "invalidation",
                row["id"],
                "reason",
                row["reason_cipher"],
                row["reason_nonce"],
            ),
            "status": row["status"],
            "evidence": _open(
                self.store,
                "invalidation",
                row["id"],
                "evidence",
                row["evidence_cipher"],
                row["evidence_nonce"],
            ),
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    def blast_radius(self, memory_id: str, *, max_depth: int = 20) -> dict[str, Any]:
        root_id = _clean(memory_id, field="memory_id", maximum=128)
        bounded_depth = max(1, min(int(max_depth), 100))
        queue: deque[tuple[str, int]] = deque([(root_id, 0)])
        visited_memories: set[str] = set()
        visited_dependencies: set[str] = set()
        impacts: list[dict[str, Any]] = []
        with self.store._connect() as db:
            root = db.execute(
                """SELECT id FROM memories
                   WHERE id = ? AND profile = ? AND forgotten_at IS NULL""",
                (root_id, self.store.profile),
            ).fetchone()
            if root is None:
                raise LookupError("Memory was not found")
            while queue:
                upstream_id, depth = queue.popleft()
                if upstream_id in visited_memories or depth >= bounded_depth:
                    continue
                visited_memories.add(upstream_id)
                rows = db.execute(
                    """SELECT * FROM state_dependencies
                       WHERE profile = ? AND upstream_memory_id = ? AND retired_at IS NULL
                       ORDER BY created_at ASC, id ASC""",
                    (self.store.profile, upstream_id),
                ).fetchall()
                for row in rows:
                    if row["id"] in visited_dependencies:
                        continue
                    visited_dependencies.add(row["id"])
                    item = self._public_dependency(row)
                    item["depth"] = depth + 1
                    impacts.append(item)
                    if row["downstream_memory_id"]:
                        queue.append((str(row["downstream_memory_id"]), depth + 1))
        return {
            "schema": "https://lians.ai/schemas/state-impact/v0.1",
            "root_memory_id": root_id,
            "impact_count": len(impacts),
            "memory_impact_count": sum(bool(item["downstream_memory_id"]) for item in impacts),
            "maximum_depth": max((item["depth"] for item in impacts), default=0),
            "impacts": impacts,
        }

    def repair_brief(
        self,
        *,
        project_id: str | None = None,
        root_trigger_memory_id: str | None = None,
        max_tokens: int = 768,
    ) -> dict[str, Any]:
        bounded_tokens = max(64, min(int(max_tokens), 4_096))
        impact_count = self.invalidation_count(
            project_id=project_id,
            root_trigger_memory_id=root_trigger_memory_id,
        )
        # A repair context can only hold a bounded number of references. Do not
        # decrypt a 10,000-item blast radius merely to return the first page.
        page_limit = min(500, max(32, bounded_tokens // 4))
        impacts = self.invalidations(
            status="open",
            project_id=project_id,
            root_trigger_memory_id=root_trigger_memory_id,
            limit=page_limit,
        )
        replacement_ids = {
            item["replacement_memory_id"] for item in impacts if item["replacement_memory_id"]
        }
        replacements: list[dict[str, Any]] = []
        if replacement_ids:
            with self.store._connect() as db:
                for memory_id in sorted(replacement_ids):
                    row = db.execute(
                        "SELECT * FROM memories WHERE id = ? AND profile = ?",
                        (memory_id, self.store.profile),
                    ).fetchone()
                    if row is not None:
                        item = self.store._public(row)
                        replacements.append(
                            {
                                "id": item["id"],
                                "memory_key": item["memory_key"],
                                "kind": item["kind"],
                                "content": item["content"],
                                "valid_from": item["valid_from"],
                                "source": item["source"],
                            }
                        )

        selected: list[dict[str, Any]] = []

        def render() -> str:
            lines = [
                "LIANS_STATE_REPAIR_V1",
                "Use CURRENT state. Never reuse INVALID memory. Review listed work only.",
            ]
            lines.extend(
                "CURRENT "
                + json.dumps(
                    [
                        item["id"],
                        item["memory_key"],
                        item["kind"],
                        item["content"],
                        item["valid_from"],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for item in replacements
            )
            lines.extend(
                "REASON "
                + json.dumps(reason, ensure_ascii=False, separators=(",", ":"))
                for reason in dict.fromkeys(
                    str(item.get("reason") or "Current state changed") for item in selected
                )
            )
            lines.extend(
                "INVALID "
                + json.dumps(
                    [
                        item["id"],
                        item["dependent_type"],
                        item["dependent_ref"],
                        item["label"]
                        if item["label"] != item["dependent_ref"]
                        else None,
                        item["downstream_memory_id"]
                        if item["downstream_memory_id"] != item["dependent_ref"]
                        else None,
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for item in selected
            )
            return "\n".join(lines)

        for impact in impacts:
            selected.append(impact)
            if _token_estimate(render()) > bounded_tokens:
                selected.pop()
                break
        context = render() if selected or replacements else ""
        return {
            "schema": "https://lians.ai/schemas/state-repair-brief/v0.1",
            "status": "repair_required" if impact_count else "current",
            "context": context,
            "token_estimate": _token_estimate(context) if context else 0,
            "impact_count": impact_count,
            "included_impact_count": len(selected),
            "omitted_impact_count": max(0, impact_count - len(selected)),
            "current_state": replacements,
            "affected_work": selected,
        }

    def resolve(
        self,
        invalidation_id: str,
        *,
        status: str,
        evidence: str,
        replacement_downstream_memory_id: str | None = None,
    ) -> dict[str, Any]:
        rendered_id = _clean(invalidation_id, field="invalidation_id", maximum=128)
        rendered_status = _clean(status, field="status", maximum=20).lower()
        if rendered_status not in _VALID_RESOLUTIONS:
            raise ValueError("status must be repaired or dismissed")
        rendered_evidence = _clean(evidence, field="evidence", maximum=4_000)
        replacement_downstream_id = (
            _clean(
                replacement_downstream_memory_id,
                field="replacement_downstream_memory_id",
                maximum=128,
            )
            if replacement_downstream_memory_id is not None
            else None
        )
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT invalidations.*, dependencies.downstream_memory_id,
                          dependencies.downstream_type, dependencies.downstream_ref_cipher,
                          dependencies.downstream_ref_nonce, dependencies.downstream_ref_hash,
                          dependencies.label_cipher, dependencies.label_nonce,
                          dependencies.relation, dependencies.provenance,
                          dependencies.upstream_memory_id
                   FROM state_invalidations AS invalidations
                   JOIN state_dependencies AS dependencies
                     ON dependencies.id = invalidations.dependency_id
                   WHERE invalidations.id = ? AND invalidations.profile = ?""",
                (rendered_id, self.store.profile),
            ).fetchone()
            if row is None:
                raise LookupError("Invalidation was not found")
            if row["status"] != "open":
                raise ValueError("Invalidation was already resolved")
            if replacement_downstream_id is not None:
                replacement = db.execute(
                    """SELECT id FROM memories
                       WHERE id = ? AND profile = ? AND forgotten_at IS NULL
                         AND superseded_by_id IS NULL""",
                    (replacement_downstream_id, self.store.profile),
                ).fetchone()
                if replacement is None:
                    raise LookupError("Replacement downstream memory is not current")
            evidence_cipher, evidence_nonce = _seal(
                self.store,
                "invalidation",
                rendered_id,
                "evidence",
                rendered_evidence,
            )
            resolved_at = _now()
            db.execute(
                """UPDATE state_invalidations
                   SET status = ?, evidence_cipher = ?, evidence_nonce = ?, resolved_at = ?
                   WHERE id = ?""",
                (
                    rendered_status,
                    evidence_cipher,
                    evidence_nonce,
                    resolved_at,
                    rendered_id,
                ),
            )
            db.execute(
                "UPDATE state_dependencies SET retired_at = ? WHERE id = ?",
                (resolved_at, row["dependency_id"]),
            )
            if rendered_status == "repaired":
                downstream_memory_id = replacement_downstream_id
                if row["downstream_memory_id"] and downstream_memory_id is None:
                    raise ValueError(
                        "repaired memory invalidations require replacement_downstream_memory_id"
                    )
                new_dependency_id = str(uuid.uuid4())
                ref_value = (
                    downstream_memory_id
                    if downstream_memory_id is not None
                    else _open(
                        self.store,
                        "dependency",
                        row["dependency_id"],
                        "ref",
                        row["downstream_ref_cipher"],
                        row["downstream_ref_nonce"],
                    )
                )
                label_value = _open(
                    self.store,
                    "dependency",
                    row["dependency_id"],
                    "label",
                    row["label_cipher"],
                    row["label_nonce"],
                ) or str(ref_value)
                ref_cipher, ref_nonce = _seal(
                    self.store, "dependency", new_dependency_id, "ref", str(ref_value)
                )
                label_cipher, label_nonce = _seal(
                    self.store, "dependency", new_dependency_id, "label", label_value
                )
                db.execute(
                    """INSERT INTO state_dependencies
                       (id, profile, project_id, upstream_memory_id, downstream_memory_id,
                        downstream_type, downstream_ref_cipher, downstream_ref_nonce,
                        downstream_ref_hash, label_cipher, label_nonce, relation, provenance,
                        created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_dependency_id,
                        self.store.profile,
                        row["project_id"],
                        row["replacement_memory_id"],
                        downstream_memory_id,
                        row["downstream_type"],
                        ref_cipher,
                        ref_nonce,
                        _ref_hash(self.store, row["downstream_type"], str(ref_value)),
                        label_cipher,
                        label_nonce,
                        row["relation"],
                        "repair",
                        resolved_at,
                    ),
                )
            self.store._activity(
                db,
                "state_invalidation_resolved",
                memory_id=row["replacement_memory_id"],
                project_id=row["project_id"],
                details={"invalidation_id": rendered_id, "status": rendered_status},
            )
        return self.invalidation(rendered_id)
