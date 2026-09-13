"""Top-up flows — Stripe / Apple Pay / Google Pay.

All three paths funnel through ``evwallet.payments.*`` verifiers and then
``evwallet.wallet.ledger.post_transaction``. The ledger is the source of
truth — topup is just a ``topup_debit`` pair on the journal.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.config import get_settings
from evwallet.db.models import WalletTransaction
from evwallet.errors import PaymentError, ValidationError
from evwallet.logging import get_logger
from evwallet.payments import apple_google, stripe
from evwallet.wallet.ledger import BUCKET_AVAILABLE, BUCKET_EXTERNAL, post_transaction

_log = get_logger(__name__)

ZERO = Decimal("0")


def _validate_topup_amount(amount: Decimal) -> Decimal:
    """Reject negative/zero/over-cap top-up amounts.

    The cap here is intentionally looser than ``preauth_max_hkd`` because
    real top-ups (e.g. EV driver topping up HKD 5000) routinely exceed the
    pre-auth hold. Cap is per-call, configurable via Settings.topup_max_hkd
    if added later; for now we cap at 10x preauth_max_hkd as a defensive bound.
    """
    settings = get_settings()
    if not isinstance(amount, Decimal):
        raise ValidationError(
            f"topup amount must be Decimal, got {type(amount).__name__}"
        )
    if amount <= ZERO:
        raise ValidationError(
            "topup amount must be > 0", details={"amount": str(amount)}
        )
    soft_cap = (settings.preauth_max_hkd * Decimal("10")).quantize(Decimal("0.0001"))
    if amount > soft_cap:
        raise ValidationError(
            "topup amount exceeds soft cap",
            details={"amount": str(amount), "soft_cap": str(soft_cap)},
        )
    return amount


def _external_ref_for(source: str, payload: str) -> str:
    """Compute deterministic idempotency key for a top-up source payload."""
    h = hashlib.sha256(f"{source}|{payload}".encode()).hexdigest()
    return f"topup:{source}:{h}"


async def topup_stripe(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    stripe_payment_intent_id: str,
) -> WalletTransaction:
    """Top up the wallet via a confirmed Stripe PaymentIntent.

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Amount to credit (Decimal, must be > 0).
        stripe_payment_intent_id: Stripe's PaymentIntent id; the verifier
            confirms the intent succeeded AND that the amount matches.

    Returns:
        The persisted WalletTransaction.

    Raises:
        PaymentError: If the Stripe verifier rejects the intent.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_topup_amount(amount_hkd)

    # Verify externally — raises PaymentError on mismatch. We do this BEFORE
    # writing to the ledger so a failed verification never touches the journal.
    await stripe.verify_payment_intent(
        payment_intent_id=stripe_payment_intent_id, expected_amount=amount
    )

    return await _post_topup(
        db,
        wallet_id,
        amount,
        external_ref=f"stripe:{stripe_payment_intent_id}",
        metadata={"source": "stripe", "stripe_payment_intent_id": stripe_payment_intent_id},
    )


async def topup_apple_pay(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    apple_payload: dict[str, Any],
) -> WalletTransaction:
    """Top up the wallet via Apple Pay (native sheet).

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Amount to credit (Decimal).
        apple_payload: PKPayment token payload — must contain ``transactionIdentifier``
            and ``paymentData``.

    Returns:
        The persisted WalletTransaction.

    Raises:
        PaymentError: If Apple Pay payload is structurally invalid.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_topup_amount(amount_hkd)

    # Structural validation only — full PKPaymentToken signature validation
    # requires the Apple merchant identity cert and is done by an out-of-band
    # service in production. See apple_google.py TODO.
    apple_google.validate_apple_pay_payload(apple_payload, expected_amount=amount)

    token_id = apple_payload.get("transactionIdentifier") or ""
    if not token_id:
        raise PaymentError(
            "apple_payload.transactionIdentifier missing",
            details={"source": "apple_pay"},
        )
    return await _post_topup(
        db,
        wallet_id,
        amount,
        external_ref=f"apple_pay:{token_id}",
        metadata={"source": "apple_pay", "transaction_id": token_id},
    )


async def topup_google_pay(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount_hkd: Decimal,
    google_payload: dict[str, Any],
) -> WalletTransaction:
    """Top up the wallet via Google Pay.

    Args:
        db: Active async session.
        wallet_id: Target wallet UUID.
        amount_hkd: Amount to credit (Decimal).
        google_payload: Google Pay token payload — must contain a token string.

    Returns:
        The persisted WalletTransaction.

    Raises:
        PaymentError: If Google Pay payload is structurally invalid.
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_topup_amount(amount_hkd)

    google_google = apple_google  # alias for clarity
    google_google.validate_google_pay_payload(google_payload, expected_amount=amount)

    token = google_payload.get("id") or google_payload.get("token") or ""
    if not token:
        raise PaymentError(
            "google_payload.id/token missing", details={"source": "google_pay"}
        )
    return await _post_topup(
        db,
        wallet_id,
        amount,
        external_ref=f"google_pay:{token}",
        metadata={"source": "google_pay", "token": token},
    )


async def _post_topup(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    amount: Decimal,
    *,
    external_ref: str,
    metadata: dict[str, Any],
) -> WalletTransaction:
    """Shared ledger write for all top-up sources.

    Entries:
        ('topup_debit', 'external', -amount)
        ('topup_debit', 'available', +amount)
    """
    txn = await post_transaction(
        db,
        wallet_id,
        kind="topup",
        entries=[
            ("topup_debit", BUCKET_EXTERNAL, -amount),
            ("topup_debit", BUCKET_AVAILABLE, +amount),
        ],
        external_ref=external_ref,
        metadata=metadata,
    )
    _log.info(
        "wallet.topup",
        extra={
            "wallet_id": str(wallet_id),
            "amount_hkd": str(amount),
            "external_ref": external_ref,
            "txn_id": str(txn.id),
        },
    )
    return txn


__all__ = ["topup_apple_pay", "topup_google_pay", "topup_stripe"]
