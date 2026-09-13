"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-13

Creates every table defined in ``src/evwallet/db/models.py`` — the
canonical EV Wallet HK schema. All UUID PKs use ``gen_random_uuid()``
from the ``pgcrypto`` extension, which is created at the top of the
upgrade.

Naming note: the model file uses ``metadata_json`` / ``raw_payload``
attribute names that map to ``metadata`` / ``raw`` columns (because
``metadata`` shadows SQLAlchemy declarative). The migration names the
columns per the architecture contract.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---- users ---------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("locale", sa.String(16), nullable=False, server_default="zh-Hant"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="users_email_uniq"),
        sa.UniqueConstraint("phone_e164", name="users_phone_uniq"),
        sa.CheckConstraint(
            "(email IS NOT NULL) OR (phone_e164 IS NOT NULL)",
            name="users_email_or_phone_required",
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_phone_e164", "users", ["phone_e164"])

    # ---- social_accounts -----------------------------------------------
    op.create_table(
        "social_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_subject", sa.String(254), nullable=False),
        sa.Column("email_at_provider", sa.String(254), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_subject",
            name="social_accounts_provider_subject_uniq",
        ),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])

    # ---- wallets -------------------------------------------------------
    op.create_table(
        "wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "available_credits",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "reserved_credits",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="HKD"),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", name="wallets_user_id_uniq"),
        sa.CheckConstraint("available_credits >= 0", name="wallets_available_nonneg"),
        sa.CheckConstraint("reserved_credits >= 0", name="wallets_reserved_nonneg"),
    )

    # ---- wallet_transactions -------------------------------------------
    op.create_table(
        "wallet_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="posted"
        ),
        sa.Column("amount", sa.Numeric(12, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="HKD"),
        sa.Column("external_ref", sa.String(128), nullable=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("external_ref", name="wallet_tx_external_ref_uniq"),
        sa.CheckConstraint(
            "kind IN ('topup','charge','refund','fee')",
            name="wallet_tx_kind_enum",
        ),
        sa.CheckConstraint(
            "status IN ('pending','posted','reversed')",
            name="wallet_tx_status_enum",
        ),
    )
    op.create_index(
        "ix_wallet_tx_wallet_id_posted_at",
        "wallet_transactions",
        ["wallet_id", "posted_at"],
    )
    op.create_index(
        "ix_wallet_tx_user_id_posted_at",
        "wallet_transactions",
        ["user_id", "posted_at"],
    )

    # ---- ledger_entries ------------------------------------------------
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "txn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(12, 4), nullable=False),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "entry_type IN ('topup_debit','charge_credit','reserve',"
            "'release','settle','refund','external_clearing')",
            name="ledger_entry_type_enum",
        ),
        sa.CheckConstraint(
            "bucket IN ('available','reserved','external')",
            name="ledger_bucket_enum",
        ),
    )
    op.create_index("ix_ledger_txn_id", "ledger_entries", ["txn_id"])
    op.create_index(
        "ix_ledger_wallet_id_posted_at",
        "ledger_entries",
        ["wallet_id", "posted_at"],
    )

    # ---- charging_stations ---------------------------------------------
    op.create_table(
        "charging_stations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("district", sa.String(64), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column(
            "parking_fee_hkd",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "amenities",
            postgresql.ARRAY(sa.String),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider_code",
            "external_id",
            name="stations_provider_external_uniq",
        ),
    )
    op.create_index(
        "ix_charging_stations_lat_lng",
        "charging_stations",
        ["latitude", "longitude"],
    )

    # ---- poles ---------------------------------------------------------
    op.create_table(
        "poles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "station_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charging_stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("connector", sa.String(16), nullable=False),
        sa.Column("speed_tier", sa.String(16), nullable=False),
        sa.Column("max_kw", sa.Numeric(8, 2), nullable=False),
        sa.Column("qr_code", sa.String(512), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "status_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("qr_code", name="poles_qr_code_uniq"),
        sa.CheckConstraint(
            "connector IN ('ccs2','type2','chademo','tesla')",
            name="poles_connector_enum",
        ),
        sa.CheckConstraint(
            "speed_tier IN ('ac_slow','ac_fast','dc_fast','dc_ultra')",
            name="poles_speed_tier_enum",
        ),
        sa.CheckConstraint(
            "status IN ('unknown','available','charging','offline','fault')",
            name="poles_status_enum",
        ),
    )
    op.create_index("ix_poles_station_id", "poles", ["station_id"])
    op.create_index("ix_poles_qr_code", "poles", ["qr_code"])

    # ---- hourly_rates --------------------------------------------------
    op.create_table(
        "hourly_rates",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "pole_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("poles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("hour_start_local", sa.Time, nullable=False),
        sa.Column("price_per_kwh_hkd", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "parking_fee_hkd",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "pole_id",
            "day_of_week",
            "hour_start_local",
            "valid_from",
            name="hourly_rates_uniq",
        ),
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6", name="hourly_rates_dow_range"
        ),
        sa.CheckConstraint(
            "price_per_kwh_hkd >= 0", name="hourly_rates_price_nonneg"
        ),
        sa.CheckConstraint(
            "parking_fee_hkd >= 0", name="hourly_rates_parking_nonneg"
        ),
    )
    op.create_index("ix_hourly_rates_pole_id", "hourly_rates", ["pole_id"])

    # ---- charging_sessions ---------------------------------------------
    op.create_table(
        "charging_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "pole_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("poles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "txn_reserve_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("target_soc_pct", sa.Integer, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "kwh_delivered", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column("peak_kw", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column(
            "running_cost_hkd",
            sa.Numeric(12, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("preauth_hkd", sa.Numeric(12, 4), nullable=False),
        sa.Column("settled_hkd", sa.Numeric(12, 4), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("idempotency_key", name="charging_sessions_idem_uniq"),
        sa.CheckConstraint(
            "status IN ('pending','active','completed','failed','cancelled')",
            name="charging_sessions_status_enum",
        ),
        sa.CheckConstraint(
            "target_soc_pct IS NULL OR (target_soc_pct BETWEEN 0 AND 100)",
            name="charging_sessions_target_soc_range",
        ),
        sa.CheckConstraint(
            "preauth_hkd >= 0", name="charging_sessions_preauth_nonneg"
        ),
    )
    op.create_index(
        "ix_charging_sessions_user_started",
        "charging_sessions",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_charging_sessions_pole_started",
        "charging_sessions",
        ["pole_id", "started_at"],
    )

    # ---- session_telemetry ---------------------------------------------
    op.create_table(
        "session_telemetry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charging_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("kwh_cumulative", sa.Numeric(12, 4), nullable=False),
        sa.Column("kw_instant", sa.Numeric(8, 2), nullable=False),
        sa.Column("soc_pct", sa.Integer, nullable=True),
        sa.Column("cost_hkd_cumulative", sa.Numeric(12, 4), nullable=False),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "kwh_cumulative >= 0", name="session_telemetry_kwh_nonneg"
        ),
        sa.CheckConstraint("kw_instant >= 0", name="session_telemetry_kw_nonneg"),
        sa.CheckConstraint(
            "cost_hkd_cumulative >= 0", name="session_telemetry_cost_nonneg"
        ),
    )
    op.create_index(
        "ix_session_telemetry_session_ts",
        "session_telemetry",
        ["session_id", "ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_telemetry_session_ts", table_name="session_telemetry")
    op.drop_table("session_telemetry")

    op.drop_index("ix_charging_sessions_pole_started", table_name="charging_sessions")
    op.drop_index("ix_charging_sessions_user_started", table_name="charging_sessions")
    op.drop_table("charging_sessions")

    op.drop_index("ix_hourly_rates_pole_id", table_name="hourly_rates")
    op.drop_table("hourly_rates")

    op.drop_index("ix_poles_qr_code", table_name="poles")
    op.drop_index("ix_poles_station_id", table_name="poles")
    op.drop_table("poles")

    op.drop_index("ix_charging_stations_lat_lng", table_name="charging_stations")
    op.drop_table("charging_stations")

    op.drop_index("ix_ledger_wallet_id_posted_at", table_name="ledger_entries")
    op.drop_index("ix_ledger_txn_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index(
        "ix_wallet_tx_user_id_posted_at", table_name="wallet_transactions"
    )
    op.drop_index(
        "ix_wallet_tx_wallet_id_posted_at", table_name="wallet_transactions"
    )
    op.drop_table("wallet_transactions")

    op.drop_table("wallets")

    op.drop_index("ix_social_accounts_user_id", table_name="social_accounts")
    op.drop_table("social_accounts")

    op.drop_index("ix_users_phone_e164", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
