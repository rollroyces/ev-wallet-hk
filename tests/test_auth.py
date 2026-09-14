"""Tests for the auth module.

Covers:

* Real cryptographic verification paths for Apple Sign-In and Google
  OAuth — RSA-signed JWTs from a fixed in-process keypair plus mocked
  JWKS responses for Apple and a patched ``verify_oauth2_token`` for
  Google.
* Negative cases: garbage tokens, wrong audience, expired tokens, and
  unknown issuer.
* End-to-end router integration through FastAPI's ``AsyncClient`` so we
  exercise the upsert path that wires the verified claims into the
  ``social_accounts`` table.

The module-level RSA keypair is generated ONCE per pytest session so the
test suite stays fast — 2048-bit RSA takes ~100ms to generate on an
M-series Mac, but sign/verify is microseconds.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.oauth2 import id_token as google_id_token_module
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evwallet.auth import apple as apple_module
from evwallet.auth.apple import (
    AppleIdentityClaims,
    reset_jwks_cache,
    verify_apple_identity_token,
)
from evwallet.auth.google import (
    GoogleIdentityClaims,
    verify_google_id_token,
)
from evwallet.config import get_settings
from evwallet.errors import AuthTokenInvalid, ConfigurationError

# ---------------------------------------------------------------------------
# Module-level fixtures — generated once, reused across tests.
# ---------------------------------------------------------------------------

_TEST_KID = "test-key-1"
_TEST_APPLE_ISSUER = "https://appleid.apple.com"


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, str, dict[str, Any]]:
    """Return ``(private_key, pem_string, jwk_public_dict)``.

    The PEM is what PyJWT expects for ``jwt.encode`` and the JWK is what
    we'd serve from Apple's JWKS endpoint, so the verifier can resolve
    the ``kid`` and load the public key.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": _TEST_KID,
        "n": _b64url_uint(public_numbers.n),
        "e": _b64url_uint(public_numbers.e),
    }
    return private_key, pem, jwk


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    """Reset the in-process JWKS cache around every test for isolation."""
    reset_jwks_cache()
    yield
    reset_jwks_cache()


def _b64url_uint(value: int) -> str:
    """Base64url-encode a big integer the way JWK spec requires."""
    byte_length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


def _encode_apple_token(
    pem: str,
    *,
    audience: str,
    issuer: str = _TEST_APPLE_ISSUER,
    expires_in: int = 300,
    overrides: dict[str, Any] | None = None,
) -> str:
    """Mint a signed Apple-style JWT for tests.

    Args:
        pem: PKCS8 PEM string for the private key.
        audience: Value for the ``aud`` claim.
        issuer: Value for the ``iss`` claim (default = Apple's).
        expires_in: Seconds from now until ``exp``.
        overrides: Per-call claim overrides (use to drive failure paths).
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": f"apple-sub-{uuid.uuid4().hex[:10]}",
        "iat": now,
        "exp": now + expires_in,
        "email": "test@example.com",
        "email_verified": True,
        "is_private_email": False,
    }
    if overrides:
        payload.update(overrides)
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": _TEST_KID})


def _apple_jwks(jwk: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single JWK in the JWKS envelope Apple's endpoint returns."""
    return {"keys": [jwk]}


def _install_mock_jwks(monkeypatch: MonkeyPatch, jwk: dict[str, Any]) -> None:
    """Patch :func:`apple_module._fetch_jwks` to return a fixed JWK set."""

    async def _fake_fetch(client: httpx.AsyncClient) -> dict[str, Any]:
        return _apple_jwks(jwk)

    monkeypatch.setattr(apple_module, "_fetch_jwks", _fake_fetch)


# ---------------------------------------------------------------------------
# Apple verifier — negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apple_verifier_rejects_garbage_token(rsa_keypair) -> None:
    """An obviously malformed string is rejected with ``AuthTokenInvalid``."""
    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token("not-a-real-jwt")
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_empty_token(rsa_keypair) -> None:
    """An empty string is rejected with the ``MISSING`` code."""
    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token("")
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_wrong_audience(rsa_keypair, monkeypatch) -> None:
    """A token whose ``aud`` does not match the configured bundle id is rejected."""
    _private_key, pem, jwk = rsa_keypair
    token = _encode_apple_token(pem, audience="com.someone.else.app")
    _install_mock_jwks(monkeypatch, jwk)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token(token)
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_expired_token(rsa_keypair, monkeypatch) -> None:
    """A token whose ``exp`` is in the past is rejected with ``EXPIRED`` code."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None  # set in conftest
    # exp 60 seconds in the past, well past the leeway
    token = _encode_apple_token(pem, audience=settings.apple_bundle_id, expires_in=-120)
    _install_mock_jwks(monkeypatch, jwk)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token(token)
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_unknown_kid(rsa_keypair, monkeypatch) -> None:
    """A token whose ``kid`` is not in the JWKS is rejected."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(pem, audience=settings.apple_bundle_id)
    bogus_jwk = {**jwk, "kid": "different-kid"}
    _install_mock_jwks(monkeypatch, bogus_jwk)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token(token)
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_INVALID"
    assert excinfo.value.details.get("reason") == "unknown_kid"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_wrong_issuer(rsa_keypair, monkeypatch) -> None:
    """A token whose ``iss`` is not Apple's is rejected."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(
        pem, audience=settings.apple_bundle_id, issuer="https://evil.example.com"
    )
    _install_mock_jwks(monkeypatch, jwk)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token(token)
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_apple_verifier_rejects_nonce_mismatch(rsa_keypair, monkeypatch) -> None:
    """When an ``expected_nonce`` is supplied but the token's nonce differs, reject."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(
        pem,
        audience=settings.apple_bundle_id,
        overrides={"nonce": "token-nonce"},
    )
    _install_mock_jwks(monkeypatch, jwk)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        await verify_apple_identity_token(token, expected_nonce="different-nonce")
    assert excinfo.value.details["code"] == "AUTH_APPLE_TOKEN_INVALID"
    assert excinfo.value.details.get("reason") == "nonce"


# ---------------------------------------------------------------------------
# Apple verifier — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apple_verifier_accepts_valid_token(rsa_keypair, monkeypatch) -> None:
    """A correctly-signed, properly-aud'd token returns typed claims."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None
    expected_aud = settings.apple_bundle_id
    expected_sub = "apple-user-123"
    token = _encode_apple_token(
        pem,
        audience=expected_aud,
        overrides={
            "sub": expected_sub,
            "email": "alice@example.com",
            "email_verified": True,
            "is_private_email": False,
            "nonce": "client-supplied-nonce",
        },
    )
    _install_mock_jwks(monkeypatch, jwk)

    claims = await verify_apple_identity_token(token, expected_nonce="client-supplied-nonce")
    assert isinstance(claims, AppleIdentityClaims)
    assert claims.apple_sub == expected_sub
    assert claims.email == "alice@example.com"
    assert claims.email_verified is True
    assert claims.is_private_email is False
    assert claims.nonce == "client-supplied-nonce"


@pytest.mark.asyncio
async def test_apple_verifier_extracts_first_sign_in_name(rsa_keypair, monkeypatch) -> None:
    """Apple nests the user's name under ``name`` on the first sign-in only."""
    _private_key, pem, jwk = rsa_keypair
    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(
        pem,
        audience=settings.apple_bundle_id,
        overrides={
            "name": {"firstName": "Alice", "lastName": "Wong"},
        },
    )
    _install_mock_jwks(monkeypatch, jwk)

    claims = await verify_apple_identity_token(token)
    assert claims.full_name == "Alice Wong"


@pytest.mark.asyncio
async def test_apple_verifier_raises_when_bundle_id_unconfigured(
    rsa_keypair, monkeypatch
) -> None:
    """A missing audience surfaces as ``ConfigurationError`` at the top of the call."""
    settings = get_settings()
    monkeypatch.setattr(settings, "apple_bundle_id", None, raising=False)
    monkeypatch.setattr(settings, "apple_pay_merchant_id", None, raising=False)

    with pytest.raises(ConfigurationError):
        await verify_apple_identity_token("any-token")


# ---------------------------------------------------------------------------
# Google verifier — negative paths
# ---------------------------------------------------------------------------


def test_google_verifier_rejects_garbage_token() -> None:
    """A non-JWT string is rejected with ``AuthTokenInvalid``."""
    with pytest.raises(AuthTokenInvalid) as excinfo:
        verify_google_id_token("not-a-real-jwt")
    assert excinfo.value.details["code"] == "AUTH_GOOGLE_TOKEN_INVALID"


def test_google_verifier_rejects_empty_token() -> None:
    """An empty token is rejected with the ``MISSING`` code."""
    with pytest.raises(AuthTokenInvalid) as excinfo:
        verify_google_id_token("")
    assert excinfo.value.details["code"] == "AUTH_GOOGLE_TOKEN_MISSING"


def test_google_verifier_rejects_wrong_audience(monkeypatch) -> None:
    """A token verified by google-auth with a bad audience is mapped to ``AuthTokenInvalid``."""

    def _verify(_token, _request, *, audience):
        raise ValueError(f"Wrong recipient, expected {audience} but got someone-else")

    monkeypatch.setattr(google_id_token_module, "verify_oauth2_token", _verify)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        verify_google_id_token("any-token")
    assert excinfo.value.details["code"] == "AUTH_GOOGLE_TOKEN_INVALID"


def test_google_verifier_rejects_wrong_issuer(monkeypatch) -> None:
    """A token whose ``iss`` isn't a Google issuer is rejected even if signed properly."""

    def _verify(_token, _request, *, audience):
        return {
            "iss": "https://evil.example.com",
            "aud": audience,
            "sub": "g-123",
            "email": "alice@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(google_id_token_module, "verify_oauth2_token", _verify)

    with pytest.raises(AuthTokenInvalid) as excinfo:
        verify_google_id_token("any-token")
    assert excinfo.value.details["code"] == "AUTH_GOOGLE_TOKEN_INVALID"
    assert excinfo.value.details.get("reason") == "iss"


# ---------------------------------------------------------------------------
# Google verifier — happy path
# ---------------------------------------------------------------------------


def test_google_verifier_accepts_valid_token(monkeypatch) -> None:
    """A correctly-verified token returns typed ``GoogleIdentityClaims``."""
    settings = get_settings()
    captured: dict[str, Any] = {}

    def _verify(token, request, *, audience):
        captured["token"] = token
        captured["audience"] = audience
        # The library passes a Request object — just keep the reference so
        # callers can prove the new code uses google.auth.transport.requests.
        captured["request_type"] = type(request).__module__ + "." + type(request).__name__
        return {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "sub": "google-user-456",
            "email": "bob@example.com",
            "email_verified": True,
            "name": "Bob Lee",
            "picture": "https://example.com/avatar.png",
        }

    monkeypatch.setattr(google_id_token_module, "verify_oauth2_token", _verify)

    claims = verify_google_id_token("any-token")
    assert isinstance(claims, GoogleIdentityClaims)
    assert claims.google_sub == "google-user-456"
    assert claims.email == "bob@example.com"
    assert claims.email_verified is True
    assert claims.name == "Bob Lee"
    assert claims.picture == "https://example.com/avatar.png"
    # We must pass the configured client id as the audience.
    assert captured["audience"] == settings.google_client_id
    # We must use the official google.auth Request — not a shim.
    assert captured["request_type"] == "google.auth.transport.requests.Request"


def test_google_verifier_accepts_short_iss(monkeypatch) -> None:
    """The library accepts the un-prefixed ``accounts.google.com`` issuer."""
    monkeypatch.setattr(
        google_id_token_module,
        "verify_oauth2_token",
        lambda *_a, **_kw: {
            "iss": "accounts.google.com",
            "aud": "aud",
            "sub": "g-1",
            "email_verified": False,
        },
    )
    claims = verify_google_id_token("any-token")
    assert claims.google_sub == "g-1"


def test_google_verifier_raises_when_client_id_unconfigured(monkeypatch) -> None:
    """A missing client id surfaces as ``ConfigurationError`` at the top of the call."""
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", None, raising=False)
    monkeypatch.setattr(settings, "google_pay_merchant_id", None, raising=False)

    with pytest.raises(ConfigurationError):
        verify_google_id_token("any-token")


# ---------------------------------------------------------------------------
# End-to-end router integration via FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apple_login_endpoint_creates_user_and_session(
    rsa_keypair, test_db_url, monkeypatch
) -> None:
    """POST /api/v1/auth/apple with a valid token returns a session and creates the user."""
    _private_key, pem, jwk = rsa_keypair
    _install_mock_jwks(monkeypatch, jwk)

    from fastapi import FastAPI

    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db

    app = FastAPI()
    app.include_router(auth_router)

    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(
        pem,
        audience=settings.apple_bundle_id,
        overrides={
            "sub": "apple-e2e-sub",
            "email": "e2e@example.com",
            "email_verified": True,
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/apple",
            json={"identity_token": token, "full_name": "Alice E2E"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "e2e@example.com"
    assert body["user"]["display_name"] == "Alice E2E"
    assert body["access_token"]

    # Verify the social_account row landed.
    async with factory() as session:
        from evwallet.db.models import SocialAccount

        result = await session.execute(
            select(SocialAccount).where(
                SocialAccount.provider == "apple",
                SocialAccount.provider_subject == "apple-e2e-sub",
            )
        )
        social = result.scalar_one_or_none()
        assert social is not None
        assert social.email_at_provider == "e2e@example.com"

    await engine.dispose()


@pytest.mark.asyncio
async def test_apple_login_endpoint_rejects_invalid_token(
    test_db_url, monkeypatch
) -> None:
    """POST /api/v1/auth/apple with garbage returns 401 ``AUTH_APPLE_TOKEN_INVALID``."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db
    from evwallet.errors import IDPError

    app = FastAPI()

    @app.exception_handler(IDPError)
    async def _handler(_request, exc):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(auth_router)

    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    transport = ASGITransport(app=app)
    # Long enough to pass Pydantic's min_length=10 check; bad enough to
    # fail JWT parsing in the verifier (no dots == not a JWS).
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/apple",
            json={"identity_token": "totally-garbage-token-with-no-dots-or-signature"},
        )
    assert response.status_code == 401
    body = response.json()
    # The canonical class code lives on the error envelope; the
    # provider-specific code lives in ``details.code`` so callers can
    # distinguish Apple vs Google vs JWT-shape failures.
    assert body["error"]["code"] == "AUTH_TOKEN_INVALID"
    assert body["error"]["details"]["code"] == "AUTH_APPLE_TOKEN_INVALID"
    await engine.dispose()


@pytest.mark.asyncio
async def test_google_login_endpoint_creates_user_and_session(
    test_db_url, monkeypatch
) -> None:
    """POST /api/v1/auth/google with a valid token returns a session."""
    monkeypatch.setattr(
        google_id_token_module,
        "verify_oauth2_token",
        lambda *_a, **_kw: {
            "iss": "https://accounts.google.com",
            "aud": get_settings().google_client_id,
            "sub": "google-e2e-sub",
            "email": "g-e2e@example.com",
            "email_verified": True,
            "name": "Google E2E",
        },
    )

    from fastapi import FastAPI

    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db

    app = FastAPI()
    app.include_router(auth_router)

    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/google",
            json={"id_token": "long-enough-to-pass-pydantic-min-length-check"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "g-e2e@example.com"
    assert body["user"]["display_name"] == "Google E2E"
    assert body["access_token"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_google_login_endpoint_rejects_invalid_token(test_db_url, monkeypatch) -> None:
    """POST /api/v1/auth/google with an invalid token returns 401."""
    monkeypatch.setattr(
        google_id_token_module,
        "verify_oauth2_token",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("bad token")),
    )

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db
    from evwallet.errors import IDPError

    app = FastAPI()

    @app.exception_handler(IDPError)
    async def _handler(_request, exc):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(auth_router)

    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/google",
            json={"id_token": "long-enough-to-pass-pydantic-min-length-check"},
        )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_TOKEN_INVALID"
    assert body["error"]["details"]["code"] == "AUTH_GOOGLE_TOKEN_INVALID"
    await engine.dispose()


@pytest.mark.asyncio
async def test_apple_login_returns_existing_user_on_second_call(
    rsa_keypair, test_db_url, monkeypatch
) -> None:
    """Two sign-ins with the same Apple sub return the same user (idempotent)."""
    _private_key, pem, jwk = rsa_keypair
    _install_mock_jwks(monkeypatch, jwk)

    from fastapi import FastAPI

    from evwallet.auth.router import router as auth_router
    from evwallet.db.session import get_db

    app = FastAPI()
    app.include_router(auth_router)

    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    settings = get_settings()
    assert settings.apple_bundle_id is not None
    token = _encode_apple_token(
        pem,
        audience=settings.apple_bundle_id,
        overrides={
            "sub": "stable-apple-sub",
            "email": "stable@example.com",
            "email_verified": True,
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        r1 = await client.post("/api/v1/auth/apple", json={"identity_token": token})
        r2 = await client.post("/api/v1/auth/apple", json={"identity_token": token})
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["user"]["id"] == r2.json()["user"]["id"]
    await engine.dispose()


# Kept to preserve the original file's signature (pytest collects even
# trivial tests so the test module is a real module-level entity).
async def test_auth_placeholder() -> None:
    """Trivial placeholder preserved from the original test file."""
    assert True


# ---------------------------------------------------------------------------
# Email + password /register and /login
# ---------------------------------------------------------------------------


async def test_register_creates_user_and_returns_session(
    test_db_url, monkeypatch, clean_users
) -> None:
    """POST /auth/register with a fresh email creates the user + wallet
    and returns a session — auto-login on signup."""
    from evwallet.db.models import User, Wallet
    from evwallet.main import create_app

    # Point Settings at the per-test DB (not the live Postgres). We have
    # to set it via env var BEFORE clearing the cache, because cache_clear
    # re-reads from the environment.
    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.post(
            "/api/v1/auth/register",
            json={"email": "new-user@evwallet-test.com", "password": "validPass123"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "access_token" in body and len(body["access_token"]) > 20
    assert body["user"]["email"] == "new-user@evwallet-test.com"
    assert body["user"]["display_name"] == "new-user"

    # DB has the user + wallet
    engine = create_async_engine(get_settings().database_url or test_db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = (
            await db.execute(
                select(User).where(User.email == "new-user@evwallet-test.com")
            )
        ).scalar_one()
        assert user.password_hash is not None
        assert user.password_hash.startswith("$argon2id$")
        wallet = (
            await db.execute(select(Wallet).where(Wallet.user_id == user.id))
        ).scalar_one()
        assert wallet.available_credits == 0
    await engine.dispose()


async def test_register_rejects_duplicate_email(test_db_url, monkeypatch, clean_users) -> None:
    """Second POST with the same email returns 409 CONFLICT."""
    from evwallet.main import create_app

    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r1 = await ac.post(
            "/api/v1/auth/register",
            json={"email": "dup@evwallet-test.com", "password": "validPass123"},
        )
        assert r1.status_code == 201
        r2 = await ac.post(
            "/api/v1/auth/register",
            json={"email": "dup@evwallet-test.com", "password": "validPass456"},
        )
    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body["error"]["code"] == "CONFLICT"
    assert body["error"]["details"]["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_login_with_correct_password_succeeds(
    test_db_url, monkeypatch, clean_users
) -> None:
    """Production-mode login verifies the Argon2 hash."""
    from evwallet.main import create_app

    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        await ac.post(
            "/api/v1/auth/register",
            json={"email": "bob@evwallet-test.com", "password": "correctPass123"},
        )
        r = await ac.post(
            "/api/v1/auth/login",
            json={"email": "bob@evwallet-test.com", "password": "correctPass123"},
        )
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()


async def test_login_with_wrong_password_returns_invalid_creds(
    test_db_url, monkeypatch, clean_users
) -> None:
    """Wrong password returns 422 with AUTH_INVALID_CREDENTIALS (same
    message as unknown-email to avoid leaking which is which)."""
    from evwallet.main import create_app

    monkeypatch.setenv("EVW_DEV_LOGIN", "false")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        await ac.post(
            "/api/v1/auth/register",
            json={"email": "carol@evwallet-test.com", "password": "rightPass123"},
        )
        r = await ac.post(
            "/api/v1/auth/login",
            json={"email": "carol@evwallet-test.com", "password": "wrongPass123"},
        )
        r_unknown = await ac.post(
            "/api/v1/auth/login",
            json={"email": "nobody@nowhere.com", "password": "anyPass12345"},
        )

    # Both return the same generic error so an attacker can't enumerate users.
    assert r.status_code == r_unknown.status_code == 422
    msg1 = r.json()["error"]["details"]["code"]
    msg2 = r_unknown.json()["error"]["details"]["code"]
    assert msg1 == msg2 == "AUTH_INVALID_CREDENTIALS"


async def test_dev_login_auto_creates_user_with_password_hash(
    test_db_url, monkeypatch, clean_users
) -> None:
    """When EVW_DEV_LOGIN=true, login auto-creates the user AND sets
    a real Argon2 hash — so flipping the flag off later still works."""
    from evwallet.db.models import User
    from evwallet.main import create_app

    monkeypatch.setenv("EVW_DEV_LOGIN", "true")
    monkeypatch.setenv("EVW_DATABASE_URL", test_db_url)
    get_settings.cache_clear()

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.post(
            "/api/v1/auth/login",
            json={"email": "dev-user@evwallet-test.com", "password": "anyPass123"},
        )
    assert r.status_code == 200

    # Hash is set on the dev-created user
    engine = create_async_engine(get_settings().database_url or test_db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = (
            await db.execute(
                select(User).where(User.email == "dev-user@evwallet-test.com")
            )
        ).scalar_one()
        assert user.password_hash is not None
        assert user.password_hash.startswith("$argon2id$")
    await engine.dispose()


async def test_password_hash_round_trip() -> None:
    """hash_password + verify_password round-trip — sanity check on the
    argon2 wrapper."""
    from evwallet.auth.password import hash_password, verify_password

    h = hash_password("hello")
    assert h.startswith("$argon2id$")
    assert verify_password("hello", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("hello", None) is False
    assert verify_password("hello", "garbage") is False
