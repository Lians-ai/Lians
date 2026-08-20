"""Make subject_keys keyed by (namespace, subject_id) - cross-tenant isolation fix

Revision ID: 0019_subject_keys_composite_pk
Revises: 0018_live_facts_namespace_rls
Create Date: 2026-07-01

Bug
---
``subject_keys`` had ``subject_id`` as its sole primary key, but subject_id is
only unique *within* a tenant (namespace). Two tenants that both use, say,
``subject_id="customer-42"`` therefore shared ONE AES data-encryption key:

  * Tenant B's memory content was encrypted under a key created by Tenant A -
    a crypto-layer breach of the information barrier the product sells.
  * A GDPR erase issued by Tenant A ran ``destroy_subject_key("customer-42")``
    against the single global row, crypto-shredding Tenant B's data and making
    Tenant B's next write for that subject 500 with
    "Subject key ... has been crypto-shredded".

Surfaced by the full-scope limit test reusing a subject_id across namespaces.

Fix
---
Promote the primary key to the composite ``(namespace, subject_id)`` so each
tenant owns an independent key space. The application code (pii.py, dek_cache.py,
memory_service.py) is updated to look up, cache, and destroy keys by both fields.

Because the old PK was ``subject_id`` alone, no two existing rows can collide on
``(namespace, subject_id)``, so the constraint swap is safe with no data motion.
"""
from alembic import op


revision = "0019_subject_keys_composite_pk"
down_revision = "0018_live_facts_namespace_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite test DB is recreated from the ORM models each run
    op.execute("ALTER TABLE subject_keys DROP CONSTRAINT IF EXISTS subject_keys_pkey")
    op.execute("ALTER TABLE subject_keys ADD PRIMARY KEY (namespace, subject_id)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE subject_keys DROP CONSTRAINT IF EXISTS subject_keys_pkey")
    # Reverting to a bare subject_id PK requires uniqueness across namespaces;
    # dedupe first if any cross-tenant collisions were created after 0019.
    op.execute("ALTER TABLE subject_keys ADD PRIMARY KEY (subject_id)")
