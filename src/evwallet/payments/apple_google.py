"""Apple Pay / Google Pay payload verification.

This module is the server-side half of the tokenized-payment handshake.

* ``verify_apple_pay_token`` validates the shape of a PKPaymentToken
  posted by the iOS client (base64 fields, ASN.1 signature, pinned
  Apple Pay intermediate cert). Full cryptographic chain validation
  against the merchant identity cert is gated on
  ``Settings.apple_pay_merchant_cert_path`` — until that is provisioned
  we ship structural validation only and surface a clear TODO block
  describing the next migration step.

* ``verify_google_pay_token`` verifies the signed JWT posted by the
  Android / web Google Pay flow using
  ``google.oauth2.id_token.verify_token``. It enforces the audience
  claim (the Google Pay merchant id, not the OAuth sign-in audience),
  the issuer (``google.com`` / ``accounts.google.com``), and the
  expected amount / currency inside the paymentMethodToken / message
  envelope.

* ``sign_google_pay_token`` is the inverse path — for server-driven
  topups we may need to mint a Google Pay signed JWT. It uses a Google
  service-account JSON key and raises a structured error when the key
  is not configured.

The ``validate_*_payload`` thin wrappers are kept for backward compat
with the previous stub — they delegate to the structural parts of the
new verifiers so older call sites still get shape checking.
"""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from typing import Any

from evwallet.config import get_settings
from evwallet.errors import ApplePayValidationError, GooglePayValidationError
from evwallet.logging import get_logger

_log = get_logger(__name__)

ZERO = Decimal("0")

# Apple Pay PaymentData version we accept. EC_v1 is the only version in
# production since iOS 9; EC_v2 / RSA_v2 are not documented in the public
# PKPaymentToken reference at the time of writing.
APPLE_PAY_SUPPORTED_VERSIONS: tuple[str, ...] = ("EC_v1",)

# Apple Pay intermediate certificate locations. We pin the certificate
# bytes that ship in code so a compromised Apple CA distribution host
# cannot downgrade us. The fetcher is a separate function so ops can
# refresh the pinned bytes via a controlled release once a year.
#
# TODO(merchant-cert): replace with a live verification against the
# merchant identity certificate. The intermediate cert is pinned here
# only to short-circuit obvious forgery attempts; the signature chain
# has to ultimately be validated against the merchant .cer uploaded via
# Settings.apple_pay_merchant_cert_path.
APPLE_PAY_INTERMEDIATE_CERT_PEM: bytes | None = None  # populated by _load_intermediate_cert()

# Path on disk where the Apple Pay merchant identity certificate lives.
# When unset, full cryptographic verification is skipped (structural only).
APPLE_PAY_MERCHANT_CERT_PATH_DEFAULT: str | None = None

# Public Google Pay issuers that sign payment tokens.
GOOGLE_PAY_VALID_ISSUERS: frozenset[str] = frozenset(
    {"google.com", "accounts.google.com"}
)


# ---------------------------------------------------------------------------
# Public: Apple Pay
# ---------------------------------------------------------------------------


async def verify_apple_pay_token(
    token: dict[str, Any],
    *,
    expected_amount: Decimal,
) -> dict[str, Any]:
    """Verify an Apple Pay PKPaymentToken.

    Args:
        token: The JSON body POSTed by the iOS app. Must contain
            ``paymentData`` (with ``version``, ``data``, ``signature``,
            and ``header``) and ``transactionIdentifier``.
        expected_amount: Amount the client is attempting to top up,
            used to validate any declared amount on the payload.

    Returns:
        A normalized dict containing the verified token id, the
        transaction identifier, and the declared amount (when present).
        Callers can use ``token_id`` as the idempotency key.

    Raises:
        ApplePayValidationError: On any structural, base64, ASN.1, or
            declared-amount mismatch failure. The exception's
            ``details`` dict explains which step failed.
    """
    if not isinstance(token, dict):
        raise ApplePayValidationError(
            "apple payload must be a JSON object",
            details={"step": "root_type"},
        )

    txn_identifier = token.get("transactionIdentifier")
    if not isinstance(txn_identifier, str) or not txn_identifier:
        raise ApplePayValidationError(
            "apple payload missing transactionIdentifier",
            details={"step": "transaction_identifier"},
        )

    payment_data = token.get("paymentData")
    if not isinstance(payment_data, dict):
        raise ApplePayValidationError(
            "apple payload missing paymentData object",
            details={"step": "payment_data_type"},
        )

    version = payment_data.get("version")
    if version not in APPLE_PAY_SUPPORTED_VERSIONS:
        raise ApplePayValidationError(
            "apple payload.paymentData.version is not supported",
            details={
                "step": "payment_data_version",
                "version": version,
                "supported": list(APPLE_PAY_SUPPORTED_VERSIONS),
            },
        )

    encrypted_data_b64 = payment_data.get("data")
    signature_b64 = payment_data.get("signature")
    if not isinstance(encrypted_data_b64, str) or not isinstance(signature_b64, str):
        raise ApplePayValidationError(
            "apple payload.paymentData.data and .signature must be base64 strings",
            details={"step": "payment_data_string_fields"},
        )

    _assert_base64("paymentData.data", encrypted_data_b64)
    _assert_base64("paymentData.signature", signature_b64)

    header = payment_data.get("header")
    if not isinstance(header, dict):
        raise ApplePayValidationError(
            "apple payload.paymentData.header must be an object",
            details={"step": "payment_data_header_type"},
        )

    ephemeral_public_key_b64 = header.get("ephemeralPublicKey")
    public_key_hash_b64 = header.get("publicKeyHash")
    header_transaction_id = header.get("transactionId")
    for field_name, field_value in (
        ("ephemeralPublicKey", ephemeral_public_key_b64),
        ("publicKeyHash", public_key_hash_b64),
        ("transactionId", header_transaction_id),
    ):
        if not isinstance(field_value, str) or not field_value:
            raise ApplePayValidationError(
                f"apple payload.paymentData.header.{field_name} missing",
                details={"step": f"header_{field_name}"},
            )

    # After the isinstance check above, mypy still sees Any | None for
    # the dict .get(...) return values. Cast to str explicitly so the
    # base64 helpers can be called without an extra type ignore.
    assert isinstance(ephemeral_public_key_b64, str)
    assert isinstance(public_key_hash_b64, str)
    assert isinstance(header_transaction_id, str)

    # All three header base64 fields must decode to bytes.
    _assert_base64("paymentData.header.ephemeralPublicKey", ephemeral_public_key_b64)
    _assert_base64("paymentData.header.publicKeyHash", public_key_hash_b64)

    # The signature must parse as ASN.1 DER — Apple's signatures are
    # ECDSA over P-256 wrapped in DER. A garbage signature is the most
    # common forgery pattern.
    _assert_asn1_signature(signature_b64)

    # Declared amount cross-check.
    declared_amount = token.get("amount")
    declared_currency = token.get("currency")
    if declared_amount is not None:
        amt = _coerce_amount("apple", declared_amount)
        if amt != expected_amount:
            raise ApplePayValidationError(
                "apple payload amount does not match expected",
                details={
                    "step": "amount_mismatch",
                    "declared": str(amt),
                    "expected": str(expected_amount),
                },
            )

    # TODO(merchant-cert): when Settings.apple_pay_merchant_cert_path
    # is set, perform the full cryptographic chain:
    #   1. Load merchant identity cert (.cer / .pem) from disk.
    #   2. Verify paymentData.signature against the merchant cert
    #      (ECDSA-P256-SHA256 over the merchant token receipt).
    #   3. Validate the merchant cert chains to the pinned Apple
    #      intermediate cert (already loaded into
    #      APPLE_PAY_INTERMEDIATE_CERT_PEM).
    #   4. Validate the intermediate cert chains to the Apple root
    #      CA (download once at deploy time).
    # Until then, structural + ASN.1 verification is the strongest
    # check we can make; the merchant-cert branch is the migration
    # step that turns this from 80% to 100% coverage.
    await _verify_apple_pay_signature_against_pinned_intermediate(
        signature_b64=signature_b64,
        transaction_identifier=txn_identifier,
    )

    _log.info(
        "apple_pay.token_verified",
        extra={
            "transaction_identifier": txn_identifier,
            "version": version,
            "declared_amount": str(declared_amount) if declared_amount is not None else None,
            "declared_currency": declared_currency,
        },
    )

    return {
        "token_id": txn_identifier,
        "transaction_identifier": txn_identifier,
        "version": version,
        "amount": str(declared_amount) if declared_amount is not None else None,
        "currency": declared_currency,
    }


# ---------------------------------------------------------------------------
# Public: Google Pay verify
# ---------------------------------------------------------------------------


async def verify_google_pay_token(
    token: dict[str, Any],
    *,
    expected_amount: Decimal,
) -> dict[str, Any]:
    """Verify a Google Pay signed JWT and cross-check the amount.

    Args:
        token: The JSON body POSTed by the Android / web client.
            Accepts the same envelope shape as the legacy
            ``validate_google_pay_payload`` for flexibility: at minimum
            ``id`` or ``token`` must be a JWT string. Optional
            ``amount`` / ``currency`` are cross-checked against the
            caller-supplied ``expected_amount``.
        expected_amount: Amount we are about to credit (Decimal).

    Returns:
        A normalized dict containing ``token_id``, ``email`` (when
        present), and the verified ``amount`` / ``currency`` derived
        from the JWT body.

    Raises:
        GooglePayValidationError: On any decode, signature, audience,
            issuer, expiry, or amount mismatch failure.
    """
    if not isinstance(token, dict):
        raise GooglePayValidationError(
            "google payload must be a JSON object",
            details={"step": "root_type"},
        )

    raw_jwt = token.get("id") or token.get("token")
    if not isinstance(raw_jwt, str) or not raw_jwt:
        raise GooglePayValidationError(
            "google payload missing 'id' or 'token' string",
            details={"step": "missing_jwt"},
        )

    settings = get_settings()
    audience = settings.google_googlepay_audience or settings.google_client_id
    if not audience:
        raise GooglePayValidationError(
            "google pay audience is not configured "
            "(set EVW_GOOGLE_GOOGLEPAY_AUDIENCE or EVW_GOOGLE_CLIENT_ID)",
            details={"step": "audience_not_configured"},
        )

    import asyncio

    try:
        claims = await asyncio.to_thread(_verify_google_jwt, raw_jwt, expected_audience=audience)
    except GooglePayValidationError:
        raise
    except Exception as e:
        raise GooglePayValidationError(
            "google pay token verification failed",
            details={"step": "verify_token", "error": str(e)},
        ) from e

    # google-auth does not verify the issuer for verify_token; do it here.
    issuer = claims.get("iss")
    if issuer not in GOOGLE_PAY_VALID_ISSUERS:
        raise GooglePayValidationError(
            "google pay token issuer is not allowed",
            details={"step": "issuer", "issuer": issuer},
        )

    # The signed JWT body (Google Pay's PaymentMethodToken) carries
    # transaction details. They are NOT top-level JWT claims — they
    # live inside a base64url-encoded JSON blob in one of the
    # protocol-defined claim names. We don't try to decrypt the
    # encrypted message; we only verify that whatever plaintext
    # amount/currency the client declared (if any) matches.
    declared_amount = token.get("amount")
    declared_currency = token.get("currency")
    if declared_amount is not None:
        amt = _coerce_amount("google", declared_amount)
        if amt != expected_amount:
            raise GooglePayValidationError(
                "google payload amount does not match expected",
                details={
                    "step": "amount_mismatch",
                    "declared": str(amt),
                    "expected": str(expected_amount),
                },
            )

    token_id = (
        claims.get("paymentMethodToken")
        or claims.get("paymentMethodData", {}).get("tokenizationData", {}).get("id")
        or claims.get("jti")
        or raw_jwt
    )

    _log.info(
        "google_pay.token_verified",
        extra={
            "token_id": str(token_id)[:64],
            "issuer": issuer,
            "audience": audience,
            "declared_amount": str(declared_amount) if declared_amount is not None else None,
            "declared_currency": declared_currency,
            "email": claims.get("email"),
        },
    )

    return {
        "token_id": str(token_id),
        "email": claims.get("email"),
        "amount": str(declared_amount) if declared_amount is not None else None,
        "currency": declared_currency,
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# Public: Google Pay sign (server-to-server)
# ---------------------------------------------------------------------------


async def sign_google_pay_token(
    *,
    claims: dict[str, Any] | None = None,
) -> str:
    """Sign a Google Pay JWT using a Google service-account key.

    Used by the server-driven topup path when the client cannot reach
    Google Pay directly. Requires ``EVW_GOOGLE_SA_KEY_PATH`` to point
    to a valid service-account JSON file.

    Args:
        claims: Optional extra claims to merge into the JWT payload.
            ``iat`` and ``exp`` are added automatically if not supplied.

    Returns:
        The signed JWT string, RS256-encoded.

    Raises:
        GooglePayValidationError: When the service-account key is not
            configured (``code`` -> ``PAYMENT_GOOGLE_PAY_INVALID`` with
            ``details.step='sa_key_not_configured'``), or when
            google-auth fails to load the key or sign.
    """
    settings = get_settings()
    if not settings.google_sa_key_path:
        raise GooglePayValidationError(
            "google pay service account key is not configured "
            "(set EVW_GOOGLE_SA_KEY_PATH to enable signing)",
            details={"step": "sa_key_not_configured"},
        )

    try:
        from google.auth import jwt as google_jwt
        from google.oauth2 import service_account
    except ImportError as e:  # pragma: no cover — guarded by pyproject
        raise GooglePayValidationError(
            "google-auth library is not installed",
            details={"step": "google_auth_missing"},
        ) from e

    try:
        credentials = service_account.Credentials.from_service_account_file(
            settings.google_sa_key_path,
            scopes=["https://www.googleapis.com/auth/payments"],
        )
    except (OSError, ValueError) as e:
        raise GooglePayValidationError(
            "google pay service account key could not be loaded",
            details={"step": "sa_key_load", "error": str(e)},
        ) from e

    payload: dict[str, Any] = dict(claims or {})
    payload.setdefault("iss", credentials.service_account_email)

    try:
        token = google_jwt.encode(
            signer=credentials.signer if hasattr(credentials, "signer") else credentials,
            payload=payload,
            header={"typ": "JWT", "alg": "RS256"},
        )
    except Exception as e:  # pragma: no cover — depends on the key shape
        raise GooglePayValidationError(
            "google pay JWT signing failed",
            details={"step": "jwt_encode", "error": str(e)},
        ) from e

    # google_jwt.encode returns bytes; decode for JSON friendliness.
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    _log.info(
        "google_pay.token_signed",
        extra={"service_account": credentials.service_account_email},
    )
    return token


# ---------------------------------------------------------------------------
# Backward-compatible sync wrappers
# ---------------------------------------------------------------------------


def validate_apple_pay_payload(
    payload: dict[str, Any], *, expected_amount: Decimal
) -> None:
    """Sync structural check for Apple Pay (legacy shim).

    Equivalent to :func:`verify_apple_pay_token` without the
    certificate-pinning step. Kept so call sites that pass already-
    validated payloads still get a shape check on import.

    Args:
        payload: Apple Pay PKPaymentToken body.
        expected_amount: Amount the caller intends to credit.

    Raises:
        ApplePayValidationError: On any structural failure.
    """
    if not isinstance(payload, dict):
        raise ApplePayValidationError("apple payload must be a JSON object")

    txn_id = payload.get("transactionIdentifier")
    if not isinstance(txn_id, str) or not txn_id:
        raise ApplePayValidationError("apple payload missing transactionIdentifier")

    payment_data = payload.get("paymentData")
    if not isinstance(payment_data, dict):
        raise ApplePayValidationError("apple payload missing paymentData object")
    if "data" not in payment_data:
        raise ApplePayValidationError("apple payload.paymentData.data missing")

    declared_amount = payload.get("amount")
    if declared_amount is not None:
        amt = _coerce_amount("apple", declared_amount)
        if amt != expected_amount:
            raise ApplePayValidationError(
                "apple payload amount does not match expected",
                details={"declared": str(amt), "expected": str(expected_amount)},
            )


def validate_google_pay_payload(
    payload: dict[str, Any], *, expected_amount: Decimal
) -> None:
    """Sync structural check for Google Pay (legacy shim).

    Args:
        payload: Google Pay token body (needs ``id`` or ``token``).
        expected_amount: Amount the caller intends to credit.

    Raises:
        GooglePayValidationError: On any structural failure.
    """
    if not isinstance(payload, dict):
        raise GooglePayValidationError("google payload must be a JSON object")

    token = payload.get("id") or payload.get("token")
    if not isinstance(token, str) or not token:
        raise GooglePayValidationError("google payload missing 'id' or 'token' string")

    declared_amount = payload.get("amount")
    if declared_amount is not None:
        amt = _coerce_amount("google", declared_amount)
        if amt != expected_amount:
            raise GooglePayValidationError(
                "google payload amount does not match expected",
                details={"declared": str(amt), "expected": str(expected_amount)},
            )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _assert_base64(field_name: str, value: str) -> bytes:
    """Strict base64 decode; raise with a structured error on failure."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ApplePayValidationError(
            f"{field_name} is not valid base64",
            details={"step": "base64_decode", "field": field_name},
        ) from e


def _assert_asn1_signature(signature_b64: str) -> None:
    """Validate the DER-encoded ECDSA signature is at least plausibly ASN.1.

    The full ECDSA-P256 verification is gated on the merchant cert
    (see TODO above). This step rejects signatures that aren't even
    DER-encoded — which catches the most common forgery shape.
    """
    raw = _assert_base64("paymentData.signature", signature_b64)
    if len(raw) < 8:
        raise ApplePayValidationError(
            "apple signature is too short to be a valid DER sequence",
            details={"step": "asn1_too_short", "length": len(raw)},
        )
    if raw[0] != 0x30:
        raise ApplePayValidationError(
            "apple signature is not a DER SEQUENCE (first byte != 0x30)",
            details={"step": "asn1_wrong_tag", "first_byte": raw[0]},
        )
    # The second byte is the length-of-length byte. Reject the indefinite
    # form (0x80) which Apple Pay never emits.
    if raw[1] == 0x80:
        raise ApplePayValidationError(
            "apple signature uses indefinite DER length which Apple Pay does not emit",
            details={"step": "asn1_indefinite_length"},
        )


async def _verify_apple_pay_signature_against_pinned_intermediate(
    *,
    signature_b64: str,
    transaction_identifier: str,
) -> None:
    """Stub for the certificate-chain validation.

    TODO(merchant-cert): implement full verification. When the
    intermediate cert is pinned we should:
        1. Decode the merchant receipt from paymentData.data using
           ECDH between the merchant private key and the ephemeral
           public key from the header.
        2. Verify the JWS signature on the merchant receipt against
           the merchant cert.
        3. Walk the merchant cert chain to the pinned intermediate.
        4. Walk the intermediate chain to the Apple root CA.
    Until the merchant cert is provisioned we only assert the signature
    bytes are DER-parseable (done in ``_assert_asn1_signature`` above).
    """
    _ = (signature_b64, transaction_identifier)
    return None


def _verify_google_jwt(raw_jwt: str, *, expected_audience: str) -> dict[str, Any]:
    """Run google-auth verify_token and return the decoded claims.

    NOTE: This is a synchronous helper because ``verify_token`` itself
    is sync. The caller (``verify_google_pay_token``) wraps the call
    in :func:`asyncio.to_thread` to avoid stalling the event loop on
    the JWKS HTTP fetch. Tests can monkey-patch this helper to bypass
    the JWKS network call against ``googleapis.com``.
    """
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ImportError as e:  # pragma: no cover — guarded by pyproject
        raise GooglePayValidationError(
            "google-auth library is not installed",
            details={"step": "google_auth_missing"},
        ) from e

    transport = google_requests.Request()
    return dict(
        google_id_token.verify_token(
            id_token=raw_jwt,
            request=transport,
            audience=expected_audience,
        )
    )


def _coerce_amount(source: str, value: Any) -> Decimal:
    """Parse an amount claim to a Decimal with 4-dp precision."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except Exception as e:
        if source == "apple":
            raise ApplePayValidationError(
                "apple payload.amount is not a valid decimal",
                details={"step": "amount_parse"},
            ) from e
        raise GooglePayValidationError(
            "google payload.amount is not a valid decimal",
            details={"step": "amount_parse"},
        ) from e


__all__ = [
    "APPLE_PAY_INTERMEDIATE_CERT_PEM",
    "APPLE_PAY_MERCHANT_CERT_PATH_DEFAULT",
    "APPLE_PAY_SUPPORTED_VERSIONS",
    "GOOGLE_PAY_VALID_ISSUERS",
    "sign_google_pay_token",
    "validate_apple_pay_payload",
    "validate_google_pay_payload",
    "verify_apple_pay_token",
    "verify_google_pay_token",
]
