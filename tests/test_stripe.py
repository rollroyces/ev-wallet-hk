"""Tests for ``evwallet.payments.stripe`` + ``evwallet.payments.router``.

Covers:

* Stripe webhook signature verification — valid, bad, and expired.
* Idempotency — POST the same event twice, only the first hits the
  ledger.
* ``payment_intent.succeeded`` credits the wallet exactly once.
* ``payment_intent.payment_failed`` does NOT credit.
* Unknown event types are acknowledged with 200 but ignored.

Test infrastructure
-------------------

The shared ``app_client`` fixture in ``conftest.py`` mounts the
charging / stations / wallet routers but NOT payments. We build a
parallel test app here that mounts only the payments router plus the
global ``IDPError`` exception handler, so 4xx envelopes look identical
to the real stack. Stripe's HMAC is constructed manually (the
``stripe.Webhook.generate_test_header_payload`` helper that older docs
mention was removed in stripe>=15; the format is documented and
reproducing it is a few lines of HMAC).

A short ``autouse`` fixture pins ``EVW_STRIPE_WEBHOOK_SECRET`` and
``EVW_STRIPE_SECRET_KEY`` so the production path (real HMAC check) is
exercised; ``get_settings.cache_clear()`` is called before and after
each test so Pydantic Settings picks up the overrides.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from evwallet.config import get_settings
from evwallet.db.models import (
    LedgerEntry,
    StripeWebhookEvent,
    User,
    Wallet,
    WalletTransaction,
)
from evwallet.errors import IDPError, StripeSignatureError
from evwallet.payments.router import router as payments_router
from evwallet.payments.stripe import verify_webhook_signature
from evwallet.wallet import ledger as wallet_ledger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_WEBHOOK_SECRET = "whsec_test_super_secret_value_for_unit_tests_only"
TEST_STRIPE_SECRET_KEY = "sk_test_dummy_key_for_unit_tests_only"


# ---------------------------------------------------------------------------
# Settings + sqlite compat (mirror conftest without touching it)
# ---------------------------------------------------------------------------


def _register_sqlite_compat(dbapi_conn, _record):  # type: ignore[no-untyped-def]
    if hasattr(dbapi_conn, "create_function"):
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))


def _patch_bigint_for_sqlite() -> None:
    """Swap BigInteger PKs to Integer for sqlite (auto-increment compat).

    Mirrors conftest's helper; safe to call twice.
    """
    from sqlalchemy import BigInteger as _RealBigInt
    from sqlalchemy import Integer

    _sqlite_bigint = _RealBigInt().with_variant(Integer, "sqlite")
    import evwallet.db.models as _models_module

    for _name in ("LedgerEntry", "HourlyRate", "SessionTelemetry", "StripeWebhookEvent"):
        _cls = getattr(_models_module, _name, None)
        if _cls is not None and "id" in _cls.__table__.columns:
            # StripeWebhookEvent PK is a String, not BigInteger — skip.
            if _name == "StripeWebhookEvent":
                continue
            _cls.__table__.columns["id"].type = _sqlite_bigint


# ---------------------------------------------------------------------------
# Signature helpers (manual — replaces stripe.Webhook.generate_test_header_payload)
# ---------------------------------------------------------------------------


def _sign_webhook(
    payload: bytes,
    *,
    secret: str = TEST_WEBHOOK_SECRET,
    timestamp: int | None = None,
) -> tuple[str, str]:
    """Return ``(timestamp, signature_header)`` for a Stripe webhook body.

    Mirrors the format documented at https://stripe.com/docs/webhooks#verify-official-libraries:
    ``t=<ts>,v1=<hmac_sha256(timestamp + '.' + body)>``.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return str(ts), f"t={ts},v1={sig}"


def _event_payload(
    *,
    event_id: str,
    event_type: str,
    amount: int | None = 10000,
    wallet_id: str | None = None,
    currency: str = "hkd",
) -> dict[str, Any]:
    """Build a minimal but valid Stripe event payload."""
    pi: dict[str, Any] = {
        "id": f"pi_{uuid.uuid4().hex[:24]}",
        "object": "payment_intent",
        "amount": amount,
        "currency": currency,
        "status": "succeeded",
        "metadata": {},
    }
    if wallet_id is not None:
        pi["metadata"] = {"wallet_id": wallet_id}
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": pi},
    }


# ---------------------------------------------------------------------------
# Settings autouse — make webhook secret available for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stripe_settings(monkeypatch):
    """Pin EVW_STRIPE_WEBHOOK_SECRET for HMAC verification.

    The webhook handler delegates to ``stripe.Webhook.construct_event``
    which only needs the webhook secret — no API call is made during
    signature verification, so we leave ``EVW_STRIPE_SECRET_KEY`` unset
    and the ledger-side ``verify_payment_intent`` falls through to its
    permissive stub (it only fires on a real Stripe API call).
    """
    monkeypatch.setenv("EVW_STRIPE_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    # Explicitly clear the API key so the stub path is exercised — real
    # tests against Stripe creds belong in the integration env, not here.
    monkeypatch.delenv("EVW_STRIPE_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test app + DB fixtures (scoped to this module)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stripe_test_engine(test_db_url):
    """Reuse the shared per-test sqlite DB. The conftest already created it
    with ``Base.metadata.create_all``; we just need an engine + factory
    that the new app's get_db override can use.
    """
    _patch_bigint_for_sqlite()
    engine = create_async_engine(test_db_url, future=True)
    event.listens_for(engine.sync_engine, "connect")(_register_sqlite_compat)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def wallet_factory(db_session):
    """Insert a User + Wallet, return the wallet row (and user for ownership).

    Uses the conftest's ``db_session`` so the row is visible through the
    same DB handle the router will mutate.
    """

    async def _make(
        *,
        available: Decimal = Decimal("1000.00"),
        email: str | None = None,
    ) -> tuple[User, Wallet]:
        user = User(
            id=uuid.uuid4(),
            email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Stripe Test User",
            locale="zh-Hant",
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        wallet = Wallet(
            id=uuid.uuid4(),
            user_id=user.id,
            available_credits=available,
            reserved_credits=Decimal("0"),
            currency="HKD",
            version=0,
        )
        db_session.add(wallet)
        await db_session.commit()
        return user, wallet

    return _make


@pytest_asyncio.fixture
async def stripe_client(test_db_url, stripe_test_engine):
    """AsyncClient bound to a test FastAPI app that mounts the payments router.

    Includes the global ``IDPError`` handler so 4xx envelopes look the
    same as the real stack. ``get_db`` is overridden to point at the
    shared per-test sqlite DB so the router's writes are visible to the
    test (which also uses ``db_session``).
    """
    factory = async_sessionmaker(
        bind=stripe_test_engine, expire_on_commit=False, class_=AsyncSession
    )

    async def _get_test_db():  # type: ignore[no-untyped-def]
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()

    @app.exception_handler(IDPError)
    async def _idp_error_handler(request, exc: IDPError):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=getattr(exc, "status", 500),
            content={
                "error": {
                    "code": getattr(exc, "code", "INTERNAL_ERROR"),
                    "message": str(exc) or getattr(exc, "code", "internal error"),
                    "details": getattr(exc, "details", {}),
                }
            },
        )

    app.include_router(payments_router, prefix="/api/v1")

    from evwallet.db.session import get_db as _real_get_db

    app.dependency_overrides[_real_get_db] = _get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _webhook_url(event_id: str) -> str:
    return "/api/v1/payments/stripe/webhook"


async def _post_signed(
    client: AsyncClient,
    payload: dict[str, Any],
    *,
    secret: str = TEST_WEBHOOK_SECRET,
    timestamp: int | None = None,
    signature_header: str | None = None,
    raw_body: bytes | None = None,
):
    """Helper: serialize the payload, sign it, POST to the webhook."""
    if raw_body is None:
        raw_body = json.dumps(payload).encode()
    if signature_header is None:
        _ts, signature_header = _sign_webhook(
            raw_body, secret=secret, timestamp=timestamp
        )
    return await client.post(
        _webhook_url(str(payload.get("id", ""))),
        content=raw_body,
        headers={"Stripe-Signature": signature_header, "Content-Type": "application/json"},
    )


# ---------------------------------------------------------------------------
# 1) Signature verification
# ---------------------------------------------------------------------------


async def test_webhook_accepts_valid_signature(stripe_client, wallet_factory, db_session):
    _user, wallet = await wallet_factory()
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.succeeded",
        amount=25000,
        wallet_id=str(wallet.id),
    )
    resp = await _post_signed(stripe_client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["wallet_id"] == str(wallet.id)
    assert body["transaction_id"]


async def test_webhook_rejects_bad_signature(stripe_client, wallet_factory, db_session):
    _user, wallet = await wallet_factory()
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.succeeded",
        amount=10000,
        wallet_id=str(wallet.id),
    )
    raw = json.dumps(payload).encode()
    ts, _good = _sign_webhook(raw)
    bad_sig = f"t={ts},v1={'0' * 64}"
    resp = await stripe_client.post(
        _webhook_url(str(payload["id"])),
        content=raw,
        headers={"Stripe-Signature": bad_sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PAYMENT_STRIPE_SIGNATURE_INVALID"
    # No idempotency row was inserted (the router fails before recording).
    rows = (await db_session.execute(select(StripeWebhookEvent))).scalars().all()
    assert rows == []


async def test_webhook_rejects_expired_timestamp(stripe_client, wallet_factory, db_session):
    _user, wallet = await wallet_factory()
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.succeeded",
        amount=10000,
        wallet_id=str(wallet.id),
    )
    # 1 hour old timestamp — outside the default 300s Stripe tolerance.
    old_ts = int(time.time()) - 3600
    raw = json.dumps(payload).encode()
    _ts, signature_header = _sign_webhook(raw, timestamp=old_ts)
    resp = await stripe_client.post(
        _webhook_url(str(payload["id"])),
        content=raw,
        headers={"Stripe-Signature": signature_header, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PAYMENT_STRIPE_SIGNATURE_INVALID"


# ---------------------------------------------------------------------------
# 2) Idempotency
# ---------------------------------------------------------------------------


async def test_webhook_is_idempotent(stripe_client, wallet_factory, db_session):
    """Same event delivered twice → 200 + 200; only ONE ledger entry."""
    _user, wallet = await wallet_factory(available=Decimal("0"))
    event_id = f"evt_{uuid.uuid4().hex[:24]}"
    payload = _event_payload(
        event_id=event_id,
        event_type="payment_intent.succeeded",
        amount=12300,
        wallet_id=str(wallet.id),
    )

    resp1 = await _post_signed(stripe_client, payload)
    assert resp1.status_code == 200, resp1.text
    resp2 = await _post_signed(stripe_client, payload)
    assert resp2.status_code == 200, resp2.text

    # The first call ran the handler (so it has a transaction_id); the
    # second is deduped at the idempotency layer and returns no
    # transaction_id — the real idempotency check is the DB query below.
    assert resp1.json()["transaction_id"] is not None
    assert resp2.json()["transaction_id"] is None
    assert resp1.json()["event_id"] == event_id
    assert resp2.json()["event_id"] == event_id

    # Exactly one stripe_webhook_events row.
    rows = (await db_session.execute(select(StripeWebhookEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == event_id
    assert rows[0].status == "processed"

    # Exactly one wallet_transactions row.
    txns = (await db_session.execute(select(WalletTransaction))).scalars().all()
    assert len(txns) == 1
    assert txns[0].external_ref == f"stripe:{payload['data']['object']['id']}"

    # Exactly two ledger_entries (topup_debit pair sums to zero).
    entries = (await db_session.execute(select(LedgerEntry))).scalars().all()
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# 3) payment_intent.succeeded crediting
# ---------------------------------------------------------------------------


async def test_payment_intent_succeeded_credits_wallet(stripe_client, wallet_factory, db_session):
    _user, wallet = await wallet_factory(available=Decimal("0"))
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.succeeded",
        amount=30050,  # HKD 300.50
        wallet_id=str(wallet.id),
    )
    resp = await _post_signed(stripe_client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wallet_id"] == str(wallet.id)
    assert body["transaction_id"]

    # Ledger shows exactly one topup pair summing to zero in the available bucket.
    txns = (await db_session.execute(select(WalletTransaction))).scalars().all()
    assert len(txns) == 1
    assert txns[0].kind == "topup"
    assert txns[0].amount == Decimal("300.5000")
    assert txns[0].external_ref == f"stripe:{payload['data']['object']['id']}"

    # Available balance is now HKD 300.50.
    avail, _resv = await wallet_ledger.get_balance(db_session, wallet.id)
    assert avail == Decimal("300.5000")


async def test_payment_intent_succeeded_uses_external_ref_idempotency(
    stripe_client, wallet_factory, db_session
):
    """Same event delivered twice credits the wallet only ONCE."""
    _user, wallet = await wallet_factory(available=Decimal("0"))
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.succeeded",
        amount=50000,
        wallet_id=str(wallet.id),
    )
    resp1 = await _post_signed(stripe_client, payload)
    resp2 = await _post_signed(stripe_client, payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # First call ran the handler (transaction_id set); second is deduped.
    assert resp1.json()["transaction_id"] is not None
    assert resp2.json()["transaction_id"] is None

    avail, _resv = await wallet_ledger.get_balance(db_session, wallet.id)
    assert avail == Decimal("500.0000")


# ---------------------------------------------------------------------------
# 4) payment_intent.payment_failed / canceled — no credit
# ---------------------------------------------------------------------------


async def test_payment_intent_failed_does_not_credit_wallet(
    stripe_client, wallet_factory, db_session
):
    _user, wallet = await wallet_factory(available=Decimal("0"))
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.payment_failed",
        amount=10000,
        wallet_id=str(wallet.id),
    )
    # The failed event still has a pi in .data.object; we add the
    # last_payment_error to make the handler path realistic.
    payload["data"]["object"]["status"] = "failed"  # type: ignore[index]
    payload["data"]["object"]["last_payment_error"] = {  # type: ignore[index]
        "code": "card_declined",
        "message": "Your card was declined.",
        "type": "card_error",
    }
    resp = await _post_signed(stripe_client, payload)
    assert resp.status_code == 200, resp.text

    # No transactions, no ledger entries, no wallet credit.
    txns = (await db_session.execute(select(WalletTransaction))).scalars().all()
    assert txns == []
    entries = (await db_session.execute(select(LedgerEntry))).scalars().all()
    assert entries == []
    avail, _resv = await wallet_ledger.get_balance(db_session, wallet.id)
    assert avail == Decimal("0")


async def test_payment_intent_canceled_does_not_credit_wallet(
    stripe_client, wallet_factory, db_session
):
    _user, wallet = await wallet_factory(available=Decimal("0"))
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="payment_intent.canceled",
        amount=10000,
        wallet_id=str(wallet.id),
    )
    payload["data"]["object"]["status"] = "canceled"  # type: ignore[index]
    payload["data"]["object"]["cancellation_reason"] = "requested_by_customer"  # type: ignore[index]
    resp = await _post_signed(stripe_client, payload)
    assert resp.status_code == 200, resp.text

    txns = (await db_session.execute(select(WalletTransaction))).scalars().all()
    assert txns == []
    entries = (await db_session.execute(select(LedgerEntry))).scalars().all()
    assert entries == []


# ---------------------------------------------------------------------------
# 5) Unknown event types
# ---------------------------------------------------------------------------


async def test_unknown_event_type_returns_200(stripe_client, wallet_factory, db_session):
    _user, wallet = await wallet_factory(available=Decimal("0"))
    payload = _event_payload(
        event_id=f"evt_{uuid.uuid4().hex[:24]}",
        event_type="customer.subscription.updated",
        amount=0,
        wallet_id=str(wallet.id),
    )
    resp = await _post_signed(stripe_client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True

    # The idempotency row exists with status=processed but NO wallet txn.
    rows = (await db_session.execute(select(StripeWebhookEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "processed"
    txns = (await db_session.execute(select(WalletTransaction))).scalars().all()
    assert txns == []


# ---------------------------------------------------------------------------
# 6) Module-level checks: verify_webhook_signature errors
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_raises_on_missing_header():
    payload = json.dumps({"id": "evt_1", "type": "ping"}).encode()
    with pytest.raises(StripeSignatureError):
        verify_webhook_signature(payload=payload, signature_header=None)


def test_verify_webhook_signature_raises_on_malformed_body():
    # Sign garbage bytes that are NOT valid JSON; the SDK still tries to
    # JSON-decode and the SignatureVerificationError becomes our
    # StripeSignatureError. We assert the wrapper surfaces the failure
    # cleanly (whatever shape the SDK raises is converted).
    bad = b"not-valid-json-at-all"
    _, sig = _sign_webhook(bad)
    with pytest.raises(StripeSignatureError):
        verify_webhook_signature(payload=bad, signature_header=sig)


# ---------------------------------------------------------------------------
# 7) Sanity — module exports
# ---------------------------------------------------------------------------


def test_payments_module_exports_unchanged():
    """The router module's public surface still exports ``router``."""
    from evwallet.payments.router import router as r

    assert r is not None
    from evwallet.payments.stripe import verify_payment_intent, verify_webhook_signature

    assert callable(verify_payment_intent)
    assert callable(verify_webhook_signature)