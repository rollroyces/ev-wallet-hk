"""Stripe PaymentIntent verification + webhook signature handling.

Real Stripe integration, no more stubs. Two surfaces here:

* :func:`verify_payment_intent` — server-side verification of a
  PaymentIntent id. Called from :func:`evwallet.wallet.topup.topup_stripe`
  to confirm the intent's status / currency / amount before crediting the
  wallet. When ``Settings.stripe_secret_key`` is unset, falls back to a
  permissive stub that only enforces ``pi_`` prefix shape so dev/test
  environments without Stripe creds can still run.

* :func:`verify_webhook_signature` — HMAC-SHA256 signature check on the
  raw webhook body using ``EVW_STRIPE_WEBHOOK_SECRET``. Delegates to
  ``stripe.Webhook.construct_event`` which raises ``ValueError`` on a
  bad / expired signature; we translate that to
  :class:`~evwallet.errors.StripeSignatureError`. In stub mode (no
  webhook secret) parses the JSON and accepts it (test-only).

Library bootstrap
-----------------

``stripe.api_key`` is configured from ``Settings.stripe_secret_key`` and
``stripe.api_version`` is pinned to ``2024-06-20`` so a future Stripe
library upgrade cannot silently change response shapes on us. The
helpers below call ``_ensure_stripe_configured()`` lazily so importing
this module does NOT need Stripe creds.

Event idempotency
-----------------

The :func:`evwallet.payments.router.stripe_webhook` endpoint persists
``event.id`` in the ``stripe_webhook_events`` table; a second delivery
collides on the PK and the router returns 200 without re-processing.
The Stripe SDK also dedupes automatically via its
``event['id']`` key.

Event types handled (others are acked but ignored):

* ``payment_intent.succeeded`` — credit the wallet.
* ``payment_intent.payment_failed`` — record; no credit.
* ``payment_intent.canceled`` — record; no credit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from evwallet.config import get_settings
from evwallet.errors import StripeIntentError, StripeSignatureError
from evwallet.logging import get_logger

_log = get_logger(__name__)

# Pinned so a stripe library upgrade does not silently change response
# shapes. Update deliberately, not by accident.
_STRIPE_API_VERSION = "2024-06-20"


def _ensure_stripe_configured() -> Any:
    """Lazy bootstrap of the stripe SDK from Settings.

    Returns the configured stripe module so callers can do
    ``stripe.Webhook.construct_event(...)`` without re-importing.

    The function does not import stripe at module scope because the
    library may not be installed in environments where only the
    stub-mode path is exercised (and importing the SDK in CI without
    creds should not be a hard requirement). When the secret key is
    unset we still import the SDK (the webhook path needs
    ``stripe.Webhook`` for HMAC), but skip ``api_key`` assignment.
    """
    import stripe as stripe_sdk

    settings = get_settings()
    if settings.stripe_secret_key:
        stripe_sdk.api_key = settings.stripe_secret_key
    stripe_sdk.api_version = _STRIPE_API_VERSION
    return stripe_sdk


# ---------------------------------------------------------------------------
# PaymentIntent verification
# ---------------------------------------------------------------------------


async def verify_payment_intent(
    *,
    payment_intent_id: str,
    expected_amount: Decimal,
) -> dict[str, Any]:
    """Verify that ``payment_intent_id`` exists and the amount matches.

    In stub mode (no ``EVW_STRIPE_SECRET_KEY`` set), this only enforces
    that ``payment_intent_id`` is non-empty and starts with ``pi_`` so
    tests / local dev can exercise the ledger without spinning up
    Stripe. In production ``EVW_STRIPE_SECRET_KEY`` MUST be set; the
    function then calls ``stripe.PaymentIntent.retrieve(...)`` and checks
    status, currency, and amount.

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
        try:
            stripe_sdk = _ensure_stripe_configured()
            intent = stripe_sdk.PaymentIntent.retrieve(payment_intent_id)
        except StripeIntentError:
            raise
        except Exception as e:  # pragma: no cover — depends on Stripe SDK
            raise StripeIntentError(
                "stripe SDK verification failed",
                details={"payment_intent_id": payment_intent_id, "error": str(e)},
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
                details={
                    "payment_intent_id": payment_intent_id,
                    "currency": intent.currency,
                },
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


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    *,
    payload: bytes,
    signature_header: str | None,
) -> dict[str, Any]:
    """Verify a Stripe webhook signature and return the parsed event.

    Uses ``stripe.Webhook.construct_event`` when
    ``EVW_STRIPE_WEBHOOK_SECRET`` is set; in stub mode, parses the JSON
    and accepts it. The returned dict has at least ``id`` and ``type``
    keys.

    Args:
        payload: Raw webhook body (bytes, NOT the parsed JSON).
        signature_header: Value of the ``Stripe-Signature`` header.

    Returns:
        Parsed event as a plain dict (so callers do not need to know
        about the Stripe SDK's object types).

    Raises:
        StripeSignatureError: If signature verification fails for any
            reason — missing header, malformed signature, timestamp out
            of tolerance, or payload that isn't valid JSON.
    """
    if not signature_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    settings = get_settings()
    if settings.stripe_webhook_secret:
        try:
            stripe_sdk = _ensure_stripe_configured()
            event = stripe_sdk.Webhook.construct_event(
                payload, signature_header, settings.stripe_webhook_secret
            )
        except StripeSignatureError:
            raise
        except Exception as e:
            # stripe.Webhook.construct_event raises ``ValueError`` on a
            # bad signature, ``SignatureVerificationError`` (a subclass)
            # on a timestamp outside the tolerance window, and
            # ``json.JSONDecodeError`` on a malformed body. We funnel
            # all of them into our domain error so the router only has
            # one failure type to handle.
            raise StripeSignatureError(
                "stripe webhook signature verification failed",
                details={"error": type(e).__name__},
            ) from e
        # ``construct_event`` returns a ``stripe.Event`` whose ``to_dict``
        # gives us a plain dict; ``dict(event)`` works too but routes
        # through ``__iter__`` which is only present on ``StripeObject``
        # subclasses — ``Event`` has it. Cast to dict for downstream.
        return _event_to_dict(event)
    # Stub mode — accept any well-formed JSON.
    import json

    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise StripeSignatureError("webhook payload is not valid JSON") from e


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert a Stripe SDK ``Event`` to a plain ``dict``.

    Stripe's ``Event`` is a ``StripeObject``; calling ``dict(event)``
    walks its ``__iter__`` to build a dict. ``construct_event`` may also
    hand us a plain dict when a stub is in play — we tolerate both.
    """
    if isinstance(event, dict):
        return event
    if hasattr(event, "to_dict"):
        result = event.to_dict()
        if isinstance(result, dict):
            return result
    if hasattr(event, "__iter__"):
        return dict(event)
    # Last resort — assume attribute access.
    return {k: getattr(event, k, None) for k in ("id", "type", "data")}


__all__ = [
    "verify_payment_intent",
    "verify_webhook_signature",
]