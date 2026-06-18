"""Add label column to api_keys

Revision ID: 0002_api_key_label
Revises: 0001_initial
Create Date: 2026-06-17
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0002_api_key_label"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "label")
