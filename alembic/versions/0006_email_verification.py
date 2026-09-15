"""add email_verifications + users.email_verified_at

Adds the email verification table for /auth/verify-email and a
``users.email_verified_at`` timestamp set on successful redemption.

Revision ID: 0006_email_verification
Revises: 0005_add_password_hash
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_email_verification"
down_revision = "0005_add_password_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "email_verifications",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("email_to", sa.String(length=254), nullable=False),
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'signup'"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_email_verifications_user_id",
        "email_verifications",
        ["user_id"],
    )
    op.create_index(
        "ix_email_verifications_user_active",
        "email_verifications",
        ["user_id", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verifications_user_active", table_name="email_verifications"
    )
    op.drop_index(
        "ix_email_verifications_user_id", table_name="email_verifications"
    )
    op.drop_table("email_verifications")
    op.drop_column("users", "email_verified_at")
