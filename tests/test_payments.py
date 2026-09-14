"""Tests for ``evwallet.payments.apple_google``.

Covers:
    * Apple Pay PKPaymentToken structural verification (base64 fields,
      ASN.1 signature, declared amount cross-check).
    * Google Pay signed JWT verification (audience, issuer, expiry,
      declared amount) using a real test RSA keypair minted in a
      pytest fixture.
    * Topup integration — patching the verifier and asserting it was
      called with the right amount, and that a verifier failure
      surfaces as the expected IDPError.
"""

from __future__ import annotations

import base64
import time
import uuid
from decimal import Decimal
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.config import get_settings
from evwallet.errors import ApplePayValidationError, GooglePayValidationError
from evwallet.payments import apple_google
from evwallet.payments.apple_google import (
    APPLE_PAY_SUPPORTED_VERSIONS,
    GOOGLE_PAY_VALID_ISSUERS,
    sign_google_pay_token,
    verify_apple_pay_token,
    verify_google_pay_token,
)
from evwallet.wallet import topup
from evwallet.wallet.topup import topup_apple_pay, topup_google_pay

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOOGLE_AUDIENCE = "test-google-pay-merchant"
GOOGLE_PAY_AUDIENCE_ENV = "EVW_GOOGLE_GOOGLEPAY_AUDIENCE"
GOOGLE_CLIENT_ID_ENV = "EVW_GOOGLE_CLIENT_ID"
GOOGLE_SA_KEY_PATH_ENV = "EVW_GOOGLE_SA_KEY_PATH"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _valid_asn1_signature() -> str:
    """A minimal DER SEQUENCE that passes our ASN.1 shape check."""
    return _b64(bytes.fromhex("3006020101020101"))


def _valid_ephemeral_public_key() -> str:
    # 65 bytes is the uncompressed P-256 public key length. Random
    # bytes are fine for the structural check.
    return _b64(b"\x04" + b"\x00" * 64)


def _valid_public_key_hash() -> str:
    # 32 bytes is the SHA-256 output length.
    return _b64(b"\xab" * 32)


def _make_apple_payload(
    *,
    transaction_identifier: str = "txn-abc-123",
    version: str = "EC_v1",
    data_b64: str | None = None,
    signature_b64: str | None = None,
    ephemeral: str | None = None,
    public_key_hash: str | None = None,
    header_txn_id: str = "header-txn-id-xyz",
    declared_amount: Any = None,
    declared_currency: str | None = None,
    omit_payment_data: bool = False,
) -> dict[str, Any]:
    """Build a structurally-valid Apple Pay PKPaymentToken.

    Each optional override lets a test break one specific field without
    rebuilding the whole blob.
    """
    payload: dict[str, Any] = {
        "transactionIdentifier": transaction_identifier,
    }
    if declared_amount is not None:
        payload["amount"] = declared_amount
    if declared_currency is not None:
        payload["currency"] = declared_currency
    if omit_payment_data:
        return payload
    payload["paymentData"] = {
        "version": version,
        "data": data_b64 if data_b64 is not None else _b64(b"encrypted-blob"),
        "signature": signature_b64 if signature_b64 is not None else _valid_asn1_signature(),
        "header": {
            "transactionId": header_txn_id,
            "ephemeralPublicKey": ephemeral if ephemeral is not None else _valid_ephemeral_public_key(),
            "publicKeyHash": public_key_hash if public_key_hash is not None else _valid_public_key_hash(),
        },
    }
    return payload


# ---------------------------------------------------------------------------
# RSA keypair fixture (function scope)
# ---------------------------------------------------------------------------


@pytest.fixture
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, bytes]:
    """Mint a fresh RSA keypair for Google Pay JWT tests.

    Returns ``(private_key, public_pem_bytes)``.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_pem


@pytest.fixture
def rsa_pem(rsa_keypair) -> bytes:
    """PEM-encoded public key for JWKS-shaped tests."""
    _priv, public_pem = rsa_keypair
    return public_pem


# ---------------------------------------------------------------------------
# Audience fixture (so verify_google_pay_token has something to check)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _google_pay_audience(monkeypatch):
    """Pin the Google Pay audience for every test in this module."""
    monkeypatch.setenv(GOOGLE_PAY_AUDIENCE_ENV, GOOGLE_AUDIENCE)
    get_settings.cache_clear()  # test-only cache reset
    try:
        yield GOOGLE_AUDIENCE
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1) Apple Pay: structural
# ---------------------------------------------------------------------------


async def test_apple_pay_validates_structure():
    """A well-formed PKPaymentToken with a DER signature passes."""
    payload = _make_apple_payload(declared_amount="100.00", declared_currency="HKD")
    result = await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert result["token_id"] == "txn-abc-123"
    assert result["version"] in APPLE_PAY_SUPPORTED_VERSIONS


async def test_apple_pay_accepts_token_without_declared_amount():
    """No `amount` field on the payload is fine — we just skip the check."""
    payload = _make_apple_payload()
    result = await verify_apple_pay_token(payload, expected_amount=Decimal("50.00"))
    assert result["token_id"] == "txn-abc-123"
    assert result["amount"] is None


async def test_apple_pay_rejects_missing_payment_data():
    payload = _make_apple_payload(omit_payment_data=True)
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "payment_data_type"


async def test_apple_pay_rejects_missing_transaction_identifier():
    payload = _make_apple_payload(transaction_identifier="")
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "transaction_identifier"


async def test_apple_pay_rejects_unsupported_version():
    payload = _make_apple_payload(version="EC_v2_bogus")
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "payment_data_version"


async def test_apple_pay_rejects_malformed_base64():
    payload = _make_apple_payload(data_b64="not!!!valid!!!base64@@@")
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "base64_decode"
    assert exc.value.details.get("field") == "paymentData.data"


async def test_apple_pay_rejects_wrong_signature_format():
    """Non-DER signature (first byte != 0x30) is rejected."""
    payload = _make_apple_payload(signature_b64=_b64(b"\xff\xff\xff\xff\xff\xff\xff\xff"))
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "asn1_wrong_tag"


async def test_apple_pay_rejects_signature_too_short():
    payload = _make_apple_payload(signature_b64=_b64(b"\x30\x02\x01\x01"))
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "asn1_too_short"


async def test_apple_pay_rejects_indefinite_length_signature():
    payload = _make_apple_payload(signature_b64=_b64(bytes.fromhex("30800201010201010000")))
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "asn1_indefinite_length"


async def test_apple_pay_rejects_malformed_ephemeral_key_base64():
    payload = _make_apple_payload(ephemeral="$$$ not base64 $$$")
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("field") == "paymentData.header.ephemeralPublicKey"


async def test_apple_pay_rejects_missing_header_field():
    payload = _make_apple_payload(header_txn_id="")  # empty → missing
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("100.00"))
    assert exc.value.details.get("step") == "header_transactionId"


async def test_apple_pay_rejects_amount_mismatch():
    payload = _make_apple_payload(declared_amount="100.00", declared_currency="HKD")
    with pytest.raises(ApplePayValidationError) as exc:
        await verify_apple_pay_token(payload, expected_amount=Decimal("250.00"))
    assert exc.value.details.get("step") == "amount_mismatch"
    # Decimal("100.00") gets quantized to 4dp -> "100.0000".
    assert exc.value.details.get("declared") == "100.0000"
    assert exc.value.details.get("expected") == "250.00"


async def test_apple_pay_rejects_non_object_payload():
    with pytest.raises(ApplePayValidationError):
        await verify_apple_pay_token("not a dict", expected_amount=Decimal("100.00"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2) Google Pay: signed JWT
# ---------------------------------------------------------------------------


def _make_signed_google_jwt(
    rsa_priv: rsa.RSAPrivateKey,
    *,
    audience: str = GOOGLE_AUDIENCE,
    issuer: str = "accounts.google.com",
    email: str | None = "[email protected]",
    extra_claims: dict[str, Any] | None = None,
    exp_offset_seconds: int = 600,
    iat_offset_seconds: int = 0,
) -> str:
    """Build a JWT signed with the test RSA key — same shape as Google Pay."""
    now = int(time.time()) + iat_offset_seconds
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset_seconds,
    }
    if email is not None:
        payload["email"] = email
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, rsa_priv, algorithm="RS256")


@pytest.fixture
def patch_google_jwks(monkeypatch, rsa_pem):
    """Replace google-auth's certs fetch with a one-key JWKS.

    Strategy: bypass the actual ``verify_token`` JWKS round-trip by
    monkey-patching it with a direct ``jwt.decode`` against the PEM
    the fixture minted. The fixture stays offline and deterministic.
    """
    from google.oauth2 import id_token as google_id_token

    def _bypass_verify_token(id_token, request, audience=None, **_kw):  # type: ignore[no-untyped-def]
        return dict(
            jwt.decode(
                id_token,
                key=rsa_pem,
                algorithms=["RS256"],
                audience=audience,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        )

    monkeypatch.setattr(google_id_token, "verify_token", _bypass_verify_token)
    return rsa_pem


async def test_google_pay_accepts_valid_jwt(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(
        priv,
        email="[email protected]",
        extra_claims={"paymentMethodToken": "gpay-tx-001"},
    )
    payload = {"token": raw_jwt, "amount": "200.00", "currency": "HKD"}
    result = await verify_google_pay_token(payload, expected_amount=Decimal("200.00"))
    assert result["token_id"] == "gpay-tx-001"
    assert result["email"] == "[email protected]"


async def test_google_pay_accepts_jwt_without_declared_amount(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(priv)
    result = await verify_google_pay_token({"token": raw_jwt}, expected_amount=Decimal("50"))
    assert result["amount"] is None


async def test_google_pay_rejects_wrong_audience(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(priv, audience="some-other-merchant")
    with pytest.raises(GooglePayValidationError):
        await verify_google_pay_token(
            {"token": raw_jwt, "amount": "50.00", "currency": "HKD"},
            expected_amount=Decimal("50.00"),
        )


@pytest.mark.parametrize("issuer", ["google.com", "accounts.google.com"])
async def test_google_pay_accepts_known_issuers(rsa_keypair, patch_google_jwks, issuer):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(priv, issuer=issuer)
    payload = {"token": raw_jwt, "amount": "10.00", "currency": "HKD"}
    result = await verify_google_pay_token(payload, expected_amount=Decimal("10.00"))
    assert result["claims"]["iss"] == issuer
    assert issuer in GOOGLE_PAY_VALID_ISSUERS


async def test_google_pay_rejects_wrong_issuer(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(priv, issuer="evil.example.com")
    with pytest.raises(GooglePayValidationError) as exc:
        await verify_google_pay_token(
            {"token": raw_jwt, "amount": "10.00", "currency": "HKD"},
            expected_amount=Decimal("10.00"),
        )
    # Audience passes (we have the right aud) but issuer is bad →
    # verify_token succeeds but our iss check rejects.
    assert exc.value.details.get("step") in {"issuer", "verify_token"}


async def test_google_pay_rejects_expired_token(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    # Issued 2 hours ago, expires 1 hour ago.
    raw_jwt = _make_signed_google_jwt(
        priv,
        iat_offset_seconds=-7200,
        exp_offset_seconds=-3600,
    )
    with pytest.raises(GooglePayValidationError):
        await verify_google_pay_token(
            {"token": raw_jwt, "amount": "10.00", "currency": "HKD"},
            expected_amount=Decimal("10.00"),
        )


async def test_google_pay_rejects_missing_jwt():
    with pytest.raises(GooglePayValidationError) as exc:
        await verify_google_pay_token({}, expected_amount=Decimal("10.00"))
    assert exc.value.details.get("step") == "missing_jwt"


async def test_google_pay_rejects_garbage_jwt(patch_google_jwks):
    with pytest.raises(GooglePayValidationError):
        await verify_google_pay_token(
            {"token": "not.a.jwt"}, expected_amount=Decimal("10.00")
        )


async def test_google_pay_rejects_amount_mismatch(rsa_keypair, patch_google_jwks):
    priv, _pub = rsa_keypair
    raw_jwt = _make_signed_google_jwt(priv)
    with pytest.raises(GooglePayValidationError) as exc:
        await verify_google_pay_token(
            {"token": raw_jwt, "amount": "100.00", "currency": "HKD"},
            expected_amount=Decimal("250.00"),
        )
    assert exc.value.details.get("step") == "amount_mismatch"


async def test_google_pay_rejects_unconfigured_audience(monkeypatch):
    # Clear both candidate audience settings.
    monkeypatch.delenv(GOOGLE_PAY_AUDIENCE_ENV, raising=False)
    monkeypatch.delenv(GOOGLE_CLIENT_ID_ENV, raising=False)
    get_settings.cache_clear()

    with pytest.raises(GooglePayValidationError) as exc:
        await verify_google_pay_token(
            {"token": "irrelevant"}, expected_amount=Decimal("10.00")
        )
    assert exc.value.details.get("step") == "audience_not_configured"


# ---------------------------------------------------------------------------
# 3) sign_google_pay_token
# ---------------------------------------------------------------------------


async def test_sign_google_pay_token_requires_sa_key(monkeypatch):
    monkeypatch.setattr(
        "evwallet.config.Settings.google_sa_key_path", None, raising=False
    )
    get_settings.cache_clear()
    with pytest.raises(GooglePayValidationError) as exc:
        await sign_google_pay_token()
    assert exc.value.details.get("step") == "sa_key_not_configured"


# ---------------------------------------------------------------------------
# 4) Topup integration — patch the verifier, assert wiring
# ---------------------------------------------------------------------------


async def _make_wallet(db_session: AsyncSession):
    """Insert a User + Wallet for topup tests."""
    from evwallet.db.models import User, Wallet

    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4().hex[:8]}@test.local",
        display_name="Apple Pay Test",
        is_active=True,
    )
    db_session.add(user)
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        available_credits=Decimal("0"),
        reserved_credits=Decimal("0"),
        currency="HKD",
        version=0,
    )
    db_session.add(wallet)
    await db_session.flush()
    return user, wallet


async def test_apple_pay_topup_calls_verifier(db_session, monkeypatch):
    _user, wallet = await _make_wallet(db_session)

    calls: list[dict[str, Any]] = []

    async def _fake_verify(token, *, expected_amount):  # type: ignore[no-untyped-def]
        calls.append({"token": token, "expected_amount": expected_amount})
        return {"token_id": "txn-captured-1", "version": "EC_v1"}

    monkeypatch.setattr(
        "evwallet.wallet.topup.apple_google.verify_apple_pay_token", _fake_verify
    )

    payload = _make_apple_payload(transaction_identifier="txn-captured-1")
    txn = await topup_apple_pay(db_session, wallet.id, Decimal("77.50"), payload)

    assert len(calls) == 1
    assert calls[0]["expected_amount"] == Decimal("77.50")
    assert calls[0]["token"] is payload
    assert txn.external_ref == "apple_pay:txn-captured-1"
    assert txn.metadata_json["source"] == "apple_pay"
    assert txn.metadata_json["verified"] is True


async def test_apple_pay_topup_fails_when_verifier_fails(db_session, monkeypatch):
    _user, wallet = await _make_wallet(db_session)

    async def _fake_verify(_token, *, expected_amount):  # type: ignore[no-untyped-def]
        raise ApplePayValidationError(
            "apple signature is not a DER SEQUENCE",
            details={"step": "asn1_wrong_tag"},
        )

    monkeypatch.setattr(
        "evwallet.wallet.topup.apple_google.verify_apple_pay_token", _fake_verify
    )

    payload = _make_apple_payload()
    with pytest.raises(ApplePayValidationError) as exc:
        await topup_apple_pay(db_session, wallet.id, Decimal("77.50"), payload)
    assert exc.value.details.get("step") == "asn1_wrong_tag"
    # No ledger write occurred.
    from sqlalchemy import select

    from evwallet.db.models import LedgerEntry, WalletTransaction

    txns = (
        (await db_session.execute(select(WalletTransaction)))
        .scalars()
        .all()
    )
    assert txns == []
    rows = (
        (await db_session.execute(select(LedgerEntry))).scalars().all()
    )
    assert rows == []


async def test_google_pay_topup_calls_verifier(db_session, monkeypatch):
    _user, wallet = await _make_wallet(db_session)

    calls: list[dict[str, Any]] = []

    async def _fake_verify(token, *, expected_amount):  # type: ignore[no-untyped-def]
        calls.append({"token": token, "expected_amount": expected_amount})
        return {"token_id": "gpay-tx-9", "email": "[email protected]"}

    monkeypatch.setattr(
        "evwallet.wallet.topup.apple_google.verify_google_pay_token", _fake_verify
    )

    payload = {"token": "ignored-stub-jwt", "amount": "33.33", "currency": "HKD"}
    txn = await topup_google_pay(db_session, wallet.id, Decimal("33.33"), payload)

    assert len(calls) == 1
    assert calls[0]["expected_amount"] == Decimal("33.33")
    assert calls[0]["token"] is payload
    assert txn.external_ref == "google_pay:gpay-tx-9"
    assert txn.metadata_json["source"] == "google_pay"
    assert txn.metadata_json["email"] == "[email protected]"
    assert txn.metadata_json["verified"] is True


async def test_google_pay_topup_fails_when_verifier_fails(db_session, monkeypatch):
    _user, wallet = await _make_wallet(db_session)

    async def _fake_verify(_token, *, expected_amount):  # type: ignore[no-untyped-def]
        raise GooglePayValidationError(
            "google pay token issuer is not allowed",
            details={"step": "issuer", "issuer": "evil.example.com"},
        )

    monkeypatch.setattr(
        "evwallet.wallet.topup.apple_google.verify_google_pay_token", _fake_verify
    )

    payload = {"token": "anything"}
    with pytest.raises(GooglePayValidationError) as exc:
        await topup_google_pay(db_session, wallet.id, Decimal("33.33"), payload)
    assert exc.value.details.get("step") == "issuer"

    from sqlalchemy import select

    from evwallet.db.models import LedgerEntry, WalletTransaction

    txns = (
        (await db_session.execute(select(WalletTransaction))).scalars().all()
    )
    assert txns == []
    rows = (
        (await db_session.execute(select(LedgerEntry))).scalars().all()
    )
    assert rows == []


# ---------------------------------------------------------------------------
# 5) Backward-compat shims
# ---------------------------------------------------------------------------


def test_validate_apple_pay_payload_legacy_shim():
    payload = _make_apple_payload(declared_amount="10.00", declared_currency="HKD")
    # Legacy shim only does shape + declared-amount check.
    apple_google.validate_apple_pay_payload(payload, expected_amount=Decimal("10.00"))


def test_validate_apple_pay_payload_legacy_shim_rejects_bad_amount():
    payload = _make_apple_payload(declared_amount="5.00")
    with pytest.raises(ApplePayValidationError):
        apple_google.validate_apple_pay_payload(payload, expected_amount=Decimal("6.00"))


def test_validate_google_pay_payload_legacy_shim():
    apple_google.validate_google_pay_payload(
        {"token": "any-string-here", "amount": "5.00"},
        expected_amount=Decimal("5.00"),
    )


def test_validate_google_pay_payload_legacy_shim_rejects_missing():
    with pytest.raises(GooglePayValidationError):
        apple_google.validate_google_pay_payload({}, expected_amount=Decimal("1.00"))


# ---------------------------------------------------------------------------
# 6) Sanity: topup public surface still exports the same names
# ---------------------------------------------------------------------------


def test_topup_module_exports_apple_and_google():
    assert callable(topup_apple_pay)
    assert callable(topup_google_pay)
    assert callable(topup.topup_stripe)
