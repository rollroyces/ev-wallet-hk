"""Wallet REST endpoints — /api/v1/wallet/* (ARCHITECTURE.md §'Wallet')."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any

import stripe
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeError

from evwallet.auth.deps import current_user
from evwallet.config import get_settings
from evwallet.db.models import User, Wallet, WalletTransaction
from evwallet.db.session import get_db
from evwallet.errors import (
    BackendUnavailableError,
    ValidationError,
    WalletNotFoundError,
)
from evwallet.logging import get_logger
from evwallet.wallet import topup as wallet_topup

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
    recent_transactions: list[TransactionOut]


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
        raise WalletNotFoundError("no wallet for user", details={"user_id": str(user.id)})
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
            raise ValidationError("stripe topup requires source_payload.payment_intent_id")
        txn = await wallet_topup.topup_stripe(db, wallet.id, body.amount_hkd, payment_intent_id)
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
    return TopupResult(transaction_id=txn.id, status=txn.status, amount_hkd=txn.amount)


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

    rows: Sequence[WalletTransaction] = (await db.execute(stmt)).scalars().all()
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
    return BalanceOut(available_hkd=wallet.available_credits, reserved_hkd=wallet.reserved_credits)


# ---------------------------------------------------------------------------
# Intent creation — used by web/mobile to kick off a topup before calling
# POST /wallet/topup with the resulting payment id.
# ---------------------------------------------------------------------------


class TopupIntentIn(BaseModel):
    """Body for intent-creation endpoints.

    ``amount_hkd`` is the wallet-credit amount the user is requesting. The
    gateway will charge a slightly larger amount (e.g. amount + 1.5% fee) but
    the wallet credit is always ``amount_hkd`` as requested.
    """

    model_config = ConfigDict(extra="forbid")

    amount_hkd: Decimal = Field(gt=Decimal("0"), le=Decimal("10000"))


class StripeIntentOut(BaseModel):
    """Response from POST /wallet/topup/stripe/intent."""

    payment_intent_id: str
    client_secret: str
    amount_hkd: Decimal
    currency: str = "hkd"


@router.post("/topup/stripe/intent", response_model=StripeIntentOut)
async def create_stripe_topup_intent(
    body: TopupIntentIn,
    user: Annotated[User, Depends(current_user)],
) -> StripeIntentOut:
    """Create a Stripe PaymentIntent for a wallet topup.

    Returns the ``client_secret`` so the client can confirm the payment
    via ``stripe.confirmCardPayment(client_secret, ...)`` in the browser,
    or via the native Stripe sheet on mobile. The webhook
    ``POST /api/v1/payments/stripe/webhook`` handles settlement —
    afterwards the wallet is credited via the existing
    ``/wallet/topup?source=stripe`` path.

    Returns ``BackendUnavailableError`` (503) if Stripe is not configured
    (no ``EVW_STRIPE_SECRET_KEY``) so the web/mobile UI can fall back to
    Apple/Google Pay.
    """
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BackendUnavailableError(
            "stripe topup unavailable: EVW_STRIPE_SECRET_KEY not configured",
        )

    # Convert HKD to the smallest currency unit (cents).
    amount_cents = int((body.amount_hkd * 100).quantize(Decimal("1")))

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="hkd",
            metadata={
                "wallet_user_id": str(user.id),
                "purpose": "ev_wallet_topup",
                "credit_amount_hkd": str(body.amount_hkd),
            },
            automatic_payment_methods={"enabled": True},
        )
    except StripeError as e:
        _log.error("stripe.payment_intents.create failed", extra={"error": str(e)})
        raise BackendUnavailableError(f"stripe error: {e.user_message or 'unknown'}") from e

    return StripeIntentOut(
        payment_intent_id=intent.id,
        client_secret=intent.client_secret or "",
        amount_hkd=body.amount_hkd,
    )


class ApplePayIntentOut(BaseModel):
    """Response from POST /wallet/topup/apple/intent.

    Apple Pay doesn't have a server-side "intent" the way Stripe does —
    the PKPaymentToken is generated client-side. This endpoint exists
    primarily to validate the merchant configuration is present
    (Apple Pay merchant id + Apple Wallet signing key) and to return the
    merchant identity the client should present to the Apple Pay sheet.
    """

    merchant_id: str
    supported_networks: list[str] = Field(default_factory=lambda: ["visa", "masterCard", "amex"])
    merchant_capabilities: list[str] = Field(default_factory=lambda: ["supports3DS"])
    currency: str = "HKD"
    country_code: str = "HK"


@router.post("/topup/apple/intent", response_model=ApplePayIntentOut)
async def create_apple_pay_topup_intent(
    _user: Annotated[User, Depends(current_user)],
) -> ApplePayIntentOut:
    """Return the Apple Pay merchant configuration for the client sheet.

    The actual PKPaymentToken is generated client-side via ``expo-apple-pay``
    (or ``PassKit`` in bare iOS) and posted to ``POST /wallet/topup``
    with ``source=apple_pay`` for validator + settlement.

    Returns ``BackendUnavailableError`` (503) if ``EVW_APPLE_PAY_MERCHANT_ID``
    is not configured.
    """
    settings = get_settings()
    if not settings.apple_pay_merchant_id:
        raise BackendUnavailableError(
            "apple pay topup unavailable: EVW_APPLE_PAY_MERCHANT_ID not configured",
        )
    return ApplePayIntentOut(merchant_id=settings.apple_pay_merchant_id)


def build_router() -> APIRouter:
    """Return the wallet router (factory pattern matching charging/stations routers)."""
    return router


__all__ = ["build_router", "router"]
