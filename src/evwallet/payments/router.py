"""Payments REST endpoints — Stripe webhook (ARCHITECTURE.md §'Payments').

Single endpoint — ``POST /api/v1/payments/stripe/webhook`` — receives
``application/json`` POSTs from Stripe. Stripe includes a
``Stripe-Signature`` header on every webhook POST; we verify it via
:func:`evwallet.payments.stripe.verify_webhook_signature`, then dispatch
on ``event.type``.

Idempotency
-----------

We persist ``event.id`` in the ``stripe_webhook_events`` table. A second
delivery of the same event (Stripe retries on non-2xx responses) collides
on the PK unique-violation; we translate that into a 200 OK so Stripe
treats the delivery as acknowledged and stops retrying. The same event id
is also rejected at the ledger layer (post_transaction is idempotent on
``external_ref = stripe:<payment_intent_id>``) so even a racing retry
after the first write committed ends up a no-op.

Supported event types
---------------------

* ``payment_intent.succeeded`` — credit the wallet via
  :func:`evwallet.wallet.topup.topup_stripe`.
* ``payment_intent.payment_failed`` — record the failure; do NOT credit.
* ``payment_intent.canceled`` — record; do NOT credit.

Any other event type is acknowledged with 200 OK and logged so Stripe
does not retry it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.db.models import StripeWebhookEvent, Wallet
from evwallet.db.session import get_db
from evwallet.errors import StripeIntentError, StripeSignatureError
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
    event_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Timezone-aware UTC now — used for processed_at bookkeeping."""
    return datetime.now(tz=UTC)


async def _mark_processed(db: AsyncSession, event_id: str) -> None:
    """Flip a row's status to 'processed' and set processed_at = now()."""
    row = await db.get(StripeWebhookEvent, event_id)
    if row is None:
        return
    row.status = "processed"
    row.processed_at = _utcnow()
    row.error = None
    await db.flush()


async def _mark_failed(db: AsyncSession, event_id: str, error: str) -> None:
    """Flip a row's status to 'failed' and record the error message."""
    row = await db.get(StripeWebhookEvent, event_id)
    if row is None:
        return
    row.status = "failed"
    row.processed_at = _utcnow()
    row.error = error
    # Truncate to keep the column bounded; the table is a log, not a dump.
    if len(error) > 1024:
        row.error = error[:1021] + "..."
    await db.flush()


async def _record_event(
    db: AsyncSession,
    *,
    event_id: str,
    event_type: str,
) -> StripeWebhookEvent | None:
    """Insert the idempotency row, returning ``None`` if it already exists.

    Returns the new row on first delivery. Returns ``None`` on a duplicate
    (``IntegrityError`` from the PK collision) so the caller can short-
    circuit without raising.

    Args:
        db: Async session.
        event_id: Stripe event id (``evt_***``).
        event_type: Stripe event type (``payment_intent.succeeded`` etc).

    Returns:
        The newly-inserted :class:`StripeWebhookEvent`, or ``None`` when
        ``event_id`` was already in the table.
    """
    row = StripeWebhookEvent(id=event_id, type=event_type, status="received")
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    return row


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/stripe/webhook", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> WebhookAck:
    """Handle a Stripe webhook POST.

    1. Verify the ``Stripe-Signature`` header. Bad/missing signature is
       a 4xx — Stripe treats non-2xx as retry-worthy, but a signature
       failure is NOT retriable, so we 4xx instead of 5xx.
    2. Look up ``event.id`` in the ``stripe_webhook_events`` table; if
       already present, return 200 OK without re-processing.
    3. Dispatch on ``event.type``.
    """
    raw = await request.body()

    # ----- 1. Signature check -------------------------------------------
    try:
        event = stripe_verify.verify_webhook_signature(
            payload=raw, signature_header=stripe_signature
        )
    except StripeSignatureError:
        # Re-raise so the global IDPError handler emits the canonical
        # 4xx envelope. We deliberately do NOT 200 here — a forged
        # request that gets a 200 ack is a configuration leak.
        raise

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_id.startswith("evt_"):
        raise StripeIntentError(
            "webhook event missing or malformed 'id'",
            details={"event_id": event_id, "type": event_type},
        )
    if not event_type:
        raise StripeIntentError(
            "webhook event missing 'type'",
            details={"event_id": event_id},
        )

    # ----- 2. Idempotency -----------------------------------------------
    new_row = await _record_event(db, event_id=event_id, event_type=event_type)
    if new_row is None:
        # Duplicate delivery — Stripe retries on non-2xx. Return 200 so it
        # stops. We do NOT touch the existing row's status — the original
        # delivery owns it.
        _log.info("stripe.webhook duplicate", extra={"event_id": event_id, "type": event_type})
        return WebhookAck(received=True, event_id=event_id)

    # ----- 3. Dispatch -------------------------------------------------
    try:
        if event_type == "payment_intent.succeeded":
            ack = await _handle_payment_intent_succeeded(db, event)
            # _handle_* may have committed; re-fetch the row to mark it.
            # If the ledger write failed, _handle_ raises and we land in
            # the except branch below.
            await _mark_processed(db, event_id)
            await db.commit()
            _log.info(
                "stripe.webhook ok",
                extra={
                    "event_id": event_id,
                    "type": event_type,
                    "wallet_id": str(ack.wallet_id) if ack.wallet_id else None,
                    "transaction_id": str(ack.transaction_id) if ack.transaction_id else None,
                },
            )
            return ack
        if event_type == "payment_intent.payment_failed":
            _handle_payment_intent_failed(event)
            await _mark_processed(db, event_id)
            await db.commit()
            return WebhookAck(received=True, event_id=event_id)
        if event_type == "payment_intent.canceled":
            _handle_payment_intent_canceled(event)
            await _mark_processed(db, event_id)
            await db.commit()
            return WebhookAck(received=True, event_id=event_id)

        # Unknown / unhandled event type — ack with 200 so Stripe stops
        # retrying, but do nothing else.
        _log.info("stripe.webhook ignored", extra={"event_id": event_id, "type": event_type})
        await _mark_processed(db, event_id)
        await db.commit()
        return WebhookAck(received=True, event_id=event_id)

    except StripeIntentError:
        await _mark_failed(db, event_id, "stripe_intent_error")
        await db.commit()
        raise
    except Exception as exc:
        # Unknown handler failure — log the row, re-raise as StripeIntent
        # so the caller sees a 4xx (the stripe_intent_error path above
        # catches domain errors; this is the catch-all for unexpected
        # failures like a DB error).
        await _mark_failed(db, event_id, f"{type(exc).__name__}: {exc}")
        await db.commit()
        raise


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_payment_intent_succeeded(
    db: AsyncSession,
    event: dict[str, Any],
) -> WebhookAck:
    """Credit the wallet for a successful PaymentIntent.

    Stripe amounts are in minor units (cents). The wallet_id and the
    payment_intent_id come from the event payload; the wallet_id is set
    on PaymentIntent.metadata at creation time (by the mobile client,
    which knows the local wallet id before posting the topup).
    """
    obj = event.get("data", {}).get("object") or {}
    payment_intent_id = str(obj.get("id") or "")
    if not payment_intent_id or not payment_intent_id.startswith("pi_"):
        raise StripeIntentError(
            "webhook missing or malformed payment_intent id",
            details={"payment_intent_id": payment_intent_id},
        )

    meta = obj.get("metadata") or {}
    if not isinstance(meta, dict):
        raise StripeIntentError(
            "webhook payment_intent.metadata is not an object",
            details={"payment_intent_id": payment_intent_id},
        )
    wallet_id_str = meta.get("wallet_id")
    if not wallet_id_str:
        raise StripeIntentError(
            "webhook payment_intent.metadata.wallet_id missing",
            details={"payment_intent_id": payment_intent_id},
        )
    try:
        wallet_id = uuid.UUID(str(wallet_id_str))
    except (ValueError, TypeError) as e:
        raise StripeIntentError(
            "webhook wallet_id is not a uuid",
            details={"wallet_id": str(wallet_id_str)},
        ) from e

    # Defensive — Stripe could send a stale event for a deleted wallet.
    wallet = await db.get(Wallet, wallet_id)
    if wallet is None:
        raise StripeIntentError(
            "webhook wallet not found",
            details={"wallet_id": str(wallet_id), "payment_intent_id": payment_intent_id},
        )

    amount_minor = obj.get("amount")
    try:
        amount_minor_int = int(amount_minor)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise StripeIntentError(
            "webhook payment_intent.amount is not an integer",
            details={"payment_intent_id": payment_intent_id, "amount": str(amount_minor)},
        ) from e
    amount_hkd = (Decimal(amount_minor_int) / Decimal("100")).quantize(Decimal("0.0001"))

    # The ledger's post_transaction is idempotent on external_ref, so
    # the rare case where Stripe retries after our 200 + DB commit but
    # before the retry tail flips to delivered is a safe no-op.
    txn = await wallet_topup.topup_stripe(
        db, wallet_id, amount_hkd, payment_intent_id
    )

    return WebhookAck(
        received=True,
        transaction_id=txn.id,
        wallet_id=wallet_id,
        event_id=str(event.get("id") or ""),
    )


def _handle_payment_intent_failed(event: dict[str, Any]) -> None:
    """Log a payment_intent.payment_failed event. No wallet credit."""
    obj = event.get("data", {}).get("object") or {}
    payment_intent_id = str(obj.get("id") or "")
    last_payment_error = obj.get("last_payment_error") or {}
    error_code = last_payment_error.get("code") or last_payment_error.get("type") or "unknown"
    error_message = last_payment_error.get("message") or ""
    _log.warning(
        "stripe.webhook payment_failed",
        extra={
            "event_id": str(event.get("id") or ""),
            "payment_intent_id": payment_intent_id,
            "error_code": error_code,
            "error_message": error_message,
        },
    )


def _handle_payment_intent_canceled(event: dict[str, Any]) -> None:
    """Log a payment_intent.canceled event. No wallet credit."""
    obj = event.get("data", {}).get("object") or {}
    payment_intent_id = str(obj.get("id") or "")
    cancellation_reason = obj.get("cancellation_reason") or "unknown"
    _log.info(
        "stripe.webhook canceled",
        extra={
            "event_id": str(event.get("id") or ""),
            "payment_intent_id": payment_intent_id,
            "cancellation_reason": cancellation_reason,
        },
    )


# Silence unused imports — ``Response`` is referenced by FastAPI machinery,
# ``Any`` is part of the type annotations above.
_ = (Response,)


__all__ = ["router"]