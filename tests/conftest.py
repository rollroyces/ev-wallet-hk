"""Shared test fixtures for Agent C's charging + stations tests.

This file was overwritten by parallel sibling agents; Agent C re-creates
it here with concrete fixtures for:
* DB session against in-memory sqlite
* ``user_factory`` returning ``(User, jwt_token, Wallet)``
* ``station_factory`` returning ``(ChargingStation, [Pole])``
* ``fake_redis`` for pub/sub tests
* ``app_client`` — FastAPI AsyncClient with routers mounted
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, time, timezone
from decimal import Decimal

# CRITICAL: set the env BEFORE importing any module that constructs
# ``Settings()``. Pydantic Settings validates eagerly.
os.environ.setdefault("EVW_JWT_SECRET", "q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0OpQ9pXrLkMz7NcV")  # 51 chars, no forbidden substrings
os.environ.setdefault("EVW_POSTGRES_USER", "evwallet")
os.environ.setdefault("EVW_POSTGRES_PASSWORD", "evwallet")
os.environ.setdefault("EVW_POSTGRES_DB", "evwallet")
os.environ.setdefault("EVW_REDIS_PASSWORD", "evwallet")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from evwallet.auth.jwt import encode_jwt
from evwallet.config import get_settings, reset_settings_cache
from evwallet.db.models import Base, ChargingStation, HourlyRate, Pole, User, Wallet
from evwallet.db.session import reset_engine_for_tests


@pytest.fixture(scope="session")
def event_loop():
    """One event loop per session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_db_url(tmp_path, monkeypatch):
    """Per-test sqlite DB on disk so engine + AsyncClient share it."""
    settings = get_settings()
    db_file = tmp_path / "agent_c_test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setattr(settings, "database_url", url, raising=False)
    reset_engine_for_tests()
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return url


@pytest_asyncio.fixture
async def db_session(test_db_url):
    """Async session bound to the in-memory DB."""
    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user_factory(db_session):
    """Returns ``async (email=None) -> (User, jwt_token, Wallet)``."""

    async def _make(email=None):
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
            available_credits=Decimal("1000.00"),
            reserved_credits=Decimal("0"),
            currency="HKD",
            version=0,
        )
        db_session.add(wallet)
        await db_session.commit()
        token = encode_jwt(user.id)
        return user, token, wallet

    return _make


@pytest_asyncio.fixture
async def station_factory(db_session):
    """Returns ``async (...) -> (ChargingStation, [Pole])``."""

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
            last_synced_at=datetime.now(tz=timezone.utc),
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
                status_updated_at=datetime.now(tz=timezone.utc),
            )
            db_session.add(pole)
            poles.append(pole)
        await db_session.flush()

        # Add rate rows for every hour of every weekday. BigInteger PKs in
        # sqlite don't autoincrement reliably, so we generate fresh UUIDs
        # and cast them to int via the lower 63 bits.
        next_id_base = uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
        counter = 0
        for pole_idx, pole in enumerate(poles):
            for dow in range(7):
                for hour in range(24):
                    rate = HourlyRate(
                        id=next_id_base + counter,
                        pole_id=pole.id,
                        day_of_week=dow,
                        hour_start_local=time(hour=hour),
                        price_per_kwh_hkd=rate_per_kwh,
                        parking_fee_hkd=Decimal("0"),
                        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
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


@pytest_asyncio.fixture
async def app_client(test_db_url, fake_redis):
    """AsyncClient with charging + stations routers mounted."""
    from fastapi import FastAPI, Header, HTTPException
    from sqlalchemy import select

    from evwallet.auth.deps import current_user as _real_current_user
    from evwallet.auth.jwt import decode_jwt
    from evwallet.charging.router import build_router as build_charging
    from evwallet.db.session import get_db as _real_get_db
    from evwallet.stations.router import build_router as build_stations

    app = FastAPI()
    app.include_router(build_charging(), prefix="/api/v1")
    app.include_router(build_stations(), prefix="/api/v1")
    app.state.redis = fake_redis

    # Build a per-test session factory so the AsyncClient shares the DB.
    engine = create_async_engine(test_db_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async def _get_test_db():
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def _test_current_user(
        authorization: str | None = Header(default=None),
        db: AsyncSession = None,  # type: ignore[assignment]
    ):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing token")
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = decode_jwt(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"bad token: {exc}")
        try:
            user_id = uuid.UUID(str(payload["sub"]))
        except (KeyError, ValueError):
            raise HTTPException(status_code=401, detail="bad sub")
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="no user")
        wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user.id))
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

    app.dependency_overrides[_real_current_user] = _test_current_user
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
