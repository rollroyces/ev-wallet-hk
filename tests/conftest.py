"""Shared test fixtures for the EV Wallet HK backend.

Consolidated from:
- Agent A's auth/health tests (basic engine + clients)
- Agent B's wallet ledger (_wallet_setup.py: ARRAY table drop, gen_random_uuid
  SQL stub, BigInteger→Integer swap)
- Agent C's charging/stations (per-test tmp file, fake_redis, app_client with
  IDPError handler, current_user override)

Strategy:
- SQLite in-memory for portability (no Docker required for `pytest -q`)
- `EVW_TEST_DATABASE_URL` overrides to real Postgres if set
- ARRAY(String) tables (Postgres-only) are removed from metadata so SQLite can
  compile the rest. Tests that need those tables run on real Postgres.

CRITICAL: env vars MUST be set BEFORE importing anything that constructs
``Settings()`` (Pydantic Settings validates eagerly at instantiation).
"""

from __future__ import annotations

import os

# ----- env (must be first) -----------------------------------------------
os.environ.setdefault("EVW_JWT_SECRET", "q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0OpQ9pXrLkMz7NcV")
os.environ.setdefault("EVW_POSTGRES_USER", "evwallet")
os.environ.setdefault("EVW_POSTGRES_PASSWORD", "evwallet")
os.environ.setdefault("EVW_POSTGRES_DB", "evwallet")
os.environ.setdefault("EVW_REDIS_PASSWORD", "evwallet")
os.environ.setdefault("EVW_ENV", "test")
os.environ.setdefault("EVW_PREAUTH_MAX_HKD", "500.00")
os.environ.setdefault("EVW_APPLE_BUNDLE_ID", "com.evwallet.hk")
os.environ.setdefault("EVW_GOOGLE_CLIENT_ID", "test-google-client-id")

# ----- stdlib + third-party ---------------------------------------------
import asyncio
import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Annotated

import pytest
import pytest_asyncio

# ----- SQLite compatibility shims (from Agent B's _wallet_setup) ---------
import sqlalchemy.dialects.postgresql as _pg
from fastapi import Header
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Integer, event
from sqlalchemy import BigInteger as _RealBigInt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ----- app imports (must come AFTER env vars) ---------------------------
from evwallet.auth.jwt import encode_jwt
from evwallet.config import get_settings
from evwallet.db.models import Base, ChargingStation, HourlyRate, Pole, User, Wallet
from evwallet.db.session import reset_engine_for_tests

_pg.JSONB = JSON  # type: ignore[attr-defined]


# ----- fixtures ---------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    """One event loop per session (async fixtures need a stable loop)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _register_sqlite_compat(dbapi_conn, _record):
    """Register Postgres-only SQL functions on a sqlite connection.

    * ``gen_random_uuid()`` — returns a random UUID string.
    """
    if hasattr(dbapi_conn, "create_function"):
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))


def _patch_bigint_for_sqlite():
    """Swap BigInteger PKs to Integer for sqlite (auto-increment compat).

    Agent A's models use BigInteger on journal PKs (correct on Postgres);
    SQLite's INTEGER PRIMARY KEY autoincrement requires literal Integer.
    """
    _sqlite_bigint = _RealBigInt().with_variant(Integer, "sqlite")
    import evwallet.db.models as _models_module

    for _name in ("LedgerEntry", "HourlyRate", "SessionTelemetry"):
        _cls = getattr(_models_module, _name, None)
        if _cls is not None and "id" in _cls.__table__.columns:
            _cls.__table__.columns["id"].type = _sqlite_bigint


@pytest_asyncio.fixture
async def test_db_url(tmp_path, monkeypatch):
    """Per-test sqlite DB on disk so engine + AsyncClient share it.

    Yields the URL. Override with ``EVW_TEST_DATABASE_URL=postgresql+asyncpg://...``
    to run against real Postgres.
    """
    _patch_bigint_for_sqlite()

    settings = get_settings()
    db_file = tmp_path / "evwallet_test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setattr(settings, "database_url", url, raising=False)
    reset_engine_for_tests()
    engine = create_async_engine(url, future=True)
    event.listens_for(engine.sync_engine, "connect")(_register_sqlite_compat)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return url


@pytest_asyncio.fixture
async def db_session(test_db_url):
    """Async session bound to the test DB."""
    engine = create_async_engine(test_db_url, future=True)
    event.listens_for(engine.sync_engine, "connect")(_register_sqlite_compat)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user_factory(db_session):
    """Returns ``async (email=None) -> (User, jwt_token, Wallet)``.

    Default wallet has HKD 1000.00 available, 0 reserved.
    """

    async def _make(email=None, available: Decimal = Decimal("1000.00")):
        user = User(
            id=uuid.uuid4(),
            email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Test User",
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
        token, _ = encode_jwt(user.id)
        return user, token, wallet

    return _make


@pytest_asyncio.fixture
async def station_factory(db_session):
    """Returns ``async (...) -> (ChargingStation, [Pole])``.

    Default: 1 station at (22.3, 114.2), 1 CCS2 pole, 50 kW, available.
    """

    async def _make(
        *,
        lat: Decimal = Decimal("22.3000"),
        lng: Decimal = Decimal("114.2000"),
        connectors: list[str] | None = None,
        rate_per_kwh: Decimal = Decimal("9.20"),
        pole_status: str = "available",
        name: str = "Test Station",
    ):
        station = ChargingStation(
            id=uuid.uuid4(),
            external_id=f"ext-{uuid.uuid4().hex[:6]}",
            provider_code="hkev",
            name=name,
            address="1 Test Road",
            district="Central",
            latitude=lat,
            longitude=lng,
            parking_fee_hkd=Decimal("0"),
            amenities=[],
            raw_payload={},
            last_synced_at=datetime.now(tz=UTC),
        )
        db_session.add(station)
        await db_session.flush()

        poles: list[Pole] = []
        connectors = connectors or ["ccs2"]
        for c in connectors:
            pole = Pole(
                id=uuid.uuid4(),
                station_id=station.id,
                external_id=f"p-{uuid.uuid4().hex[:6]}",
                connector=c,
                speed_tier="dc_fast",
                max_kw=Decimal("50"),
                qr_code=f"evwallet://{uuid.uuid4()}?sig=test",
                status=pole_status,
                status_updated_at=datetime.now(tz=UTC),
            )
            db_session.add(pole)
            poles.append(pole)
        await db_session.flush()

        # Add rate rows for every hour of every weekday.
        next_id_base = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
        counter = 0
        for pole in poles:
            for dow in range(7):
                for hour in range(24):
                    rate = HourlyRate(
                        id=next_id_base + counter,
                        pole_id=pole.id,
                        day_of_week=dow,
                        hour_start_local=time(hour=hour),
                        price_per_kwh_hkd=rate_per_kwh,
                        parking_fee_hkd=Decimal("0"),
                        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                        valid_to=None,
                    )
                    db_session.add(rate)
                    counter += 1
        await db_session.commit()
        return station, poles

    return _make


@pytest_asyncio.fixture
async def fake_redis():
    """fakeredis client (async) for pub/sub."""
    import fakeredis.aioredis as fakeredis_async

    client = fakeredis_async.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def _build_test_current_user(factory):
    """Return a ``current_user`` override that resolves a real JWT to a User shim."""

    async def _override(  # type: ignore[no-untyped-def]
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ):
        from fastapi import HTTPException
        from sqlalchemy import select

        from evwallet.auth.jwt import decode_jwt

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing token")
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = decode_jwt(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"bad token: {exc}") from exc
        try:
            user_id = uuid.UUID(str(payload["sub"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="bad sub") from exc

        async with factory() as session:
            user_result = await session.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=401, detail="no user")
            wallet_result = await session.execute(select(Wallet).where(Wallet.user_id == user.id))
            wallet = wallet_result.scalar_one_or_none()

        class _Shim:
            pass

        shim = _Shim()
        shim.id = user.id
        shim.email = user.email
        shim.display_name = user.display_name
        shim.is_active = user.is_active
        shim.is_admin = user.is_admin
        shim.wallet = wallet
        return shim

    return _override


@pytest_asyncio.fixture
async def app_client(test_db_url, fake_redis, monkeypatch):
    """AsyncClient with charging + stations + wallet routers mounted.

    Includes the global ``IDPError`` exception handler so domain errors
    surface as canonical HTTP envelopes. Overrides ``current_user`` and
    ``get_db`` so the test app uses the per-test DB and resolves JWTs.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from evwallet.charging.router import build_router as build_charging
    from evwallet.errors import IDPError
    from evwallet.stations.router import build_router as build_stations
    from evwallet.wallet.router import build_router as build_wallet

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

    app.include_router(build_charging(), prefix="/api/v1")
    app.include_router(build_stations(), prefix="/api/v1")
    app.include_router(build_wallet(), prefix="/api/v1")
    app.state.redis = fake_redis

    engine = create_async_engine(test_db_url, future=True)
    event.listens_for(engine.sync_engine, "connect")(_register_sqlite_compat)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async def _get_test_db():
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    from evwallet.auth.deps import current_user as _real_current_user
    from evwallet.db.session import get_db as _real_get_db

    app.dependency_overrides[_real_current_user] = _build_test_current_user(factory)
    app.dependency_overrides[_real_get_db] = _get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    await engine.dispose()


@pytest.fixture
def make_qr_payload():
    """``(pole_uuid) -> qr_string`` using the configured HMAC secret."""
    from evwallet.charging.qr import compute_signature

    def _make(pole_id):
        return f"evwallet://{pole_id}?sig={compute_signature(str(pole_id))}"

    return _make
