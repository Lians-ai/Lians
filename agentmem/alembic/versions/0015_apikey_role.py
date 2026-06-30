"""api_keys.role for RBAC

Revision ID: 0015_apikey_role
Revises: 0014_idempotency_keys
Create Date: 2026-06-29

Adds an optional named role to API keys. When set, the role expands to a scope
set at auth time (owner / analyst / compliance / readonly), giving role-based
access control on top of the existing explicit scopes.
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_apikey_role"
down_revision = "0014_idempotency_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("role", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "role")
