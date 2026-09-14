"""add users.password_hash

Stores an Argon2id hash of the local password. Nullable because
OAuth-only users (Apple, Google) never set a local password.

Revision ID: 0005_add_password_hash
Revises: 0004_expand_enums
Create Date: 2026-09-14 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_add_password_hash"
down_revision = "0004_expand_enums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")