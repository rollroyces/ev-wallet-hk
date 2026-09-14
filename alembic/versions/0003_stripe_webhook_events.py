"""add stripe_webhook_events

Revision ID: 0003_stripe_webhook_events
Revises: 0002_push_tokens
Create Date: 2026-09-14

Idempotency log for Stripe webhook events. Agent D owns this table — the
router (src/evwallet/payments/router.py) inserts one row per delivery on
``POST /api/v1/payments/stripe/webhook`` and catches the unique-PK
collision when Stripe retries the same ``evt_***`` id.

The primary key is the Stripe event id (``evt_***``) which is unique
across retries; ``status`` ('received' | 'processed' | 'failed') and
``error`` (free-form text captured when the handler raises) give ops a
quick way to inspect handler health.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_stripe_webhook_events"
down_revision = "0002_push_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="received",
            nullable=False,
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="stripe_webhook_events_pkey"),
        sa.CheckConstraint(
            "status IN ('received','processed','failed')",
            name="stripe_webhook_events_status_enum",
        ),
    )
    op.create_index(
        "ix_stripe_webhook_events_type_received_at",
        "stripe_webhook_events",
        ["type", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stripe_webhook_events_type_received_at",
        table_name="stripe_webhook_events",
    )
    op.drop_table("stripe_webhook_events")