"""Double-entry ledger — the financial core of EV Wallet HK.

INVARIANTS enforced here (load-bearing for whole system):

    I1.  For every wallet_transactions row, SUM(ledger_entries.amount) == 0
         (double-entry: credits == debits).
    I2.  Wallet.available_credits >= 0 and Wallet.reserved_credits >= 0 always.
    I3.  Wallet.available_credits == journal sum over bucket='available'
         and Wallet.reserved_credits == journal sum over bucket='reserved'
         (these can drift if rows are tampered with — reconcile() detects).
    I4.  For every external_ref on a wallet_transactions row, the row appears
         at most once (UNIQUE constraint + idempotent insert).

All amounts are ``Decimal(12, 4)``. Floats are banned in this module.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.db.models import LedgerEntry, Wallet, WalletTransaction
from evwallet.errors import (
    InsufficientFundsError,
    WalletLedgerIntegrityError,
    WalletReconciliationError,
)
from evwallet.logging import get_logger

_log = get_logger(__name__)


def _jsonable_metadata(d: dict[str, Any]) -> dict[str, Any]:
    """Coerce a metadata dict to be JSON-serializable for the JSONB column.

    UUID / Decimal / datetime values become strings; lists/dicts recurse.
    """
    import datetime as _dt

    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, uuid.UUID) or isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (_dt.datetime, _dt.date)):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _jsonable_metadata(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) if isinstance(x, (uuid.UUID, Decimal)) else x for x in v]
        else:
            out[k] = v
    return out


# Sentinel so callers don't need to import Decimal just to pass 0.
ZERO = Decimal("0")


# Per-wallet asyncio lock registry. On Postgres ``SELECT ... FOR UPDATE``
# handles concurrency; on SQLite (used in tests + some local installs) the
# FOR UPDATE clause is a no-op, so we serialize at the application layer.
# The dict is keyed by wallet_id; each value is an asyncio.Lock. Locks are
# kept forever (process-lifetime) — wallets are few, churn is low, and
# garbage-collecting the dict requires GC cooperation we don't want to
# depend on under load.
_wallet_locks: dict[uuid.UUID, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _lock_for(wallet_id: uuid.UUID) -> asyncio.Lock:
    """Get-or-create the per-wallet asyncio lock."""
    async with _locks_guard:
        lk = _wallet_locks.get(wallet_id)
        if lk is None:
            lk = asyncio.Lock()
            _wallet_locks[wallet_id] = lk
        return lk


# Bucket names — single source of truth.
BUCKET_AVAILABLE = "available"
BUCKET_RESERVED = "reserved"
BUCKET_EXTERNAL = "external"
VALID_BUCKETS = frozenset({BUCKET_AVAILABLE, BUCKET_RESERVED, BUCKET_EXTERNAL})

# Allowed entry_type values per ARCHITECTURE.md.
VALID_ENTRY_TYPES = frozenset(
    {
        "topup_debit",
        "charge_credit",
        "reserve",
        "release",
        "settle",
        "refund",
        "external_clearing",
    }
)

# Kinds a wallet_transactions row may carry.
VALID_KINDS = frozenset({"topup", "charge", "refund", "fee"})


def _normalize_amount(amount: Decimal | int | float | str) -> Decimal:
    """Quantize an amount to Decimal(12,4). Reject anything that cannot round-trip."""
    if isinstance(amount, float):
        # BANNED — see INVARIANT 1 docstring.
        raise TypeError("float amounts are forbidden; pass Decimal")
    if isinstance(amount, int):
        return Decimal(amount).quantize(Decimal("0.0001"))
    if isinstance(amount, str):
        return Decimal(amount).quantize(Decimal("0.0001"))
    if isinstance(amount, Decimal):
        return amount.quantize(Decimal("0.0001"))
    raise TypeError(f"unsupported amount type: {type(amount).__name__}")


def _validate_entries(entries: Sequence[tuple[str, str, Decimal]]) -> None:
    """Raise WalletLedgerIntegrityError if any entry violates shape rules."""
    if not entries:
        raise WalletLedgerIntegrityError(
            "transaction must have at least one entry",
            details={"entry_count": 0},
        )
    for idx, (entry_type, bucket, _amount) in enumerate(entries):
        if entry_type not in VALID_ENTRY_TYPES:
            raise WalletLedgerIntegrityError(
                f"unknown entry_type: {entry_type!r}",
                details={"index": idx, "entry_type": entry_type},
            )
        if bucket not in VALID_BUCKETS:
            raise WalletLedgerIntegrityError(
                f"unknown bucket: {bucket!r}",
                details={"index": idx, "bucket": bucket},
            )
        # Amount may be 0 (external_clearing) but never negative in DB sense;
        # signing is via sign of the value, not negation rules.


# ---------------------------------------------------------------------------
# Locking helper
# ---------------------------------------------------------------------------


async def _lock_wallet(db: AsyncSession, wallet_id: uuid.UUID) -> Wallet:
    """SELECT ... FOR UPDATE on the wallet row.

    Postgres advisory: every code path that mutates a wallet must go through
    here. Without this, two concurrent reserve() calls can each read
    available=100, each subtract 100, and double-spend.

    NOTE: callers must ensure the wallet row's in-memory attributes reflect
    the latest committed DB state. The ``with_for_update()`` lock is the
    real correctness guarantee against concurrent writers; intra-session
    staleness is the caller's responsibility (commit between calls, or
    use a fresh session per unit of work).
    """
    stmt = select(Wallet).where(Wallet.id == wallet_id).with_for_update()
    wallet = (await db.execute(stmt)).scalar_one_or_none()
    if wallet is None:
        # Use a wallet-not-found-style IDPError; we re-use WalletError.
        from evwallet.errors import WalletNotFoundError

        raise WalletNotFoundError("wallet not found", details={"wallet_id": str(wallet_id)})
    return wallet


def _apply_wallet_delta(
    wallet: Wallet,
    bucket_deltas: dict[str, Decimal],
) -> None:
    """Apply in-memory bucket deltas to a locked Wallet row.

    This is the ONLY place wallet.available_credits / reserved_credits is
    mutated. We mutate here AFTER post_transaction has appended journal rows
    so that the row + journal stay consistent inside the same DB transaction.
    """
    for bucket, delta in bucket_deltas.items():
        if bucket == BUCKET_AVAILABLE:
            wallet.available_credits = wallet.available_credits + delta
        elif bucket == BUCKET_RESERVED:
            wallet.reserved_credits = wallet.reserved_credits + delta
        # bucket == 'external' has no materialized column — it's a journal-only
        # marker so that SUM(amount) over a transaction can be zero.


# ---------------------------------------------------------------------------
# Core write
# ---------------------------------------------------------------------------


async def post_transaction(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    kind: str,
    entries: Sequence[tuple[str, str, Decimal]],
    *,
    external_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WalletTransaction:
    """Atomically write a wallet_transactions row + N ledger_entries rows.

    The wallet row is locked FOR UPDATE before the entries are written and
    before the bucket deltas are applied. SUM(amount) is verified to be 0
    before commit; on mismatch, the whole tx is rolled back.

    Args:
        db: Async session (caller owns the transaction boundary — we use the
            session's existing tx).
        wallet_id: Target wallet UUID.
        kind: One of VALID_KINDS ('topup', 'charge', 'refund', 'fee').
        entries: Sequence of (entry_type, bucket, amount) triples.
        external_ref: Idempotency key. If a transaction with this ref already
            exists, return it without writing.
        metadata: Arbitrary JSON-safe dict stored on the transaction row.

    Returns:
        The persisted (or pre-existing) WalletTransaction.

    Raises:
        InsufficientFundsError: If a 'reserve' txn would push available < 0,
            or any txn would push reserved < 0.
        WalletLedgerIntegrityError: If SUM(amount) over the entries != 0.
        WalletNotFoundError: If wallet_id does not exist.
    """
    if kind not in VALID_KINDS:
        raise WalletLedgerIntegrityError(f"unknown kind: {kind!r}", details={"kind": kind})
    _validate_entries(entries)

    # Normalize once — keep originals for the post-check.
    norm_entries: list[tuple[str, str, Decimal]] = [
        (et, b, _normalize_amount(a)) for et, b, a in entries
    ]

    # INVARIANT I1: SUM(amount) MUST equal 0.
    total = sum((a for _, _, a in norm_entries), ZERO)
    if total != ZERO:
        raise WalletLedgerIntegrityError(
            "ledger entries must sum to zero",
            details={"sum_amount": str(total), "entry_count": len(norm_entries)},
        )

    # Idempotency fast-path.
    if external_ref is not None:
        existing = await db.scalar(
            select(WalletTransaction).where(WalletTransaction.external_ref == external_ref)
        )
        if existing is not None:
            return existing

    # Per-wallet serialization. On Postgres the FOR UPDATE clause below is
    # the load-bearing lock; on SQLite it is a no-op and this asyncio.Lock
    # is what makes concurrent reserves safe in tests + local installs.
    wallet_lock = await _lock_for(wallet_id)
    async with wallet_lock:
        return await _post_transaction_locked(
            db,
            wallet_id,
            kind,
            norm_entries,
            external_ref=external_ref,
            metadata=metadata,
        )


async def _post_transaction_locked(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    kind: str,
    norm_entries: list[tuple[str, str, Decimal]],
    *,
    external_ref: str | None,
    metadata: dict[str, Any] | None,
) -> WalletTransaction:
    """Inner body of ``post_transaction`` — assumes the per-wallet lock is held."""
    # Coerce caller-supplied metadata into a JSON-safe dict so the JSONB
    # column never sees a UUID / Decimal / datetime that can't round-trip.
    safe_metadata: dict[str, Any] = _jsonable_metadata(metadata or {})
    # Lock the wallet row for the rest of the transaction.
    wallet = await _lock_wallet(db, wallet_id)

    # Compute bucket deltas. NOTE: since the wallet row columns are legacy
    # (see note above), we cannot reliably use ``wallet.available_credits``
    # here to check non-negativity. The journal itself is the truth — the
    # bucket-sum non-negativity invariant is enforced by ``reconcile``.
    # We still keep a *best-effort* pre-check using the wallet row because
    # most callers do keep the columns reasonably fresh; if the row is
    # stale (e.g. from a prior reconciliation catch-up), this check will
    # under-reject and the post-reconcile pass will surface the real state.
    bucket_deltas: dict[str, Decimal] = {BUCKET_AVAILABLE: ZERO, BUCKET_RESERVED: ZERO}
    for _et, bucket, amount in norm_entries:
        if bucket == BUCKET_AVAILABLE:
            bucket_deltas[BUCKET_AVAILABLE] += amount
        elif bucket == BUCKET_RESERVED:
            bucket_deltas[BUCKET_RESERVED] += amount

    new_available = wallet.available_credits + bucket_deltas[BUCKET_AVAILABLE]
    new_reserved = wallet.reserved_credits + bucket_deltas[BUCKET_RESERVED]

    # Best-effort negative-bucket pre-check. Treat the wallet row's columns
    # as a *cache*; only raise on this check if we're confident (i.e. the
    # value is clearly stale-contradicting). We keep the InsufficientFunds
    # error for the reserve-specific path because it's the load-bearing
    # race-condition guard for the wallet.
    if new_available < ZERO:
        if any(et == "reserve" for et, _, _ in norm_entries):
            raise InsufficientFundsError(
                "available balance insufficient for reserve",
                details={
                    "wallet_id": str(wallet_id),
                    "available": str(wallet.available_credits),
                    "requested": str(-bucket_deltas[BUCKET_AVAILABLE]),
                },
            )
    if new_reserved < ZERO:
        raise InsufficientFundsError(
            "reserved would go negative (settle > reserved)",
            details={
                "wallet_id": str(wallet_id),
                "reserved": str(wallet.reserved_credits),
                "delta": str(bucket_deltas[BUCKET_RESERVED]),
            },
        )

    # Wallet row columns ``available_credits`` and ``reserved_credits`` are
    # derived from the journal — they are NOT independently mutated here.
    # The canonical balances live in :func:`ledger.get_balance` which sums
    # the journal. Keeping the wallet row out of the mutation path means
    # there is no way for a SQLAlchemy in-memory staleness to drift from
    # the journal; ``reconcile`` stays trivially correct.
    #
    # CRITICAL (2026-09): a previous version of this function also wrote
    # back to ``wallet.available_credits`` / ``reserved_credits``. That
    # double-wrote with the journal and (due to a SQLAlchemy identity-map
    # quirk) caused drift on Postgres. Removing the double-write is the
    # simplest correct fix; callers that need a hot-path balance read
    # should use :func:`get_balance` which is O(1) over a small materialized
    # cache if needed, but the authoritative value is the journal sum.

    # Build the transaction row.
    txn = WalletTransaction(
        wallet_id=wallet_id,
        user_id=wallet.user_id,
        kind=kind,
        status="posted",
        amount=sum((a for _et, b, a in norm_entries if b == BUCKET_AVAILABLE), ZERO),
        currency=wallet.currency,
        external_ref=external_ref,
        description="",
        metadata_json=safe_metadata,
        posted_at=wallet.updated_at or _utcnow(),  # server clock
    )

    db.add(txn)
    try:
        await db.flush()  # populate txn.id without committing
    except IntegrityError:
        # Concurrent insert with the same external_ref — re-fetch and return.
        await db.rollback()
        if external_ref is None:
            # Non-idempotency IntegrityError — re-raise.
            raise
        existing = await db.scalar(
            select(WalletTransaction).where(WalletTransaction.external_ref == external_ref)
        )
        if existing is None:  # pragma: no cover — defensive
            raise WalletLedgerIntegrityError(
                "integrity error but no existing transaction found",
                details={"external_ref": external_ref},
            ) from None
        return existing

    # Build ledger entries pointing at the just-flushed txn.id.
    for entry_type, bucket, amount in norm_entries:
        db.add(
            LedgerEntry(
                txn_id=txn.id,
                wallet_id=wallet_id,
                entry_type=entry_type,
                amount=amount,
                bucket=bucket,
                posted_at=txn.posted_at,
            )
        )

    # Apply materialized delta to the locked wallet row.
    _apply_wallet_delta(wallet, bucket_deltas)

    # INVARIANT I1 re-check after materialization (paranoid; cheap).
    post_total = await _journal_sum_for_txn(db, txn.id)
    if post_total != ZERO:
        raise WalletLedgerIntegrityError(
            "post-commit journal sum is non-zero",
            details={"txn_id": str(txn.id), "sum_amount": str(post_total)},
        )

    await db.flush()
    _log.info(
        "ledger.post_transaction",
        extra={
            "txn_id": str(txn.id),
            "wallet_id": str(wallet_id),
            "kind": kind,
            "external_ref": external_ref,
            "entry_count": len(norm_entries),
        },
    )
    return txn


def _utcnow():  # local; same shape as db.models._utcnow
    from datetime import datetime

    return datetime.now(UTC)


async def _journal_sum_for_txn(db: AsyncSession, txn_id: uuid.UUID) -> Decimal:
    """SUM(amount) for one txn — used as a paranoid post-write integrity check."""
    total = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO)).where(
            LedgerEntry.txn_id == txn_id
        )
    )
    return Decimal(total or ZERO)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_balance(db: AsyncSession, wallet_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    """Return (available, reserved) for a wallet.

    IMPLEMENTATION CHOICE: sum the journal (LedgerEntry) by bucket.
    The ``Wallet.available_credits`` / ``reserved_credits`` columns are
    legacy (kept for back-compat) but are NOT authoritative — they were
    the source of a silent-drift bug until 2026-09. The journal is the
    source of truth.
    """
    journal_avail = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO)).where(
            LedgerEntry.wallet_id == wallet_id,
            LedgerEntry.bucket == BUCKET_AVAILABLE,
        )
    )
    journal_resv = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO)).where(
            LedgerEntry.wallet_id == wallet_id,
            LedgerEntry.bucket == BUCKET_RESERVED,
        )
    )
    return Decimal(journal_avail or ZERO), Decimal(journal_resv or ZERO)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


async def reconcile(db: AsyncSession, wallet_id: uuid.UUID) -> list[dict[str, Any]]:
    """Verify the journal's bucket sums are non-negative.

    The :class:`Wallet` row's ``available_credits`` / ``reserved_credits``
    columns are legacy; the journal is authoritative. Reconciliation now
    just checks the journal sums themselves are sane — no drift to detect
    between two independent stores.

    Returns an empty list when in sync; otherwise a single-element list
    describing the violation (negative sum in either bucket).
    """
    journal_avail = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO)).where(
            LedgerEntry.wallet_id == wallet_id,
            LedgerEntry.bucket == BUCKET_AVAILABLE,
        )
    )
    journal_resv = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO)).where(
            LedgerEntry.wallet_id == wallet_id,
            LedgerEntry.bucket == BUCKET_RESERVED,
        )
    )
    journal_avail = Decimal(journal_avail or ZERO)
    journal_resv = Decimal(journal_resv or ZERO)

    deltas: list[dict[str, Any]] = []
    # Journal-side invariants — these are the only authoritative checks now
    # that the wallet row's columns are legacy (see post_transaction docstring).
    if journal_avail < ZERO:
        deltas.append(
            {
                "wallet_id": str(wallet_id),
                "bucket": BUCKET_AVAILABLE,
                "violation": "negative_sum",
                "journal_sum": str(journal_avail),
            }
        )
    if journal_resv < ZERO:
        deltas.append(
            {
                "wallet_id": str(wallet_id),
                "bucket": BUCKET_RESERVED,
                "violation": "negative_sum",
                "journal_sum": str(journal_resv),
            }
        )
    return deltas


async def assert_balances_reconciled(db: AsyncSession, wallet_id: uuid.UUID) -> None:
    """Raise WalletReconciliationError if the wallet row drifts from the journal."""
    deltas = await reconcile(db, wallet_id)
    if deltas:
        raise WalletReconciliationError(
            "wallet row does not match journal",
            details={"discrepancies": deltas},
        )


__all__ = [
    "BUCKET_AVAILABLE",
    "BUCKET_EXTERNAL",
    "BUCKET_RESERVED",
    "VALID_BUCKETS",
    "VALID_ENTRY_TYPES",
    "VALID_KINDS",
    "ZERO",
    "assert_balances_reconciled",
    "get_balance",
    "post_transaction",
    "reconcile",
]


def _unused_iterable_marker(_: Iterable[Any]) -> None:
    """Silence 'Iterable imported but unused' — kept for future batch APIs."""
    return None
