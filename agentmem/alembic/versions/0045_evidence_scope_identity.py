"""Bind evidence relationships to tenant scope and validate canonical identities.

Revision ID: 0045_evidence_scope_identity
Revises: 0044_durable_metering
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import context, op

revision = "0045_evidence_scope_identity"
down_revision = "0044_durable_metering"
branch_labels = None
depends_on = None

_CANONICAL_BATCH_SIZE = 1_000
_CANONICAL_PREFLIGHT_INDEX = "ix_0045_evidence_canonical_identity"


_COMPOSITE_FOREIGN_KEYS = (
    (
        "decision_evidence_links",
        "fk_decision_evidence_link_decision_namespace",
        ["decision_id", "namespace"],
        "decision_records",
        ["id", "namespace"],
        "CASCADE",
    ),
    (
        "decision_evidence_links",
        "fk_decision_evidence_link_artifact_namespace",
        ["artifact_id", "namespace"],
        "evidence_artifacts",
        ["id", "namespace"],
        "RESTRICT",
    ),
    (
        "decision_evidence_link_registrations",
        "fk_evidence_link_registration_link_namespace",
        ["link_id", "namespace"],
        "decision_evidence_links",
        ["id", "namespace"],
        "CASCADE",
    ),
    (
        "decision_evidence_coverage_sets",
        "fk_evidence_coverage_set_decision_namespace",
        ["decision_id", "namespace"],
        "decision_records",
        ["id", "namespace"],
        "CASCADE",
    ),
    (
        "decision_evidence_kind_coverage",
        "fk_evidence_kind_coverage_set_namespace",
        ["coverage_set_sequence", "namespace"],
        "decision_evidence_coverage_sets",
        ["sequence", "namespace"],
        "CASCADE",
    ),
    (
        "decision_evidence_kind_coverage",
        "fk_evidence_kind_coverage_decision_namespace",
        ["decision_id", "namespace"],
        "decision_records",
        ["id", "namespace"],
        "CASCADE",
    ),
    (
        "decision_impact_assessment_jobs",
        "fk_impact_job_completion_event_namespace",
        ["completion_event_id", "namespace"],
        "ledger_events",
        ["id", "namespace"],
        "RESTRICT",
    ),
    (
        "decision_impact_assessment_matches",
        "fk_impact_match_job_namespace",
        ["job_id", "namespace"],
        "decision_impact_assessment_jobs",
        ["id", "namespace"],
        "CASCADE",
    ),
    (
        "decision_impact_assessment_matches",
        "fk_impact_match_decision_namespace",
        ["decision_id", "namespace"],
        "decision_records",
        ["id", "namespace"],
        "RESTRICT",
    ),
)

_PARENT_UNIQUES = (
    ("decision_records", "uq_decision_record_id_namespace", ["id", "namespace"]),
    ("ledger_events", "uq_ledger_event_id_namespace", ["id", "namespace"]),
    (
        "evidence_artifacts",
        "uq_evidence_artifact_id_namespace",
        ["id", "namespace"],
    ),
    (
        "decision_evidence_links",
        "uq_decision_evidence_link_id_namespace",
        ["id", "namespace"],
    ),
    (
        "decision_evidence_coverage_sets",
        "uq_evidence_coverage_set_sequence_namespace",
        ["sequence", "namespace"],
    ),
    (
        "decision_impact_assessment_jobs",
        "uq_impact_job_id_namespace",
        ["id", "namespace"],
    ),
)


def _set_admin_context() -> None:
    # Session scope survives Alembic autocommit blocks used by the resumable
    # pages and concurrent indexes. The dedicated migration connection is
    # disposed immediately after the revision run.
    op.execute(sa.text("SELECT set_config('app.current_namespace', '__admin__', false)"))
    op.execute(sa.text("SELECT set_config('agentmem.barrier_group', '', false)"))


def _drop_foreign_key_for_columns(table: str, columns: Iterable[str]) -> None:
    expected = list(columns)
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(table):
        if list(foreign_key.get("constrained_columns") or []) == expected:
            name = foreign_key.get("name")
            if not name:
                raise RuntimeError(f"Cannot replace unnamed foreign key on {table}")
            op.drop_constraint(name, table, type_="foreignkey")
            return
    # A restart can observe the old key already removed and its composite
    # replacement committed NOT VALID. Treat that state as resumable.


def _install_canonical_functions() -> None:
    op.execute(
        r"""CREATE OR REPLACE FUNCTION public.lians_ascii_trim(value text)
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path = pg_catalog
        AS $$ SELECT btrim(value, E' \t\n\r\f\v') $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_ascii_fold(value text)
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path = pg_catalog
        AS $$
          SELECT translate(
            public.lians_ascii_trim(value),
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
            'abcdefghijklmnopqrstuvwxyz'
          )
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_normalize_hash_algorithm(value text)
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path = pg_catalog
        AS $$
          WITH normalized(value) AS (SELECT public.lians_ascii_fold(value))
          SELECT CASE
            WHEN replace(value, '-', '') LIKE 'sha%'
              OR replace(value, '-', '') LIKE 'blake%'
            THEN replace(value, '-', '')
            ELSE value
          END
          FROM normalized
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_normalize_artifact_hash(
            value text,
            algorithm text
        ) RETURNS text
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            normalized_value text;
            normalized_algorithm text;
        BEGIN
            IF value IS NULL THEN
                RETURN NULL;
            END IF;
            normalized_value := public.lians_ascii_trim(value);
            IF normalized_value = '' THEN
                RETURN NULL;
            END IF;
            normalized_algorithm := public.lians_normalize_hash_algorithm(algorithm);
            IF normalized_algorithm LIKE 'sha%'
               OR normalized_algorithm LIKE 'blake%'
               OR normalized_algorithm IN ('md5', 'xxh32', 'xxh64') THEN
                RETURN public.lians_ascii_fold(normalized_value);
            END IF;
            RETURN normalized_value;
        END
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_sha256_text(value text)
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path = pg_catalog, public
        AS $$
          SELECT encode(public.digest(convert_to(value, 'UTF8'), 'sha256'), 'hex')
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_evidence_identity(
            barrier_group text,
            kind text,
            identifier text,
            version text,
            hash_algorithm text,
            artifact_hash text
        ) RETURNS text
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            normalized_kind text := public.lians_ascii_fold(kind);
            normalized_identifier text := public.lians_ascii_fold(identifier);
            normalized_version text := NULLIF(public.lians_ascii_fold(version), '');
            normalized_algorithm text :=
                public.lians_normalize_hash_algorithm(hash_algorithm);
            normalized_hash text := public.lians_normalize_artifact_hash(
                artifact_hash,
                hash_algorithm
            );
            canonical text;
        BEGIN
            canonical :=
                '{"artifact_hash":' ||
                COALESCE(to_jsonb(normalized_hash)::text, 'null') ||
                ',"barrier_group":' ||
                COALESCE(to_jsonb(barrier_group)::text, 'null') ||
                ',"hash_algorithm":' ||
                COALESCE(
                    to_jsonb(
                        CASE WHEN normalized_hash IS NULL
                             THEN NULL ELSE normalized_algorithm END
                    )::text,
                    'null'
                ) ||
                ',"identifier":' || to_jsonb(normalized_identifier)::text ||
                ',"kind":' || to_jsonb(normalized_kind)::text ||
                ',"version":' ||
                COALESCE(to_jsonb(normalized_version)::text, 'null') ||
                '}';
            RETURN public.lians_sha256_text(canonical);
        END
        $$"""
    )
    for function in (
        "lians_ascii_trim(text)",
        "lians_ascii_fold(text)",
        "lians_normalize_hash_algorithm(text)",
        "lians_normalize_artifact_hash(text,text)",
        "lians_sha256_text(text)",
        "lians_evidence_identity(text,text,text,text,text,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def _artifact_mismatch_sql(alias: str = "artifact") -> str:
    return f"""{alias}.identifier IS DISTINCT FROM
                    public.lians_ascii_trim({alias}.identifier)
             OR {alias}.identifier_normalized IS DISTINCT FROM
                    public.lians_ascii_fold({alias}.identifier)
             OR {alias}.identifier_lookup_hash IS DISTINCT FROM
                    public.lians_sha256_text(
                        public.lians_ascii_fold({alias}.identifier)
                    )
             OR {alias}.version IS DISTINCT FROM
                    NULLIF(public.lians_ascii_trim({alias}.version), '')
             OR {alias}.version_normalized IS DISTINCT FROM
                    NULLIF(public.lians_ascii_fold({alias}.version), '')
             OR {alias}.version_lookup_hash IS DISTINCT FROM CASE
                    WHEN NULLIF(public.lians_ascii_fold({alias}.version), '') IS NULL
                    THEN NULL
                    ELSE public.lians_sha256_text(
                        public.lians_ascii_fold({alias}.version)
                    )
                END
             OR {alias}.kind IS DISTINCT FROM public.lians_ascii_fold({alias}.kind)
             OR {alias}.coordinate IS DISTINCT FROM
                    public.lians_ascii_fold({alias}.identifier) || CASE
                        WHEN NULLIF(
                            public.lians_ascii_fold({alias}.version), ''
                        ) IS NULL THEN ''
                        ELSE ':' || public.lians_ascii_fold({alias}.version)
                    END
             OR {alias}.coordinate_lookup_hash IS DISTINCT FROM
                    public.lians_sha256_text(
                        public.lians_ascii_fold({alias}.identifier) || CASE
                            WHEN NULLIF(
                                public.lians_ascii_fold({alias}.version), ''
                            ) IS NULL THEN ''
                            ELSE ':' || public.lians_ascii_fold({alias}.version)
                        END
                    )
             OR {alias}.hash_algorithm IS DISTINCT FROM
                    public.lians_normalize_hash_algorithm({alias}.hash_algorithm)
             OR {alias}.artifact_hash IS DISTINCT FROM
                    public.lians_normalize_artifact_hash(
                        {alias}.artifact_hash,
                        {alias}.hash_algorithm
                    )
             OR {alias}.identity_hash IS DISTINCT FROM
                    public.lians_evidence_identity(
                        {alias}.barrier_group,
                        {alias}.kind,
                        {alias}.identifier,
                        {alias}.version,
                        {alias}.hash_algorithm,
                        {alias}.artifact_hash
                    )"""


def _install_canonicalization_boundary() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_evidence_append_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            table_owner name;
        BEGIN
            SELECT pg_get_userbyid(relation.relowner)
            INTO table_owner
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = TG_TABLE_SCHEMA
              AND relation.relname = TG_TABLE_NAME;
            IF TG_TABLE_SCHEMA = 'public'
               AND TG_TABLE_NAME = 'evidence_artifacts'
               AND TG_OP = 'UPDATE'
               AND current_setting(
                    'lians.migration_evidence_canonicalization', true
               ) = '0045_evidence_scope_identity'
               AND pg_has_role(current_user, table_owner, 'USAGE') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only; % is forbidden',
                TG_TABLE_NAME, TG_OP;
        END;
        $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validate_evidence_artifact()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            table_owner name;
            expected_identifier text := public.lians_ascii_trim(NEW.identifier);
            expected_identifier_normalized text :=
                public.lians_ascii_fold(NEW.identifier);
            expected_version text := NULLIF(public.lians_ascii_trim(NEW.version), '');
            expected_version_normalized text :=
                NULLIF(public.lians_ascii_fold(NEW.version), '');
            expected_kind text := public.lians_ascii_fold(NEW.kind);
            expected_algorithm text :=
                public.lians_normalize_hash_algorithm(NEW.hash_algorithm);
            expected_artifact_hash text := public.lians_normalize_artifact_hash(
                NEW.artifact_hash,
                NEW.hash_algorithm
            );
            expected_coordinate text;
        BEGIN
            SELECT pg_get_userbyid(relation.relowner)
            INTO table_owner
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = TG_TABLE_SCHEMA
              AND relation.relname = TG_TABLE_NAME;
            IF TG_OP = 'UPDATE'
               AND current_setting(
                    'lians.migration_evidence_canonicalization', true
               ) = '0045_evidence_scope_identity'
               AND pg_has_role(session_user, table_owner, 'USAGE') THEN
                RETURN NEW;
            END IF;
            expected_coordinate := expected_identifier_normalized || CASE
                WHEN expected_version_normalized IS NULL THEN ''
                ELSE ':' || expected_version_normalized
            END;
            IF expected_identifier = ''
               OR NEW.identifier IS DISTINCT FROM expected_identifier
               OR NEW.identifier_normalized IS DISTINCT FROM
                    expected_identifier_normalized
               OR NEW.identifier_lookup_hash IS DISTINCT FROM
                    public.lians_sha256_text(expected_identifier_normalized)
               OR NEW.version IS DISTINCT FROM expected_version
               OR NEW.version_normalized IS DISTINCT FROM expected_version_normalized
               OR NEW.version_lookup_hash IS DISTINCT FROM (CASE
                    WHEN expected_version_normalized IS NULL THEN NULL
                    ELSE public.lians_sha256_text(expected_version_normalized)
                  END)
               OR NEW.kind IS DISTINCT FROM expected_kind
               OR NEW.coordinate IS DISTINCT FROM expected_coordinate
               OR NEW.coordinate_lookup_hash IS DISTINCT FROM
                    public.lians_sha256_text(expected_coordinate)
               OR NEW.hash_algorithm IS DISTINCT FROM expected_algorithm
               OR NEW.artifact_hash IS DISTINCT FROM expected_artifact_hash
               OR NEW.identity_hash IS DISTINCT FROM public.lians_evidence_identity(
                    NEW.barrier_group,
                    NEW.kind,
                    NEW.identifier,
                    NEW.version,
                    NEW.hash_algorithm,
                    NEW.artifact_hash
                  ) THEN
                RAISE EXCEPTION 'evidence artifact identity is not canonical';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    for function in (
        "lians_evidence_append_reject_mutation",
        "lians_validate_evidence_artifact",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_evidence_artifact_validate "
        "ON public.evidence_artifacts"
    )
    op.execute(
        """CREATE TRIGGER trg_evidence_artifact_validate
        BEFORE INSERT OR UPDATE ON public.evidence_artifacts
        FOR EACH ROW EXECUTE FUNCTION public.lians_validate_evidence_artifact()"""
    )


def _restore_strict_append_boundary() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_evidence_append_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is forbidden',
                TG_TABLE_NAME, TG_OP;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_evidence_append_reject_mutation() FROM PUBLIC"
    )


def _index_valid(index_name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid AND index.indisunique
               FROM pg_index AS index
               JOIN pg_class AS relation ON relation.oid = index.indexrelid
               JOIN pg_namespace AS namespace
                 ON namespace.oid = relation.relnamespace
               WHERE namespace.nspname = 'public'
                 AND relation.relname = :index_name"""
        ),
        {"index_name": index_name},
    ).scalar_one_or_none()


def _create_or_repair_canonical_preflight_index() -> None:
    valid = _index_valid(_CANONICAL_PREFLIGHT_INDEX)
    if valid is False:
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS public.{_CANONICAL_PREFLIGHT_INDEX}"
        )
        valid = None
    if valid is None:
        op.execute(
            f"""CREATE UNIQUE INDEX CONCURRENTLY {_CANONICAL_PREFLIGHT_INDEX}
            ON public.evidence_artifacts (
                namespace,
                public.lians_evidence_identity(
                    barrier_group,
                    kind,
                    identifier,
                    version,
                    hash_algorithm,
                    artifact_hash
                )
            )"""
        )


def _install_canonical_page_function() -> None:
    mismatch = _artifact_mismatch_sql("artifact")
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_0045_canonicalize_page(
            batch_size integer
        ) RETURNS integer
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            target_ids uuid[];
        BEGIN
            IF batch_size < 1 OR batch_size > {_CANONICAL_BATCH_SIZE} THEN
                RAISE EXCEPTION 'invalid 0045 canonicalization batch size';
            END IF;
            PERFORM set_config(
                'lians.migration_evidence_canonicalization',
                '0045_evidence_scope_identity',
                true
            );
            SELECT array_agg(target.id ORDER BY target.id)
            INTO target_ids
            FROM (
                SELECT artifact.id
                FROM public.evidence_artifacts AS artifact
                WHERE {mismatch}
                ORDER BY artifact.id
                LIMIT batch_size
                FOR UPDATE SKIP LOCKED
            ) AS target;
            IF target_ids IS NULL THEN
                RETURN 0;
            END IF;
            UPDATE public.evidence_artifacts AS artifact
            SET identity_hash = public.lians_sha256_text(
                'lians:0045:temporary:' || artifact.id::text
            )
            WHERE artifact.id = ANY(target_ids);
            UPDATE public.evidence_artifacts AS artifact
            SET identifier = public.lians_ascii_trim(artifact.identifier),
                identifier_normalized = public.lians_ascii_fold(artifact.identifier),
                identifier_lookup_hash = public.lians_sha256_text(
                    public.lians_ascii_fold(artifact.identifier)
                ),
                version = NULLIF(public.lians_ascii_trim(artifact.version), ''),
                version_normalized = NULLIF(
                    public.lians_ascii_fold(artifact.version), ''
                ),
                version_lookup_hash = CASE
                    WHEN NULLIF(
                        public.lians_ascii_fold(artifact.version), ''
                    ) IS NULL THEN NULL
                    ELSE public.lians_sha256_text(
                        public.lians_ascii_fold(artifact.version)
                    )
                END,
                kind = public.lians_ascii_fold(artifact.kind),
                coordinate = public.lians_ascii_fold(artifact.identifier) || CASE
                    WHEN NULLIF(
                        public.lians_ascii_fold(artifact.version), ''
                    ) IS NULL THEN ''
                    ELSE ':' || public.lians_ascii_fold(artifact.version)
                END,
                coordinate_lookup_hash = public.lians_sha256_text(
                    public.lians_ascii_fold(artifact.identifier) || CASE
                        WHEN NULLIF(
                            public.lians_ascii_fold(artifact.version), ''
                        ) IS NULL THEN ''
                        ELSE ':' || public.lians_ascii_fold(artifact.version)
                    END
                ),
                hash_algorithm = public.lians_normalize_hash_algorithm(
                    artifact.hash_algorithm
                ),
                artifact_hash = public.lians_normalize_artifact_hash(
                    artifact.artifact_hash,
                    artifact.hash_algorithm
                ),
                identity_hash = public.lians_evidence_identity(
                    artifact.barrier_group,
                    artifact.kind,
                    artifact.identifier,
                    artifact.version,
                    artifact.hash_algorithm,
                    artifact.artifact_hash
                )
            WHERE artifact.id = ANY(target_ids);
            RETURN cardinality(target_ids);
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_0045_canonicalize_page(integer) FROM PUBLIC"
    )


def _canonicalize_artifacts() -> None:
    bind = op.get_bind()
    blank = bind.execute(
        sa.text(
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.evidence_artifacts
                   WHERE public.lians_ascii_trim(identifier) = ''
                      OR public.lians_ascii_trim(hash_algorithm) = ''
               )"""
        )
    ).scalar_one()
    if blank:
        raise RuntimeError(
            "0045_evidence_scope_identity found blank immutable identity fields; "
            "repair them explicitly before rerunning the online migration."
        )
    mismatch = _artifact_mismatch_sql("artifact")
    mismatches_exist = bind.execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1
                    FROM public.evidence_artifacts AS artifact
                    WHERE {mismatch}
                )"""
        )
    ).scalar_one()
    if not mismatches_exist:
        _restore_strict_append_boundary()
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_0045_canonicalize_page(integer)"
        )
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS "
            f"public.{_CANONICAL_PREFLIGHT_INDEX}"
        )
        return

    _create_or_repair_canonical_preflight_index()
    _install_canonical_page_function()
    while True:
        processed = bind.execute(
            sa.text("SELECT public.lians_0045_canonicalize_page(:batch_size)"),
            {"batch_size": _CANONICAL_BATCH_SIZE},
        ).scalar_one()
        if processed == 0:
            break
    remaining = bind.execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1
                    FROM public.evidence_artifacts AS artifact
                    WHERE {mismatch}
                )"""
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "0045_evidence_scope_identity could not drain every noncanonical "
            "artifact because a row remains locked; rerun the online migration."
        )
    _restore_strict_append_boundary()
    op.execute("DROP FUNCTION public.lians_0045_canonicalize_page(integer)")
    op.execute(
        f"DROP INDEX CONCURRENTLY IF EXISTS public.{_CANONICAL_PREFLIGHT_INDEX}"
    )


def _validate_existing_relationships() -> None:
    checks = (
        (
            "decision evidence links contain cross-scope parents",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_evidence_links link
                   LEFT JOIN public.decision_records decision
                     ON decision.id = link.decision_id
                    AND decision.namespace = link.namespace
                   LEFT JOIN public.evidence_artifacts artifact
                     ON artifact.id = link.artifact_id
                    AND artifact.namespace = link.namespace
                   WHERE decision.id IS NULL
                      OR artifact.id IS NULL
                      OR (
                          decision.barrier_group IS NOT NULL
                          AND artifact.barrier_group IS NOT NULL
                          AND decision.barrier_group <> artifact.barrier_group
                      )
                      OR link.barrier_group IS DISTINCT FROM COALESCE(
                          decision.barrier_group,
                          artifact.barrier_group
                      )
               )""",
        ),
        (
            "evidence link registrations contain cross-scope parents",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_evidence_link_registrations registration
                   LEFT JOIN public.decision_evidence_links link
                     ON link.id = registration.link_id
                    AND link.namespace = registration.namespace
                    AND link.barrier_group IS NOT DISTINCT FROM
                        registration.barrier_group
                   WHERE link.id IS NULL
               )""",
        ),
        (
            "evidence coverage sets contain cross-scope decisions",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_evidence_coverage_sets coverage
                   LEFT JOIN public.decision_records decision
                     ON decision.id = coverage.decision_id
                    AND decision.namespace = coverage.namespace
                    AND decision.barrier_group IS NOT DISTINCT FROM
                        coverage.barrier_group
                   WHERE decision.id IS NULL
               )""",
        ),
        (
            "kind coverage rows contain cross-scope parents",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_evidence_kind_coverage kind_coverage
                   LEFT JOIN public.decision_evidence_coverage_sets coverage
                     ON coverage.sequence = kind_coverage.coverage_set_sequence
                    AND coverage.namespace = kind_coverage.namespace
                    AND coverage.decision_id = kind_coverage.decision_id
                    AND coverage.barrier_group IS NOT DISTINCT FROM
                        kind_coverage.barrier_group
                   WHERE coverage.sequence IS NULL
               )""",
        ),
        (
            "impact matches contain cross-scope parents",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_impact_assessment_matches match
                   LEFT JOIN public.decision_impact_assessment_jobs job
                     ON job.id = match.job_id
                    AND job.namespace = match.namespace
                    AND job.barrier_group IS NOT DISTINCT FROM
                        match.job_barrier_group
                   LEFT JOIN public.decision_records decision
                     ON decision.id = match.decision_id
                    AND decision.namespace = match.namespace
                   WHERE job.id IS NULL OR decision.id IS NULL
               )""",
        ),
        (
            "impact jobs contain cross-scope completion events",
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.decision_impact_assessment_jobs job
                   LEFT JOIN public.ledger_events event
                     ON event.id = job.completion_event_id
                    AND event.namespace = job.namespace
                    AND event.barrier_group IS NOT DISTINCT FROM job.barrier_group
                   WHERE job.completion_event_id IS NOT NULL
                     AND event.id IS NULL
               )""",
        ),
    )
    bind = op.get_bind()
    for message, query in checks:
        if bind.execute(sa.text(query)).scalar_one():
            raise RuntimeError(f"0045_evidence_scope_identity: {message}")


def _install_scope_trigger() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validate_evidence_relationship_scope()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF TG_TABLE_NAME = 'decision_evidence_links' THEN
            IF NOT EXISTS (
              SELECT 1
              FROM public.decision_records decision
              JOIN public.evidence_artifacts artifact
                ON artifact.id = NEW.artifact_id
               AND artifact.namespace = NEW.namespace
              WHERE decision.id = NEW.decision_id
                AND decision.namespace = NEW.namespace
                AND (
                    decision.barrier_group IS NULL
                    OR artifact.barrier_group IS NULL
                    OR decision.barrier_group = artifact.barrier_group
                )
                AND NEW.barrier_group IS NOT DISTINCT FROM COALESCE(
                    decision.barrier_group,
                    artifact.barrier_group
                )
            ) THEN
              RAISE EXCEPTION 'decision evidence link scope does not match its parents';
            END IF;
          ELSIF TG_TABLE_NAME = 'decision_evidence_link_registrations' THEN
            IF NOT EXISTS (
              SELECT 1 FROM public.decision_evidence_links link
              WHERE link.id = NEW.link_id
                AND link.namespace = NEW.namespace
                AND link.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
            ) THEN
              RAISE EXCEPTION 'evidence link registration scope does not match its link';
            END IF;
          ELSIF TG_TABLE_NAME = 'decision_evidence_coverage_sets' THEN
            IF NOT EXISTS (
              SELECT 1 FROM public.decision_records decision
              WHERE decision.id = NEW.decision_id
                AND decision.namespace = NEW.namespace
                AND decision.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
            ) THEN
              RAISE EXCEPTION 'evidence coverage scope does not match its decision';
            END IF;
          ELSIF TG_TABLE_NAME = 'decision_evidence_kind_coverage' THEN
            IF NOT EXISTS (
              SELECT 1 FROM public.decision_evidence_coverage_sets coverage
              WHERE coverage.sequence = NEW.coverage_set_sequence
                AND coverage.namespace = NEW.namespace
                AND coverage.decision_id = NEW.decision_id
                AND coverage.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
            ) THEN
              RAISE EXCEPTION 'kind coverage scope does not match its coverage set';
            END IF;
          ELSIF TG_TABLE_NAME = 'decision_impact_assessment_matches' THEN
            IF NOT EXISTS (
              SELECT 1
              FROM public.decision_impact_assessment_jobs job
              JOIN public.decision_records decision
                ON decision.id = NEW.decision_id
               AND decision.namespace = NEW.namespace
              WHERE job.id = NEW.job_id
                AND job.namespace = NEW.namespace
                AND job.barrier_group IS NOT DISTINCT FROM NEW.job_barrier_group
            ) THEN
              RAISE EXCEPTION 'impact match scope does not match its parents';
            END IF;
          ELSIF TG_TABLE_NAME = 'decision_impact_assessment_jobs'
                AND NEW.completion_event_id IS NOT NULL THEN
            IF NOT EXISTS (
              SELECT 1 FROM public.ledger_events event
              WHERE event.id = NEW.completion_event_id
                AND event.namespace = NEW.namespace
                AND event.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
            ) THEN
              RAISE EXCEPTION 'impact completion event scope does not match its job';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_validate_evidence_relationship_scope() FROM PUBLIC"
    )
    for table in (
        "decision_evidence_links",
        "decision_evidence_link_registrations",
        "decision_evidence_coverage_sets",
        "decision_evidence_kind_coverage",
        "decision_impact_assessment_jobs",
        "decision_impact_assessment_matches",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_validate_scope "
            f"ON public.{table}"
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table}_validate_scope
            BEFORE INSERT OR UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION
                public.lians_validate_evidence_relationship_scope()"""
        )


def _constraint_state(table: str, name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            """SELECT constraint_record.convalidated
               FROM pg_constraint AS constraint_record
               JOIN pg_class AS relation
                 ON relation.oid = constraint_record.conrelid
               JOIN pg_namespace AS namespace
                 ON namespace.oid = relation.relnamespace
               WHERE namespace.nspname = 'public'
                 AND relation.relname = :table_name
                 AND constraint_record.conname = :constraint_name"""
        ),
        {"table_name": table, "constraint_name": name},
    ).scalar_one_or_none()


def _create_or_repair_parent_unique_indexes() -> None:
    for table, name, columns in _PARENT_UNIQUES:
        if _constraint_state(table, name) is not None:
            continue
        valid = _index_valid(name)
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            valid = None
        if valid is None:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {name} "
                f"ON public.{table} ({', '.join(columns)})"
            )


def _attach_parent_unique_constraints() -> None:
    for table, name, _columns in _PARENT_UNIQUES:
        if _constraint_state(table, name) is None:
            op.execute(
                f"ALTER TABLE public.{table} ADD CONSTRAINT {name} "
                f"UNIQUE USING INDEX {name}"
            )


def _install_composite_foreign_keys_not_valid() -> None:
    for table, name, columns, target, remote, ondelete in _COMPOSITE_FOREIGN_KEYS:
        if _constraint_state(table, name) is not None:
            continue
        op.execute(
            f"ALTER TABLE public.{table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({', '.join(columns)}) "
            f"REFERENCES public.{target} ({', '.join(remote)}) "
            f"ON DELETE {ondelete} NOT VALID"
        )


def _validate_composite_foreign_keys() -> None:
    for table, name, _columns, _target, _remote, _ondelete in _COMPOSITE_FOREIGN_KEYS:
        if _constraint_state(table, name) is False:
            op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def _upgrade_postgresql() -> None:
    _set_admin_context()
    _install_canonical_functions()
    _install_canonicalization_boundary()
    _install_scope_trigger()
    with op.get_context().autocommit_block():
        _canonicalize_artifacts()
        _validate_existing_relationships()
        _create_or_repair_parent_unique_indexes()
    _attach_parent_unique_constraints()
    for table, _name, columns, _target, _remote, _ondelete in _COMPOSITE_FOREIGN_KEYS:
        _drop_foreign_key_for_columns(table, columns[:1])
    _install_composite_foreign_keys_not_valid()
    with op.get_context().autocommit_block():
        _validate_composite_foreign_keys()


def _downgrade_postgresql() -> None:
    _set_admin_context()
    for table in (
        "decision_evidence_links",
        "decision_evidence_link_registrations",
        "decision_evidence_coverage_sets",
        "decision_evidence_kind_coverage",
        "decision_impact_assessment_jobs",
        "decision_impact_assessment_matches",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_validate_scope ON public.{table}")
    op.execute("DROP FUNCTION public.lians_validate_evidence_relationship_scope()")

    for table, name, _columns, _target, _remote, _ondelete in reversed(
        _COMPOSITE_FOREIGN_KEYS
    ):
        op.drop_constraint(name, table, type_="foreignkey")
    legacy_foreign_keys = (
        ("decision_evidence_links", "decision_id", "decision_records", "id", "CASCADE"),
        ("decision_evidence_links", "artifact_id", "evidence_artifacts", "id", "RESTRICT"),
        (
            "decision_evidence_link_registrations",
            "link_id",
            "decision_evidence_links",
            "id",
            "CASCADE",
        ),
        (
            "decision_evidence_coverage_sets",
            "decision_id",
            "decision_records",
            "id",
            "CASCADE",
        ),
        (
            "decision_evidence_kind_coverage",
            "coverage_set_sequence",
            "decision_evidence_coverage_sets",
            "sequence",
            "CASCADE",
        ),
        (
            "decision_evidence_kind_coverage",
            "decision_id",
            "decision_records",
            "id",
            "CASCADE",
        ),
        (
            "decision_impact_assessment_jobs",
            "completion_event_id",
            "ledger_events",
            "id",
            "RESTRICT",
        ),
        (
            "decision_impact_assessment_matches",
            "job_id",
            "decision_impact_assessment_jobs",
            "id",
            "CASCADE",
        ),
        (
            "decision_impact_assessment_matches",
            "decision_id",
            "decision_records",
            "id",
            "RESTRICT",
        ),
    )
    for index, (table, column, target, remote, ondelete) in enumerate(
        legacy_foreign_keys,
        start=1,
    ):
        op.create_foreign_key(
            f"fk_0045_legacy_{index}",
            table,
            target,
            [column],
            [remote],
            ondelete=ondelete,
        )
    for table, name, _columns in reversed(_PARENT_UNIQUES):
        op.drop_constraint(name, table, type_="unique")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_evidence_artifact_validate "
        "ON public.evidence_artifacts"
    )
    _restore_strict_append_boundary()
    op.execute("DROP FUNCTION public.lians_validate_evidence_artifact()")
    for function in (
        "lians_evidence_identity(text,text,text,text,text,text)",
        "lians_sha256_text(text)",
        "lians_normalize_artifact_hash(text,text)",
        "lians_normalize_hash_algorithm(text)",
        "lians_ascii_fold(text)",
        "lians_ascii_trim(text)",
    ):
        op.execute(f"DROP FUNCTION public.{function}")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "0045_evidence_scope_identity requires an online PostgreSQL "
                "connection so selective canonicalization pages, concurrent "
                "unique indexes, and NOT VALID constraint validation can resume "
                "safely. Generate reviewed offline DDL only through "
                "0044_durable_metering, then run 0045 online."
            )
        _upgrade_postgresql()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _downgrade_postgresql()
