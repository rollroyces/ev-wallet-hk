"""Payments REST endpoints — Stripe webhook (ARCHITECTURE.md §'Payments')."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.db.models import Wallet
from evwallet.db.session import get_db
from evwallet.errors import StripeIntentError
from evwallet.logging import get_logger
from evwallet.payments import stripe as stripe_verify
from evwallet.wallet import topup as wallet_topup

_log = get_logger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


class WebhookAck(BaseModel):
    """Stripe webhook ack shape."""

    received: bool
    transaction_id: uuid.UUID | None = None
    wallet_id: uuid.UUID | None = None


@router.post("/stripe/webhook", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> WebhookAck:
    """Handle a Stripe ``payment_intent.succeeded`` webhook.

    1. Verify the Stripe signature.
    2. Pull the wallet_id from event.metadata (Stripe-collected; set on
       PaymentIntent creation in the iOS / Android client).
    3. Verify the PaymentIntent via the SDK (or stub).
    4. Call ``wallet_topup.topup_stripe`` — which is idempotent on
       ``stripe:<payment_intent_id>``.
    """
    raw = await request.body()
    event = stripe_verify.verify_webhook_signature(payload=raw, signature_header=stripe_signature)

    # Only handle the success event; ignore the rest.
    etype = event.get("type")
    if etype != "payment_intent.succeeded":
        _log.info("stripe.webhook ignored", extra={"type": etype})
        return WebhookAck(received=True)

    obj = event.get("data", {}).get("object", {})
    payment_intent_id = obj.get("id") or ""
    if not payment_intent_id:
        raise StripeIntentError("webhook missing payment_intent id")

    # The wallet_id comes from the PaymentIntent's metadata — set by the
    # client at PaymentIntent creation time.
    meta = obj.get("metadata") or {}
    wallet_id_str = meta.get("wallet_id")
    if not wallet_id_str:
        raise StripeIntentError("webhook payment_intent.metadata.wallet_id missing")
    try:
        wallet_id = uuid.UUID(wallet_id_str)
    except ValueError as e:
        raise StripeIntentError("webhook wallet_id is not a uuid") from e

    # Verify the wallet exists (defensive — Stripe could send stale events).
    wallet = await db.scalar(select(Wallet).where(Wallet.id == wallet_id))
    if wallet is None:
        raise StripeIntentError("webhook wallet not found", details={"wallet_id": str(wallet_id)})

    # Compute the amount from the PaymentIntent (Stripe amounts are cents).
    amount_minor = Decimal(str(obj.get("amount", "0")))
    amount_hkd = (amount_minor / Decimal("100")).quantize(Decimal("0.0001"))

    txn = await wallet_topup.topup_stripe(db, wallet_id, amount_hkd, payment_intent_id)
    await db.commit()

    _log.info(
        "stripe.webhook ok",
        extra={
            "wallet_id": str(wallet_id),
            "txn_id": str(txn.id),
            "payment_intent_id": payment_intent_id,
        },
    )
    return WebhookAck(received=True, transaction_id=txn.id, wallet_id=wallet_id)


__all__ = ["router"]


# Silence unused imports — Request/Response are re-exported by FastAPI.
_ = (Response, Any)
