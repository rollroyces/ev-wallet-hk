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

from .reservation import _jsonable_metadata

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
        raise ValidationError(f"topup amount must be Decimal, got {type(amount).__name__}")
    if amount <= ZERO:
        raise ValidationError("topup amount must be > 0", details={"amount": str(amount)})
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
        metadata=_jsonable_metadata(
            {"source": "stripe", "stripe_payment_intent_id": stripe_payment_intent_id}
        ),
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
        apple_payload: PKPayment token payload — must contain
            ``transactionIdentifier`` and a ``paymentData`` object
            whose ``version`` / ``data`` / ``signature`` / ``header``
            fields are well-formed base64 (plus ASN.1-parseable
            signature). The verifier also cross-checks any declared
            amount against ``amount_hkd``.

    Returns:
        The persisted WalletTransaction.

    Raises:
        ApplePayValidationError: If the Apple Pay payload fails
            structural verification (raised before any ledger write).
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_topup_amount(amount_hkd)

    # Full PKPaymentToken verification (base64 + ASN.1 + declared
    # amount cross-check). Full cryptographic chain validation against
    # the merchant identity cert is gated on
    # Settings.apple_pay_merchant_cert_path — see the TODO inside
    # apple_google.verify_apple_pay_token.
    verified = await apple_google.verify_apple_pay_token(
        apple_payload, expected_amount=amount
    )
    token_id = str(verified.get("token_id") or "")
    if not token_id:
        # Defensive — verify_apple_pay_token already raised if the id
        # was missing, but a future refactor must not silently drop
        # the idempotency key.
        raise PaymentError(
            "apple_payload.transactionIdentifier missing after verify",
            details={"source": "apple_pay"},
        )
    return await _post_topup(
        db,
        wallet_id,
        amount,
        external_ref=f"apple_pay:{token_id}",
        metadata=_jsonable_metadata(
            {
                "source": "apple_pay",
                "transaction_id": token_id,
                "verified": True,
            }
        ),
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
        google_payload: Google Pay token payload — must contain a
            ``token`` (or ``id``) JWT string signed by Google. The
            verifier checks the audience, issuer, and declared amount.

    Returns:
        The persisted WalletTransaction.

    Raises:
        GooglePayValidationError: If the Google Pay payload fails
            verification (raised before any ledger write).
        ValidationError: If amount is non-positive or exceeds cap.
    """
    amount = _validate_topup_amount(amount_hkd)

    verified = await apple_google.verify_google_pay_token(
        google_payload, expected_amount=amount
    )
    token_id = str(verified.get("token_id") or "")
    if not token_id:
        raise PaymentError(
            "google_payload.id/token missing after verify",
            details={"source": "google_pay"},
        )
    return await _post_topup(
        db,
        wallet_id,
        amount,
        external_ref=f"google_pay:{token_id}",
        metadata=_jsonable_metadata(
            {
                "source": "google_pay",
                "token": token_id,
                "email": verified.get("email"),
                "verified": True,
            }
        ),
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
