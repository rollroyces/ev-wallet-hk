"""Apple Pay / Google Pay payload validation.

TODO(console/validate): full PKPaymentToken verification requires:

    1. The Apple merchant identity certificate (X.509) configured via
       ``EVW_APPLE_PAY_MERCHANT_ID`` + a PEM file on disk.
    2. Decrypting ``paymentData.header`` using ECDH against the cert.
    3. Validating the inner JSON Web Signature (JWS) against Apple's
       root CA.
    4. Verifying the ephemeral public key in ``header.ephemeralPublicKey``
       matches the one in the JWS protected header.

For this milestone we ship structural validation only — enough to catch
trivially malformed payloads (missing transactionIdentifier, wrong amount,
no token blob) and feed the rest of the pipeline. Swap this stub out when
the merchant identity cert is provisioned.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from evwallet.errors import ApplePayValidationError, GooglePayValidationError

ZERO = Decimal("0")


def validate_apple_pay_payload(payload: dict[str, Any], *, expected_amount: Decimal) -> None:
    """Validate the SHAPE of an Apple Pay PKPayment payload.

    Args:
        payload: The JSON body POSTed by the iOS app.
        expected_amount: Amount we are about to credit.

    Raises:
        ApplePayValidationError: If the payload is malformed.
    """
    if not isinstance(payload, dict):
        raise ApplePayValidationError("apple payload must be a JSON object")

    txn_id = payload.get("transactionIdentifier")
    if not isinstance(txn_id, str) or not txn_id:
        raise ApplePayValidationError("apple payload missing transactionIdentifier")

    payment_data = payload.get("paymentData")
    if not isinstance(payment_data, dict):
        raise ApplePayValidationError("apple payload missing paymentData object")

    # The encrypted blob — must exist; we do NOT decrypt in this stub.
    if "data" not in payment_data:
        raise ApplePayValidationError("apple payload.paymentData.data missing")

    # Amount check: client may pass the amount they intend to pay; if so,
    # it must match.
    declared_amount = payload.get("amount")
    if declared_amount is not None:
        try:
            amt = Decimal(str(declared_amount)).quantize(Decimal("0.0001"))
        except Exception as e:
            raise ApplePayValidationError("apple payload.amount is not a valid decimal") from e
        if amt != expected_amount:
            raise ApplePayValidationError(
                "apple payload amount does not match expected",
                details={"declared": str(amt), "expected": str(expected_amount)},
            )


def validate_google_pay_payload(payload: dict[str, Any], *, expected_amount: Decimal) -> None:
    """Validate the SHAPE of a Google Pay token payload.

    Args:
        payload: The JSON body POSTed by the Android app / web.
        expected_amount: Amount we are about to credit.

    Raises:
        GooglePayValidationError: If the payload is malformed.
    """
    if not isinstance(payload, dict):
        raise GooglePayValidationError("google payload must be a JSON object")

    # Google Pay's PaymentData token shape is:
    #   {"id": "...", "paymentMethodData": {...}, "email": "...?}
    # We accept either ``id`` or ``token`` at the top level for flexibility.
    token = payload.get("id") or payload.get("token")
    if not isinstance(token, str) or not token:
        raise GooglePayValidationError("google payload missing 'id' or 'token' string")

    declared_amount = payload.get("amount")
    if declared_amount is not None:
        try:
            amt = Decimal(str(declared_amount)).quantize(Decimal("0.0001"))
        except Exception as e:
            raise GooglePayValidationError("google payload.amount is not a valid decimal") from e
        if amt != expected_amount:
            raise GooglePayValidationError(
                "google payload amount does not match expected",
                details={"declared": str(amt), "expected": str(expected_amount)},
            )


__all__ = ["validate_apple_pay_payload", "validate_google_pay_payload"]
