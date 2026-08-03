"""Versioned master-key envelopes and encrypted closure statements.

Revision ID: 0036_master_key_rotation
Revises: 0035_workload_credentials
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_master_key_rotation"
down_revision = "0035_workload_credentials"
branch_labels = None
depends_on = None


def _sealed_constraint(column: str) -> str:
    return (
        f"({column} LIKE 'lians-sealed:v1:%' OR "
        f"{column} LIKE 'lians-sealed:v2:%')"
    )


def _nullable_sealed_constraint(column: str) -> str:
    return f"({column} IS NULL OR {_sealed_constraint(column)})"


def upgrade() -> None:
    # Existing append-only closure rows remain v1.  The offline operator tool
    # encrypts their statements only after verifying their original hash.
    with op.batch_alter_table("control_closure_attestations") as batch:
        batch.alter_column(
            "statement",
            existing_type=sa.Text(),
            nullable=True,
        )
        batch.add_column(sa.Column("statement_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("statement_hash", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "hash_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.create_check_constraint(
            "ck_control_attestation_statement_storage",
            "((statement IS NOT NULL AND statement_encrypted IS NULL) OR "
            "(statement IS NULL AND statement_encrypted IS NOT NULL))",
        )
        batch.create_check_constraint(
            "ck_control_attestation_statement_sealed",
            _nullable_sealed_constraint("statement_encrypted"),
        )
        batch.create_check_constraint(
            "ck_control_attestation_statement_hash",
            (
                "statement_hash IS NULL OR statement_hash ~ '^[0-9a-f]{64}$'"
                if op.get_bind().dialect.name == "postgresql"
                else "statement_hash IS NULL OR length(statement_hash) = 64"
            ),
        )
        batch.create_check_constraint(
            "ck_control_attestation_hash_version",
            "hash_version IN (1, 2)",
        )
        batch.create_check_constraint(
            "ck_control_attestation_v2_statement_hash",
            "hash_version = 1 OR statement_hash IS NOT NULL",
        )

    # All application-sealed columns accept the legacy reader format and the
    # self-identifying current format during a rolling rotation.
    for table, name, column in (
        (
            "gate_approval_attestations",
            "ck_gate_approval_statement_sealed",
            "statement_encrypted",
        ),
        (
            "decision_review_events",
            "ck_decision_review_note_sealed",
            "note_encrypted",
        ),
        (
            "integration_destinations",
            "ck_integration_destination_secret_sealed",
            "secret_config_encrypted",
        ),
        (
            "integration_outbox_events",
            "ck_integration_payload_sealed",
            "payload_encrypted",
        ),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="check")
            expression = (
                _nullable_sealed_constraint(column)
                if table in {"gate_approval_attestations", "decision_review_events"}
                else _sealed_constraint(column)
            )
            batch.create_check_constraint(name, expression)

    # A single global checkpoint is written in the same transaction as a
    # successful operator rewrap.  It contains identifiers, counts, and hashes
    # only—never key material, ciphertext, or plaintext-derived content.
    op.create_table(
        "master_key_rotation_state",
        sa.Column(
            "singleton_id",
            sa.SmallInteger(),
            primary_key=True,
            nullable=False,
            server_default="1",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_key_id", sa.String(length=64), nullable=False),
        sa.Column("previous_key_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_values", sa.BigInteger(), nullable=False),
        sa.Column("rewritten_values", sa.BigInteger(), nullable=False),
        sa.Column("legacy_values_remaining", sa.BigInteger(), nullable=False),
        sa.Column("previous_values_remaining", sa.BigInteger(), nullable=False),
        sa.Column("unknown_values_remaining", sa.BigInteger(), nullable=False),
        sa.Column("plaintext_closures_remaining", sa.BigInteger(), nullable=False),
        sa.Column("inventory_sha256", sa.String(length=64), nullable=False),
        sa.Column("backup_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("singleton_id = 1", name="ck_master_key_rotation_singleton"),
        sa.CheckConstraint(
            "status IN ('verified', 'blocked')",
            name="ck_master_key_rotation_status",
        ),
        sa.CheckConstraint(
            "length(current_key_id) BETWEEN 1 AND 64",
            name="ck_master_key_rotation_current_id",
        ),
        sa.CheckConstraint(
            "previous_key_id IS NULL OR "
            "(length(previous_key_id) BETWEEN 1 AND 64 "
            "AND previous_key_id <> current_key_id)",
            name="ck_master_key_rotation_previous_id",
        ),
        sa.CheckConstraint(
            "total_values >= 0 AND rewritten_values >= 0 AND "
            "legacy_values_remaining >= 0 AND previous_values_remaining >= 0 AND "
            "unknown_values_remaining >= 0 AND plaintext_closures_remaining >= 0",
            name="ck_master_key_rotation_counts",
        ),
        sa.CheckConstraint(
            "length(inventory_sha256) = 64",
            name="ck_master_key_rotation_inventory_hash",
        ),
        sa.CheckConstraint(
            "length(backup_manifest_sha256) = 64",
            name="ck_master_key_rotation_backup_hash",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    v2_counts = 0
    for table, column in (
        ("pending_admissions", "content"),
        ("webhook_endpoints", "secret"),
        ("gate_approval_attestations", "statement_encrypted"),
        ("decision_review_events", "note_encrypted"),
        ("integration_destinations", "secret_config_encrypted"),
        ("integration_outbox_events", "payload_encrypted"),
        ("control_closure_attestations", "statement_encrypted"),
    ):
        v2_counts += int(
            bind.execute(
                sa.text(
                    f"SELECT count(*) FROM {table} "
                    f"WHERE {column} LIKE 'lians-sealed:v2:%'"
                )
            ).scalar_one()
        )
    wrapper_magic_hex = b"lians-dek:v2\x00".hex()
    subject_v2 = int(
        bind.execute(
            sa.text(
                (
                    "SELECT count(*) FROM subject_keys WHERE "
                    f"substring(enc_key from 1 for 13) = decode('{wrapper_magic_hex}', 'hex')"
                )
                if bind.dialect.name == "postgresql"
                else (
                    "SELECT count(*) FROM subject_keys WHERE "
                    f"substr(enc_key, 1, 13) = X'{wrapper_magic_hex}'"
                )
            )
        ).scalar_one()
    )
    encrypted_closures = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM control_closure_attestations "
                "WHERE statement_encrypted IS NOT NULL OR statement IS NULL"
            )
        ).scalar_one()
    )
    if v2_counts or subject_v2 or encrypted_closures:
        raise RuntimeError(
            "0036 downgrade refused: v2 envelopes or encrypted closure statements remain"
        )

    op.drop_table("master_key_rotation_state")

    for table, name, column in (
        (
            "gate_approval_attestations",
            "ck_gate_approval_statement_sealed",
            "statement_encrypted",
        ),
        (
            "decision_review_events",
            "ck_decision_review_note_sealed",
            "note_encrypted",
        ),
        (
            "integration_destinations",
            "ck_integration_destination_secret_sealed",
            "secret_config_encrypted",
        ),
        (
            "integration_outbox_events",
            "ck_integration_payload_sealed",
            "payload_encrypted",
        ),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="check")
            expression = (
                f"{column} IS NULL OR {column} LIKE 'lians-sealed:v1:%'"
                if table in {"gate_approval_attestations", "decision_review_events"}
                else f"{column} LIKE 'lians-sealed:v1:%'"
            )
            batch.create_check_constraint(name, expression)

    with op.batch_alter_table("control_closure_attestations") as batch:
        batch.drop_constraint("ck_control_attestation_v2_statement_hash", type_="check")
        batch.drop_constraint("ck_control_attestation_hash_version", type_="check")
        batch.drop_constraint("ck_control_attestation_statement_hash", type_="check")
        batch.drop_constraint("ck_control_attestation_statement_sealed", type_="check")
        batch.drop_constraint("ck_control_attestation_statement_storage", type_="check")
        batch.drop_column("hash_version")
        batch.drop_column("statement_hash")
        batch.drop_column("statement_encrypted")
        batch.alter_column("statement", existing_type=sa.Text(), nullable=False)
