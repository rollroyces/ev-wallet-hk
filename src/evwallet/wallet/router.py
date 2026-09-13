"""Wallet REST endpoints — /api/v1/wallet/* (ARCHITECTURE.md §'Wallet')."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.auth.deps import current_user
from evwallet.db.models import User, Wallet, WalletTransaction
from evwallet.db.session import get_db
from evwallet.errors import (
    IDPError,
    InsufficientFundsError,
    ValidationError,
    WalletNotFoundError,
)
from evwallet.logging import get_logger
from evwallet.wallet import ledger
from evwallet.wallet import topup as wallet_topup
from evwallet.payments import apple_google

_log = get_logger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"])


# ---------------------------------------------------------------------------
# Pydantic response/request shapes
# ---------------------------------------------------------------------------


class WalletSummary(BaseModel):
    """Top-level wallet state returned by GET /wallet."""

    available_hkd: Decimal
    reserved_hkd: Decimal
    currency: str
    recent_transactions: list["TransactionOut"]


class BalanceOut(BaseModel):
    """Returned by GET /wallet/balance."""

    available_hkd: Decimal
    reserved_hkd: Decimal


class TransactionOut(BaseModel):
    """One transaction row."""

    id: uuid.UUID
    kind: str
    status: str
    amount: Decimal
    currency: str
    description: str = ""
    external_ref: str | None = None
    posted_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(populate_by_name=True)


class TopupRequest(BaseModel):
    """Body for POST /wallet/topup."""

    amount_hkd: Decimal
    source: str = Field(pattern=r"^(stripe|apple_pay|google_pay)$")
    source_payload: dict[str, Any] = Field(default_factory=dict)


class TopupResult(BaseModel):
    """Response for POST /wallet/topup."""

    transaction_id: uuid.UUID
    status: str
    amount_hkd: Decimal


class TransactionsPage(BaseModel):
    """Cursor-paginated transaction list."""

    transactions: list[TransactionOut]
    next_cursor: str | None = None


WalletSummary.model_rebuild()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_wallet_for_user(db: AsyncSession, user: User) -> Wallet:
    wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
    if wallet is None:
        raise WalletNotFoundError(
            "no wallet for user", details={"user_id": str(user.id)}
        )
    return wallet


def _txn_to_out(txn: WalletTransaction) -> TransactionOut:
    return TransactionOut(
        id=txn.id,
        kind=txn.kind,
        status=txn.status,
        amount=txn.amount,
        currency=txn.currency,
        description=txn.description,
        external_ref=txn.external_ref,
        posted_at=txn.posted_at.isoformat() if txn.posted_at else "",
        metadata=txn.metadata_json or {},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=WalletSummary)
async def get_wallet(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WalletSummary:
    """Return the current user's wallet summary + recent transactions."""
    wallet = await _load_wallet_for_user(db, user)
    recent = list(
        (
            await db.execute(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet.id)
                .order_by(desc(WalletTransaction.posted_at))
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return WalletSummary(
        available_hkd=wallet.available_credits,
        reserved_hkd=wallet.reserved_credits,
        currency=wallet.currency,
        recent_transactions=[_txn_to_out(t) for t in recent],
    )


@router.post("/topup", response_model=TopupResult)
async def topup(
    body: TopupRequest,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopupResult:
    """Top up the wallet via Stripe / Apple Pay / Google Pay.

    For Stripe: ``source_payload`` should contain ``payment_intent_id``.
    For Apple/Google Pay: ``source_payload`` is the native token payload.
    """
    wallet = await _load_wallet_for_user(db, user)

    if body.source == "stripe":
        payment_intent_id = body.source_payload.get("payment_intent_id")
        if not payment_intent_id:
            raise ValidationError(
                "stripe topup requires source_payload.payment_intent_id"
            )
        txn = await wallet_topup.topup_stripe(
            db, wallet.id, body.amount_hkd, payment_intent_id
        )
    elif body.source == "apple_pay":
        txn = await wallet_topup.topup_apple_pay(
            db, wallet.id, body.amount_hkd, body.source_payload
        )
    elif body.source == "google_pay":
        txn = await wallet_topup.topup_google_pay(
            db, wallet.id, body.amount_hkd, body.source_payload
        )
    else:  # pragma: no cover — pattern guard above
        raise ValidationError(f"unsupported source: {body.source}")

    await db.commit()
    return TopupResult(
        transaction_id=txn.id, status=txn.status, amount_hkd=txn.amount
    )


@router.get("/transactions", response_model=TransactionsPage)
async def list_transactions(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> TransactionsPage:
    """Return the user's transaction history, newest first, cursor-paginated."""
    wallet = await _load_wallet_for_user(db, user)

    stmt = (
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
        .order_by(desc(WalletTransaction.posted_at), desc(WalletTransaction.id))
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_ts = uuid.UUID(cursor)  # not a real cursor; placeholder
        except ValueError as e:
            raise ValidationError("invalid cursor") from e
        stmt = stmt.where(WalletTransaction.id < cursor_ts)

    rows: Sequence[WalletTransaction] = (
        (await db.execute(stmt)).scalars().all()
    )
    has_more = len(rows) > limit
    page_rows = list(rows[:limit])
    next_cursor = str(page_rows[-1].id) if has_more and page_rows else None
    return TransactionsPage(
        transactions=[_txn_to_out(t) for t in page_rows],
        next_cursor=next_cursor,
    )


@router.get("/balance", response_model=BalanceOut)
async def get_balance(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BalanceOut:
    """Return the current user's wallet balance only (no transactions)."""
    wallet = await _load_wallet_for_user(db, user)
    return BalanceOut(
        available_hkd=wallet.available_credits, reserved_hkd=wallet.reserved_credits
    )


__all__ = ["router"]
