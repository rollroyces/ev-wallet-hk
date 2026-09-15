"""Tests for email verification (issue + redeem + topup gate).

We use the conftest's ``db_session`` fixture everywhere — it has the
SQLite compatibility shims (gen_random_uuid, BigInteger→Integer)
already registered on its engine. Building our own engine from
``test_db_url`` skips those shims and produces errors.

Why no per-test Engine?  Because the conftest already constructs one
per test with the right listeners attached, and reusing it keeps the
schema consistent.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from evwallet.auth.password import hash_password
from evwallet.config import get_settings
from evwallet.db.models import EmailVerification, User, Wallet
from evwallet.email.sender import reset_sender_for_tests
from evwallet.email.verification import (
    get_last_dev_code,
    issue_code,
    redeem_code,
)
from evwallet.main import create_app

# ---------------------------------------------------------------------------
# Pure service tests (no HTTP) — use db_session which has the SQLite
# compat shims (gen_random_uuid, BigInteger->Integer) registered.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_code_creates_row_and_returns_dev_code(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    """issue_code() persists a row, sends the email, and (in console-sender
    mode) returns the plaintext code via the IssuedCode dataclass."""
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    reset_sender_for_tests()

    async with db_session as db:
        user = User(
            email="verify-1@evwallet-test.com",
            display_name="verify-1",
            password_hash=hash_password("validPass123"),
        )
        db.add(user)
        await db.flush()
        db.add(Wallet(user_id=user.id))
        await db.commit()
        user_id = user.id

    async with db_session as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        issued = await issue_code(user, db, purpose="signup")
        assert issued.dev_code is not None
        assert len(issued.dev_code) == 6
        assert issued.dev_code.isdigit()

        # Row is persisted with the hash, NOT the plaintext
        row = (
            await db.execute(
                select(EmailVerification).where(
                    EmailVerification.user_id == user.id
                )
            )
        ).scalar_one()
        assert row.code_hash != issued.dev_code
        assert row.code_hash.startswith("$argon2id$")
        assert row.email_to == "verify-1@evwallet-test.com"
        assert row.purpose == "signup"


@pytest.mark.asyncio
async def test_redeem_code_correct_succeeds(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    """Redeeming the correct code sets email_verified_at and consumes the row."""
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    reset_sender_for_tests()

    async with db_session as db:
        user = User(
            email="verify-2@evwallet-test.com",
            display_name="verify-2",
            password_hash=hash_password("validPass123"),
        )
        db.add(user)
        await db.flush()
        db.add(Wallet(user_id=user.id))
        await db.commit()
        user_id = user.id

    # Issue
    async with db_session as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        issued = await issue_code(user, db, purpose="signup")
        code = issued.dev_code
        assert code is not None

    # Redeem
    async with db_session as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        ok = await redeem_code(user, code, db)
        assert ok is True
        assert user.email_verified_at is not None

        # Row is now consumed
        row = (
            await db.execute(
                select(EmailVerification).where(
                    EmailVerification.user_id == user.id
                )
            )
        ).scalar_one()
        assert row.consumed_at is not None


@pytest.mark.asyncio
async def test_redeem_code_wrong_increments_attempts(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    """5 wrong codes lock the row. Even the right code is rejected after."""
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    reset_sender_for_tests()

    async with db_session as db:
        user = User(
            email="verify-3@evwallet-test.com",
            display_name="verify-3",
            password_hash=hash_password("validPass123"),
        )
        db.add(user)
        await db.flush()
        db.add(Wallet(user_id=user.id))
        await db.commit()
        user_id = user.id

    async with db_session as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        issued = await issue_code(user, db, purpose="signup")
        right_code = issued.dev_code
        assert right_code is not None
        wrong = "000000" if right_code != "000000" else "111111"

    # 6 wrong attempts — on the 6th, the row is consumed
    for _ in range(6):
        async with db_session as db:
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            ok = await redeem_code(user, wrong, db)
            assert ok is False

    # Row should now be locked (consumed)
    async with db_session as db:
        row = (
            await db.execute(
                select(EmailVerification).where(
                    EmailVerification.user_id == user_id
                )
            )
        ).scalar_one()
        assert row.consumed_at is not None
        assert row.attempts >= 5

    # Even the right code is now rejected
    async with db_session as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        ok = await redeem_code(user, right_code, db)
        assert ok is False


# ---------------------------------------------------------------------------
# HTTP-level test: register → verify → me shows email_verified_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_issues_code_and_endpoint_roundtrip(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    """End-to-end: register, fetch the dev code, verify, /me shows verified."""
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    monkeypatch.setenv("EVW_RATE_LIMIT_REGISTER", "0")
    monkeypatch.setenv("EVW_RATE_LIMIT_VERIFY", "0")
    get_settings.cache_clear()
    reset_sender_for_tests()

    app = create_app()
    email_addr = "http-roundtrip@evwallet-test.com"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Register
        r = await ac.post(
            "/api/v1/auth/register",
            json={"email": email_addr, "password": "validPass123"},
        )
        assert r.status_code == 201, r.text
        token = r.json()["access_token"]
        assert r.json()["user"]["email_verified_at"] is None

        # Fetch the user_id from /me
        me = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        user_id = me.json()["user"]["id"]

        # Read the dev code from the in-memory store
        dev_code = get_last_dev_code(user_id)
        assert dev_code is not None and len(dev_code) == 6

        # Verify with the code
        r = await ac.post(
            "/api/v1/auth/verify-email",
            json={"code": dev_code},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["verified"] is True
        assert r.json()["email_verified_at"] is not None

        # /me now shows verified
        me = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.json()["user"]["email_verified_at"] is not None


@pytest.mark.asyncio
async def test_verify_wrong_code_returns_422(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    monkeypatch.setenv("EVW_RATE_LIMIT_REGISTER", "0")
    monkeypatch.setenv("EVW_RATE_LIMIT_VERIFY", "0")
    get_settings.cache_clear()
    reset_sender_for_tests()

    app = create_app()
    email_addr = "verify-wrong@evwallet-test.com"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.post(
            "/api/v1/auth/register",
            json={"email": email_addr, "password": "validPass123"},
        )
        assert r.status_code == 201
        token = r.json()["access_token"]

        # Wrong code
        r = await ac.post(
            "/api/v1/auth/verify-email",
            json={"code": "999999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["details"]["code"] == "VERIFY_CODE_INVALID"


@pytest.mark.asyncio
async def test_topup_blocked_until_email_verified(
    test_db_url, monkeypatch, clean_users, db_session
) -> None:
    """The wallet topup endpoint refuses unverified users with 422."""
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    monkeypatch.setenv("EVW_RATE_LIMIT_REGISTER", "0")
    get_settings.cache_clear()
    reset_sender_for_tests()

    app = create_app()
    email_addr = "topup-blocked@evwallet-test.com"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.post(
            "/api/v1/auth/register",
            json={"email": email_addr, "password": "validPass123"},
        )
        assert r.status_code == 201
        token = r.json()["access_token"]

        # Try to topup — should be blocked
        r = await ac.post(
            "/api/v1/wallet/topup",
            json={
                "amount_hkd": "100.00",
                "source": "stripe",
                "source_payload": {"payment_intent_id": "pi_test"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["details"]["code"] == "EMAIL_NOT_VERIFIED"
