"""Canonical SQLAlchemy 2.0 models for EV Wallet HK.

This module is the **source of truth** for the database schema (per
``docs/ARCHITECTURE.md``). Other agents (B/C/D/E/F) import from here and
MUST NOT invent new fields — if they need a new column, they request it
via the architecture doc update first.

Conventions
-----------
* All money is ``Numeric(12, 4)`` (HKD with 4 decimal places).
* All UUID PKs use ``gen_random_uuid()`` (pgcrypto extension — created by
  ``init_db`` / Alembic).
* Every table has ``created_at`` and ``updated_at`` where natural.
* All FKs cascade-on-delete only where the contract says so (e.g.
  ``social_accounts.user_id`` cascades; ``charging_sessions.user_id`` does
  NOT — sessions are preserved for accounting).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from evwallet.db import Base

# ---------------------------------------------------------------------------
# Cross-dialect JSON helpers (Agent A owns models; Agent C adds these
# local-only so tests can run on sqlite while production stays on Postgres).
# ---------------------------------------------------------------------------


class JSONColumn(TypeDecorator):
    """JSON column that uses Postgres JSONB when available, JSON otherwise.

    Agent A's canonical models assume Postgres; this TypeDecorator lets the
    same model file target both Postgres (production) and SQLite (tests).
    Drop once Agent A's conftest provides a real Postgres test instance.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Timezone-aware UTC now — used as column default."""
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class User(Base):
    """End-user account.

    A user always has exactly one :class:`Wallet` (1:1).
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(email IS NOT NULL) OR (phone_e164 IS NOT NULL)",
            name="users_email_or_phone_required",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True, nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-Hant")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    wallet: Mapped[Wallet | None] = relationship(
        "Wallet", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    social_accounts: Mapped[list[SocialAccount]] = relationship(
        "SocialAccount", back_populates="user", cascade="all, delete-orphan"
    )
    charging_sessions: Mapped[list[ChargingSession]] = relationship(
        "ChargingSession", back_populates="user"
    )


# ---------------------------------------------------------------------------
# social_accounts
# ---------------------------------------------------------------------------


class SocialAccount(Base):
    """OAuth identity linked to a :class:`User`.

    One user can have multiple social accounts (one per provider), but the
    ``(provider, provider_subject)`` pair is globally unique — Apple and
    Google IDs never collide.
    """

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_subject", name="social_accounts_provider_subject_uniq"
        ),
        Index("ix_social_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(254), nullable=False)

    # Optional cached display info from the provider.
    email_at_provider: Mapped[str | None] = mapped_column(String(254), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship("User", back_populates="social_accounts")


# ---------------------------------------------------------------------------
# wallets
# ---------------------------------------------------------------------------


class Wallet(Base):
    """Per-user credit wallet. Exactly one row per user.

    Money is in **HKD** (``Numeric(12, 4)``). The ``version`` column is an
    optimistic-lock counter that Agent B (wallet ledger) increments on every
    write — readers catch ``StaleDataError`` if their view is behind.
    """

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", name="wallets_user_id_uniq"),
        # NOTE (2026-09): available_credits / reserved_credits are LEGACY
        # columns. The canonical balance lives in the ledger (LedgerEntry
        # rows summed by bucket); see get_balance() in evwallet.wallet.ledger.
        # These columns are kept in the schema for back-compat with external
        # reporting/BI tooling but no production code writes to them. The
        # CHECK constraints below are belt-and-braces — a leftover from the
        # legacy invariant; new code paths use the journal, not these columns.
        # A future migration can drop the columns + constraints entirely once
        # we've confirmed no BI tool reads them.
        CheckConstraint("available_credits >= 0", name="wallets_available_nonneg"),
        CheckConstraint("reserved_credits >= 0", name="wallets_reserved_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # LEGACY columns — see the table-args note above. Use
    # ``evwallet.wallet.ledger.get_balance`` for the authoritative balance.
    available_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    reserved_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="HKD")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship("User", back_populates="wallet")


# ---------------------------------------------------------------------------
# wallet_transactions
# ---------------------------------------------------------------------------


class WalletTransaction(Base):
    """A user-visible transaction (topup / charge / refund / fee).

    Each transaction fans out into one-or-more :class:`LedgerEntry` rows
    that obey the double-entry invariant ``SUM(amount) = 0``.
    """

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("external_ref", name="wallet_tx_external_ref_uniq"),
        Index("ix_wallet_tx_wallet_id_posted_at", "wallet_id", "posted_at"),
        Index("ix_wallet_tx_user_id_posted_at", "user_id", "posted_at"),
        CheckConstraint(
            "kind IN ('topup','charge','refund','fee')",
            name="wallet_tx_kind_enum",
        ),
        CheckConstraint(
            "status IN ('pending','posted','reversed')",
            name="wallet_tx_status_enum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="posted")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="HKD")
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Column renamed to ``metadata`` in SQL; attribute is ``metadata_json``.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONColumn, nullable=False, default=dict
    )
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# ledger_entries
# ---------------------------------------------------------------------------


class LedgerEntry(Base):
    """Double-entry journal entry. High-volume, autoincrement BIGINT PK.

    **Integrity invariant** — Agent B enforces this:

    .. code-block:: sql

        SELECT SUM(amount) FROM ledger_entries WHERE txn_id = ?  -- = 0
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_txn_id", "txn_id"),
        Index("ix_ledger_wallet_id_posted_at", "wallet_id", "posted_at"),
        CheckConstraint(
            "entry_type IN ('topup_debit','charge_credit','reserve',"
            "'release','settle','refund','external_clearing')",
            name="ledger_entry_type_enum",
        ),
        CheckConstraint(
            "bucket IN ('available','reserved','external')",
            name="ledger_bucket_enum",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    txn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# ---------------------------------------------------------------------------
# charging_stations
# ---------------------------------------------------------------------------


class ChargingStation(Base):
    """A physical EV charging location. May have multiple :class:`Pole` rows."""

    __tablename__ = "charging_stations"
    __table_args__ = (
        UniqueConstraint("provider_code", "external_id", name="stations_provider_external_uniq"),
        Index("ix_charging_stations_lat_lng", "latitude", "longitude"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    parking_fee_hkd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    amenities: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        "raw", JSONColumn, nullable=False, default=dict
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    poles: Mapped[list[Pole]] = relationship(
        "Pole", back_populates="station", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# poles
# ---------------------------------------------------------------------------


class Pole(Base):
    """One physical connector on a :class:`ChargingStation`."""

    __tablename__ = "poles"
    __table_args__ = (
        UniqueConstraint("qr_code", name="poles_qr_code_uniq"),
        Index("ix_poles_station_id", "station_id"),
        Index("ix_poles_qr_code", "qr_code"),
        CheckConstraint(
            "connector IN ('ccs2','type2','chademo','tesla')",
            name="poles_connector_enum",
        ),
        CheckConstraint(
            "speed_tier IN ('ac_slow','ac_fast','dc_fast','dc_ultra')",
            name="poles_speed_tier_enum",
        ),
        CheckConstraint(
            "status IN ('unknown','available','charging','offline','fault')",
            name="poles_status_enum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("charging_stations.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector: Mapped[str] = mapped_column(String(16), nullable=False)
    speed_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    max_kw: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    qr_code: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    station: Mapped[ChargingStation] = relationship("ChargingStation", back_populates="poles")
    rates: Mapped[list[HourlyRate]] = relationship(
        "HourlyRate", back_populates="pole", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# hourly_rates
# ---------------------------------------------------------------------------


class HourlyRate(Base):
    """Time-of-use price for a :class:`Pole`."""

    __tablename__ = "hourly_rates"
    __table_args__ = (
        UniqueConstraint(
            "pole_id",
            "day_of_week",
            "hour_start_local",
            "valid_from",
            name="hourly_rates_uniq",
        ),
        Index("ix_hourly_rates_pole_id", "pole_id"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="hourly_rates_dow_range"),
        CheckConstraint("price_per_kwh_hkd >= 0", name="hourly_rates_price_nonneg"),
        CheckConstraint("parking_fee_hkd >= 0", name="hourly_rates_parking_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pole_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("poles.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    hour_start_local: Mapped[time] = mapped_column(Time, nullable=False)
    price_per_kwh_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    parking_fee_hkd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    valid_from: Mapped[date] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    pole: Mapped[Pole] = relationship("Pole", back_populates="rates")


# ---------------------------------------------------------------------------
# charging_sessions
# ---------------------------------------------------------------------------


class ChargingSession(Base):
    """A user's charging session at a specific :class:`Pole`."""

    __tablename__ = "charging_sessions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="charging_sessions_idem_uniq"),
        Index("ix_charging_sessions_user_started", "user_id", "started_at"),
        Index("ix_charging_sessions_pole_started", "pole_id", "started_at"),
        CheckConstraint(
            "status IN ('pending','active','completed','failed','cancelled')",
            name="charging_sessions_status_enum",
        ),
        CheckConstraint(
            "target_soc_pct IS NULL OR (target_soc_pct BETWEEN 0 AND 100)",
            name="charging_sessions_target_soc_range",
        ),
        CheckConstraint("preauth_hkd >= 0", name="charging_sessions_preauth_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pole_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("poles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    txn_reserve_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    target_soc_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kwh_delivered: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    peak_kw: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("0"))
    running_cost_hkd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    preauth_hkd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    settled_hkd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONColumn, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship("User", back_populates="charging_sessions")
    telemetry: Mapped[list[SessionTelemetry]] = relationship(
        "SessionTelemetry", back_populates="session", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# session_telemetry
# ---------------------------------------------------------------------------


class SessionTelemetry(Base):
    """High-frequency telemetry samples for a :class:`ChargingSession`.

    BIGINT PK because this table grows quickly (~1 row / 2 seconds per
    active session). Agent C writes here.
    """

    __tablename__ = "session_telemetry"
    __table_args__ = (
        Index("ix_session_telemetry_session_ts", "session_id", "ts"),
        CheckConstraint("kwh_cumulative >= 0", name="session_telemetry_kwh_nonneg"),
        CheckConstraint("kw_instant >= 0", name="session_telemetry_kw_nonneg"),
        CheckConstraint("cost_hkd_cumulative >= 0", name="session_telemetry_cost_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("charging_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    kwh_cumulative: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    kw_instant: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    soc_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_hkd_cumulative: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    session: Mapped[ChargingSession] = relationship("ChargingSession", back_populates="telemetry")


# ---------------------------------------------------------------------------
# Push tokens — registered by the mobile app for APNs/FCM notifications.
# ---------------------------------------------------------------------------


class PushToken(Base):
    __tablename__ = "push_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(512), unique=True)
    platform: Mapped[str] = mapped_column(String(16))  # 'ios' | 'android'
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------------------------------------------------------------------------
# Stripe webhook events — idempotency log for incoming Stripe webhook calls.
#
# Agent D owns this model. The PK is the Stripe event id (``evt_***``) so a
# retry from Stripe (on non-2xx from us) is detected by unique-violation
# rather than needing a separate dedup query.
#
# Status values:
#   * 'received'  — payload stored, processing not yet attempted
#   * 'processed' — handler ran successfully
#   * 'failed'    — handler raised; the error message is stored in ``error``
# ---------------------------------------------------------------------------


class StripeWebhookEvent(Base):
    """Idempotency log for Stripe webhook events.

    The primary key is the Stripe event id (``evt_***``) which is globally
    unique and is what Stripe uses to retry deliveries. A second delivery
    hits the ``id`` primary-key unique constraint, which the router catches
    and turns into a no-op 200 response — Stripe treats that as a successful
    ack and stops retrying.
    """

    __tablename__ = "stripe_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received','processed','failed')",
            name="stripe_webhook_events_status_enum",
        ),
        Index("ix_stripe_webhook_events_type_received_at", "type", "received_at"),
    )

    # Stripe event id, e.g. ``evt_1PqX...``. This is the primary key —
    # the value is stable across retries so a second insert collides on
    # the PK and we return 200 without re-processing.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# All model names — exposed for typing helpers and Alembic autogenerate.
# ---------------------------------------------------------------------------


__all__ = [
    "ChargingSession",
    "ChargingStation",
    "HourlyRate",
    "LedgerEntry",
    "Pole",
    "PushToken",
    "SessionTelemetry",
    "SocialAccount",
    "StripeWebhookEvent",
    "User",
    "Wallet",
    "WalletTransaction",
]
