"""Stripe PaymentIntent verification + webhook signature handling.

The actual Stripe SDK call requires ``stripe_secret_key`` in Settings; when
that key is absent (CI, local dev without Stripe creds), ``verify_payment_intent``
runs in stub mode that accepts any well-formed payment_intent_id. That stub
mode is INTENTIONALLY permissive — it lets tests exercise the ledger without
spinning up Stripe, but in production ``EVW_STRIPE_SECRET_KEY`` must be set.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from evwallet.config import get_settings
from evwallet.errors import StripeIntentError, StripeSignatureError
from evwallet.logging import get_logger

_log = get_logger(__name__)


async def verify_payment_intent(
    *,
    payment_intent_id: str,
    expected_amount: Decimal,
) -> dict[str, Any]:
    """Verify that ``payment_intent_id`` exists and the amount matches.

    In stub mode (no ``EVW_STRIPE_SECRET_KEY`` set), this only enforces that
    ``payment_intent_id`` is non-empty and starts with ``pi_``. The real
    Stripe SDK call lives behind the runtime gate.

    Args:
        payment_intent_id: Stripe's PaymentIntent id (e.g. ``pi_3Oq...``).
        expected_amount: Decimal amount the client claims to top up.

    Returns:
        The verified PaymentIntent payload as a dict.

    Raises:
        StripeIntentError: If verification fails for any reason.
    """
    if not payment_intent_id or not payment_intent_id.startswith("pi_"):
        raise StripeIntentError(
            "payment_intent_id must look like 'pi_...'",
            details={"payment_intent_id": payment_intent_id},
        )
    settings = get_settings()
    if settings.stripe_secret_key:
        # Production path — call the Stripe SDK.
        try:
            import stripe as stripe_sdk

            stripe_sdk.api_key = settings.stripe_secret_key
            intent = stripe_sdk.PaymentIntent.retrieve(payment_intent_id)
        except Exception as e:  # pragma: no cover — depends on Stripe SDK
            raise StripeIntentError(
                "stripe SDK verification failed",
                details={"payment_intent_id": payment_intent_id},
            ) from e
        if intent.status != "succeeded":
            raise StripeIntentError(
                f"payment intent status is {intent.status!r}, expected 'succeeded'",
                details={"payment_intent_id": payment_intent_id, "status": intent.status},
            )
        # Stripe amounts are in minor units (cents) for HKD.
        if intent.currency and intent.currency.lower() != "hkd":
            raise StripeIntentError(
                f"payment intent currency is {intent.currency!r}, expected 'HKD'",
                details={"payment_intent_id": payment_intent_id, "currency": intent.currency},
            )
        amount_minor = Decimal(str(intent.amount))
        amount_major = (amount_minor / Decimal("100")).quantize(Decimal("0.0001"))
        if amount_major != expected_amount:
            raise StripeIntentError(
                "stripe amount does not match expected top-up",
                details={
                    "payment_intent_id": payment_intent_id,
                    "stripe_amount_hkd": str(amount_major),
                    "expected_amount_hkd": str(expected_amount),
                },
            )
        return {
            "id": intent.id,
            "status": intent.status,
            "amount": str(amount_major),
            "currency": intent.currency,
        }
    # Stub mode — accept.
    _log.debug(
        "stripe.verify stub mode",
        extra={"payment_intent_id": payment_intent_id},
    )
    return {
        "id": payment_intent_id,
        "status": "succeeded",
        "amount": str(expected_amount),
        "currency": "hkd",
    }


def verify_webhook_signature(*, payload: bytes, signature_header: str | None) -> dict[str, Any]:
    """Verify a Stripe webhook signature and return the parsed event.

    Uses ``stripe.Webhook.construct_event`` when ``EVW_STRIPE_WEBHOOK_SECRET``
    is set; in stub mode, parses the JSON and accepts it.

    Args:
        payload: Raw webhook body (bytes, NOT the parsed JSON).
        signature_header: Value of the ``Stripe-Signature`` header.

    Returns:
        Parsed event dict.

    Raises:
        StripeSignatureError: If signature verification fails.
    """
    settings = get_settings()
    if not signature_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    if settings.stripe_webhook_secret:
        try:
            import stripe as stripe_sdk

            event = stripe_sdk.Webhook.construct_event(
                payload, signature_header, settings.stripe_webhook_secret
            )
        except Exception as e:  # pragma: no cover
            raise StripeSignatureError("stripe webhook signature verification failed") from e
        # event is a StripeObject; cast to dict for downstream consumers.
        return dict(event)  # type: ignore[arg-type]
    # Stub mode — accept any well-formed JSON.
    import json

    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise StripeSignatureError("webhook payload is not valid JSON") from e


__all__ = ["verify_payment_intent", "verify_webhook_signature"]
