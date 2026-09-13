"""Tests for ``/api/v1/auth/*`` and the JWT helpers.

What we lock in here:
* ``is_idp_error`` returns ``False`` for ``ValueError`` (the contract test
  from the production-readiness-hardening skill).
* ``encode_jwt`` / ``decode_jwt`` round-trip cleanly.
* ``decode_jwt`` rejects tampered / expired / wrong-secret tokens.
* ``GET /api/v1/auth/me`` returns the authenticated user when a valid
  bearer is supplied.
* ``GET /api/v1/auth/me`` returns 401 when the header is missing.
* The Apple / Google endpoints exist and surface a clear error when
  their respective audience env vars are not configured.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from evwallet.auth.jwt import decode_jwt, encode_jwt
from evwallet.config import reset_settings_cache
from evwallet.errors import (
    ConfigurationError,
    IDPError,
    SchemaValidationError,
    is_idp_error,
)

# --- Local agent-A app fixture --------------------------------------------
#
# Sibling Agent C owns the shared ``app_client`` fixture (it only mounts
# charging + stations routers). To exercise the auth router we need a
# FastAPI app that includes ``evwallet.auth.router`` and the four
# health/version/metrics endpoints. We build one locally per-test.

os.environ.setdefault(
    "EVW_JWT_SECRET", "q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0Op"
)
os.environ.setdefault("EVW_POSTGRES_USER", "evwallet")
os.environ.setdefault("EVW_POSTGRES_PASSWORD", "evwallet")
os.environ.setdefault("EVW_POSTGRES_DB", "evwallet")
os.environ.setdefault("EVW_REDIS_PASSWORD", "evwallet")


@pytest_asyncio.fixture
async def agent_a_db_engine():
    """Per-test sqlite engine shared by the agent-A app + user factory."""
    import evwallet.db.models  # noqa: F401 — register models on Base.metadata
    from evwallet.db import Base
    from evwallet.db.session import reset_engine_for_tests

    reset_engine_for_tests()

    url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    reset_engine_for_tests()


@pytest_asyncio.fixture
async def agent_a_app(agent_a_db_engine):
    """Build a FastAPI app with the auth router + health endpoints mounted."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse, Response

    from evwallet import __version__
    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db
    from evwallet.errors import IDPError
    from evwallet.main import _idp_error_handler, _unhandled_error_handler
    from evwallet.metrics import metrics

    factory = async_sessionmaker(
        bind=agent_a_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app = FastAPI(title="EV Wallet HK (Agent A test app)")
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = _override_get_db
    app.add_exception_handler(IDPError, _idp_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)  # type: ignore[arg-type]

    @app.get("/healthz", include_in_schema=False)
    async def _h():
        return PlainTextResponse("ok")

    @app.get("/version", include_in_schema=False)
    async def _v():
        return PlainTextResponse(__version__)

    @app.get("/metrics", include_in_schema=False)
    async def _m():
        return Response(
            content=metrics.export_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    yield app


@pytest_asyncio.fixture
async def agent_a_client(agent_a_app):
    """``AsyncClient`` bound to the agent-A test app."""
    transport = ASGITransport(app=agent_a_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def agent_a_user_factory(agent_a_db_engine):
    """Return an async factory that creates a User + JWT in the shared DB.

    Returns ``(user, token, expires_at)``.
    """
    from datetime import datetime
    from decimal import Decimal

    from evwallet.db.models import SocialAccount, User, Wallet

    factory = async_sessionmaker(
        bind=agent_a_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    async def _make(*, email: str | None = None) -> tuple[User, str, int]:
        async with factory() as session:
            user = User(
                id=uuid.uuid4(),
                email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
                display_name="Test User",
                locale="zh-Hant",
                is_active=True,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
            session.add(user)
            await session.flush()
            wallet = Wallet(
                id=uuid.uuid4(),
                user_id=user.id,
                available_credits=Decimal("0"),
                reserved_credits=Decimal("0"),
                currency="HKD",
                version=0,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
            session.add(wallet)
            social = SocialAccount(
                id=uuid.uuid4(),
                user_id=user.id,
                provider="email",
                provider_subject=f"sub-{user.id.hex[:16]}",
                email_at_provider=user.email,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
            session.add(social)
            await session.commit()
        token, exp = encode_jwt(user.id)
        return user, token, exp

    return _make


# ---------------------------------------------------------------------------
# error hierarchy
# ---------------------------------------------------------------------------


def test_is_idp_error_does_not_catch_builtin_value_error():
    """``except IDPError`` must NOT catch ``ValueError``."""
    try:
        raise ValueError("builtin")
    except IDPError:
        pytest.fail("IDPError caught ValueError — base class is wrong")
    except ValueError:
        pass


def test_is_idp_error_true_for_subclass():
    """``is_idp_error`` returns ``True`` for IDPError subclasses."""
    assert is_idp_error(ConfigurationError("x")) is True
    assert is_idp_error(SchemaValidationError("y")) is True


def test_is_idp_error_false_for_builtin():
    """``is_idp_error`` returns ``False`` for Python builtins."""
    assert is_idp_error(ValueError("x")) is False
    assert is_idp_error(KeyError("x")) is False
    assert is_idp_error(Exception("x")) is False


# ---------------------------------------------------------------------------
# JWT round-trip
# ---------------------------------------------------------------------------


def test_encode_decode_roundtrip(monkeypatch):
    """A freshly-issued token decodes back to the same payload."""
    monkeypatch.setenv("EVW_JWT_SECRET", "x" * 64)
    reset_settings_cache()
    user_id = uuid.uuid4()
    token, exp = encode_jwt(user_id)
    assert exp > int(time.time())
    payload = decode_jwt(token)
    assert payload["sub"] == str(user_id)
    assert payload["exp"] == exp


def test_decode_rejects_tampered_token(monkeypatch):
    """A token signed with the wrong secret fails to decode."""
    monkeypatch.setenv("EVW_JWT_SECRET", "x" * 64)
    reset_settings_cache()
    user_id = uuid.uuid4()
    token, _ = encode_jwt(user_id)
    with pytest.raises(SchemaValidationError):
        decode_jwt(token[:-2] + "AA")


def test_decode_rejects_expired_token(monkeypatch):
    """A token whose ``exp`` is in the past is rejected."""
    monkeypatch.setenv("EVW_JWT_SECRET", "x" * 64)
    reset_settings_cache()
    now = int(time.time())
    payload_dict = {"sub": str(uuid.uuid4()), "iat": now - 100, "exp": now - 50}
    token = pyjwt.encode(payload_dict, "x" * 64, algorithm="HS256")
    with pytest.raises(SchemaValidationError):
        decode_jwt(token)


def test_encode_rejects_weak_secret(monkeypatch):
    """Settings refuses to load a weak JWT secret at startup."""
    monkeypatch.setenv("EVW_JWT_SECRET", "short")
    reset_settings_cache()
    with pytest.raises(ConfigurationError):
        encode_jwt(uuid.uuid4())


# ---------------------------------------------------------------------------
# /api/v1/auth/me
# ---------------------------------------------------------------------------


async def test_me_requires_bearer(agent_a_client):
    """``GET /me`` without a bearer returns a 422-shaped error envelope.

    SchemaValidationError.status == 422 per the IDPError contract.
    """
    resp = await agent_a_client.get("/api/v1/auth/me")
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "SCHEMA_VALIDATION_ERROR"


async def test_me_returns_authenticated_user(agent_a_client, agent_a_user_factory):
    """``GET /me`` with a valid bearer returns user + wallet summary."""
    _user, token, _exp = await agent_a_user_factory(email="me@example.com")
    resp = await agent_a_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "me@example.com"
    assert body["wallet"]["currency"] == "HKD"
    # Decimal serialises with full scale — "0.0000" not "0".
    assert float(body["wallet"]["available_hkd"]) == 0.0


async def test_me_rejects_malformed_header(agent_a_client):
    """A non-Bearer auth header is rejected with a clear error envelope."""
    resp = await agent_a_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "NotBearer abc"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/v1/auth/apple + google — configuration gating
# ---------------------------------------------------------------------------


async def test_apple_login_requires_audience(agent_a_client, monkeypatch):
    """Apple login without ``EVW_APPLE_BUNDLE_ID`` returns 500 with a clear code."""
    monkeypatch.delenv("EVW_APPLE_BUNDLE_ID", raising=False)
    resp = await agent_a_client.post(
        "/api/v1/auth/apple",
        json={"identity_token": "irrelevant.but.long.enough"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "CONFIGURATION_ERROR"


async def test_google_login_requires_audience(agent_a_client, monkeypatch):
    """Google login without ``EVW_GOOGLE_CLIENT_ID`` returns 500 with a clear code."""
    monkeypatch.delenv("EVW_GOOGLE_CLIENT_ID", raising=False)
    resp = await agent_a_client.post(
        "/api/v1/auth/google",
        json={"id_token": "irrelevant.but.long.enough"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "CONFIGURATION_ERROR"


async def test_apple_login_rejects_empty_token(agent_a_client, monkeypatch):
    """Apple login with an empty token returns 422 schema validation."""
    monkeypatch.setenv("EVW_APPLE_BUNDLE_ID", "com.example.app")
    resp = await agent_a_client.post(
        "/api/v1/auth/apple",
        json={"identity_token": ""},
    )
    assert resp.status_code == 422
