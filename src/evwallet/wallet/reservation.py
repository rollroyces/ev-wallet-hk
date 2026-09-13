"""Pre-auth / settle / release for EV charging sessions.

Every function here writes through ``evwallet.wallet.ledger.post_transaction``
so the journal, the materialized wallet columns, and the row-level lock stay
in lockstep.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.config import get_settings
from evwallet.db.models import LedgerEntry, Wallet, WalletTransaction
from evwallet.errors import (
    InsufficientFundsError,
    ValidationError,
    WalletError,
)
from evwallet.logging import get_logger
from evwallet.wallet.ledger import BUCKET_AVAILABLE, BUCKET_EXTERNAL, BUCKET_RESERVED, post_transaction

_log = get_logger(__name__)

ZERO = Decimal("0")


def _idempotency_key_for_reserve(
    wallet_id: uuid.UUID, session_id: str | None
) -> str | None:
    """Compute deterministic idempotency key for a (wallet, session) pair.

    Returns None when session_id is None — callers that pre-auth without a
    session must provide their own external_ref via post_transaction directly.
    """
    if session_id is None:
        return None
    h = hashlib.sha256(f"{wallet_id}|{session_id}|reserve".encode()).hexdigest()
    return f"reserve:{h}"


def _validate_amount(amount: Decimal, *, op: str) -> Decimal:
    """Reject negative / zero / over-cap amounts at the API boundary."""
    settings = get_settings()
    if not isinstance(amount, Decimal):
        raise ValidationError(
            f"{op} amount must be Decimal, got {type(amount).__name__}"
        )
    if amount <= ZERO:
        raise ValidationError(
            f"{op} amount must be > 0", details={"amount": str(amount)}
        )
    if amount > settings.preauth_max_hkd:
        raise ValidationError(
            f"{op} amount exceeds preauth_max_hkd",
            details={"amount": str(amount), "cap": str(settings.preauth_max_hkd)},
        )
    return amount


async def reserve(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    *,
    session_id: str | None = None,
) -> tuple[WalletTransaction, list[LedgerEntry]]:
    """Pre-authorize ``amount_hkd`` against the wallet.

    Writes a single wallet_transactions row of kind='reserve' with three
    ledger entries:

        1. ``reserve`` on ``available``    : -amount   (funds move out of available)
        2. ``reserve`` on ``reserved``     : +amount   (funds move into reserved)
        3. ``external_clearing`` on ``external`` : 0  (keeps SUM(amount)=0)

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Pre-auth amount (Decimal, must be > 0 and <= preauth_max_hkd).
        session_id: Optional charging session id; when present, the txn is
            idempotent on its SHA-256 hash.

    Returns:
        (WalletTransaction, list_of_three_LedgerEntry_rows)

    Raises:
        InsufficientFundsError: If available < amount.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_amount(amount_hkd, op="reserve")

    external_ref = _idempotency_key_for_reserve(wallet_id, session_id)
    txn = await post_transaction(
        db,
        wallet_id,
        kind="charge",
        entries=[
            ("reserve", BUCKET_AVAILABLE, -amount),
            ("reserve", BUCKET_RESERVED, +amount),
            ("external_clearing", BUCKET_EXTERNAL, ZERO),
        ],
        external_ref=external_ref,
        metadata={
            "operation": "reserve",
            "session_id": session_id,
            "amount_hkd": str(amount),
        },
    )

    entries = list(
        (
            await db.execute(
                select(LedgerEntry).where(LedgerEntry.txn_id == txn.id)
            )
        )
        .scalars()
        .all()
    )
    _log.info(
        "reservation.reserve",
        extra={
            "wallet_id": str(wallet_id),
            "session_id": session_id,
            "amount_hkd": str(amount),
            "txn_id": str(txn.id),
        },
    )
    return txn, entries


async def settle(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    *,
    session_id: str | None = None,
) -> WalletTransaction:
    """Finalize a charge by draining the reserved pool.

    Writes a single wallet_transactions row of kind='charge' with two
    ledger entries:

        1. ``settle`` on ``reserved``  : -amount   (funds leave reserved)
        2. ``charge_credit`` on ``external`` : +amount  (funds leave the system)

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Settled amount (Decimal, must be > 0).
        session_id: Optional session id — included in metadata only (settle
            is intentionally NOT idempotent on session_id because the actual
            settle amount may vary across retries; the caller controls that
            via external_ref if needed).

    Returns:
        The persisted WalletTransaction.

    Raises:
        InsufficientFundsError: If amount > currently reserved.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_amount(amount_hkd, op="settle")

    return await post_transaction(
        db,
        wallet_id,
        kind="charge",
        entries=[
            ("settle", BUCKET_RESERVED, -amount),
            ("charge_credit", BUCKET_EXTERNAL, +amount),
        ],
        external_ref=None,
        metadata={
            "operation": "settle",
            "session_id": session_id,
            "amount_hkd": str(amount),
        },
    )


async def release(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    *,
    session_id: str | None = None,
) -> WalletTransaction:
    """Release part of a pre-auth back to available.

    Writes a single wallet_transactions row of kind='refund' with two
    ledger entries:

        1. ``release`` on ``reserved`` : -amount
        2. ``release`` on ``available``: +amount

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Release amount (Decimal, must be > 0).
        session_id: Optional session id.

    Returns:
        The persisted WalletTransaction.

    Raises:
        InsufficientFundsError: If amount > currently reserved.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_amount(amount_hkd, op="release")

    return await post_transaction(
        db,
        wallet_id,
        kind="refund",
        entries=[
            ("release", BUCKET_RESERVED, -amount),
            ("release", BUCKET_AVAILABLE, +amount),
        ],
        external_ref=None,
        metadata={
            "operation": "release",
            "session_id": session_id,
            "amount_hkd": str(amount),
        },
    )


async def _current_reserved(db: AsyncSession, wallet_id: uuid.UUID) -> Decimal:
    """Return the wallet's current reserved balance (FOR UPDATE locked)."""
    from evwallet.wallet.ledger import _lock_wallet

    wallet = await _lock_wallet(db, wallet_id)
    return wallet.reserved_credits


async def end_session_settle(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    final_kwh: Decimal,
    rate_hkd_per_kwh: Decimal,
    *,
    session_id: str | None = None,
) -> tuple[WalletTransaction, WalletTransaction | None]:
    """Settle a charging session, releasing any pre-auth remainder.

    Algorithm:
        computed_final = final_kwh * rate_hkd_per_kwh
        reserved       = wallet.reserved_credits
        if computed_final == reserved: settle(computed_final)
        elif computed_final <  reserved: settle(computed_final); release(reserved - computed_final)
        else (computed_final > reserved): raise — caller should reserve more

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        final_kwh: kWh delivered (Decimal, >= 0).
        rate_hkd_per_kwh: Rate (Decimal, > 0).
        session_id: Optional session id forwarded into metadata.

    Returns:
        Tuple (settle_txn, release_txn_or_None).

    Raises:
        InsufficientFundsError: If computed_final > reserved (the caller must
            reserve more before settling; we never silently extend a pre-auth).
        ValidationError: If final_kwh < 0 or rate <= 0 or any cap violation.
    """
    if final_kwh < ZERO:
        raise ValidationError(
            "final_kwh must be >= 0", details={"final_kwh": str(final_kwh)}
        )
    if rate_hkd_per_kwh <= ZERO:
        raise ValidationError(
            "rate_hkd_per_kwh must be > 0",
            details={"rate_hkd_per_kwh": str(rate_hkd_per_kwh)},
        )

    # Compute with full Decimal precision — no float anywhere on this path.
    computed_final: Decimal = (final_kwh * rate_hkd_per_kwh).quantize(
        Decimal("0.0001")
    )
    _validate_amount(computed_final, op="end_session_settle")

    reserved = await _current_reserved(db, wallet_id)

    if computed_final > reserved:
        raise InsufficientFundsError(
            "computed final exceeds preauth; reserve more before settling",
            details={
                "wallet_id": str(wallet_id),
                "computed_final": str(computed_final),
                "reserved": str(reserved),
                "shortfall": str(computed_final - reserved),
            },
        )

    # First leg: settle the computed amount.
    settle_txn = await settle(db, wallet_id, computed_final, session_id=session_id)

    # Optional second leg: release the remainder.
    remainder = (reserved - computed_final).quantize(Decimal("0.0001"))
    if remainder > ZERO:
        release_txn = await release(
            db, wallet_id, remainder, session_id=session_id
        )
    else:
        release_txn = None

    _log.info(
        "reservation.end_session_settle",
        extra={
            "wallet_id": str(wallet_id),
            "session_id": session_id,
            "final_kwh": str(final_kwh),
            "rate_hkd_per_kwh": str(rate_hkd_per_kwh),
            "computed_final": str(computed_final),
            "reserved": str(reserved),
            "released": str(remainder) if remainder > ZERO else "0",
        },
    )
    return settle_txn, release_txn


__all__ = [
    "end_session_settle",
    "release",
    "reserve",
    "settle",
]


# Silence unused-import warnings for typing helpers we re-export in __all__.
_ = (Sequence, Any, Wallet, WalletError)
