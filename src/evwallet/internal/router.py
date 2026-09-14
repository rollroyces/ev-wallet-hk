"""Internal endpoints used by:
- n8n provider ingestion (stations/upsert, rates/bulk-upsert)
- Mobile app (push-tokens registration, sessions list)
- Web admin (stations list, all transactions)

All internal endpoints are gated by a shared bearer token
(``EVW_INTERNAL_TOKEN``); admin endpoints additionally require
``current_user.is_admin=True``.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import current_user
from ..config import get_settings
from ..db.models import (
    ChargingStation,
    HourlyRate,
    Pole,
    PushToken,
    User,
    WalletTransaction,
)
from ..db.session import get_db
from .providers import ADAPTERS, ProviderUnavailable
from .schemas import StationUpsertIn, StationUpsertOut

# ---------------------------------------------------------------------------
# Auth gate for n8n-style endpoints
# ---------------------------------------------------------------------------


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    """Gate the n8n-facing endpoints with a shared static token.

    The token is set via ``EVW_INTERNAL_TOKEN`` and rotated by redeploy.
    Compare with ``hmac.compare_digest`` to avoid timing attacks.
    """
    expected = get_settings().internal_token
    if not expected:
        # No token configured → internal endpoints are DISABLED in prod.
        # In dev, allow if env explicitly opts in.
        if get_settings().env != "development":
            raise HTTPException(status_code=503, detail="internal endpoints disabled")
        return
    if not x_internal_token or not hmac.compare_digest(
        x_internal_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid internal token")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------
#
# Pydantic models for the /internal/* endpoints live in ``schemas.py`` to
# break the circular import with ``providers.py`` (which uses them as
# adapter return types). The router re-exports them so the existing
# ``from evwallet.internal.router import StationUpsertIn`` keeps working.
# ---------------------------------------------------------------------------
from .schemas import (  # noqa: E402, F401 — re-export
    AdminStationRow,
    AdminTransactionRow,
    BulkRateUpsertIn,
    BulkRateUpsertOut,
    PoleUpsertIn,
    PushTokenRegisterIn,
    PushTokenRegisterOut,
    RateUpsertIn,
)

_log = logging.getLogger(__name__)


def _get_http_client() -> httpx.AsyncClient:
    """Reuse a single httpx client across the request lifecycle for efficiency."""
    return httpx.AsyncClient(timeout=60.0, follow_redirects=True)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/internal", tags=["internal"])

    # --- Provider polling (n8n calls these) ---------------------------------

    @router.get(
        "/providers/{provider_code}/stations",
        response_model=list[StationUpsertIn],
        dependencies=[Depends(require_internal_token)],
    )
    async def get_provider_stations(
        provider_code: str,
    ) -> list[StationUpsertIn]:
        """Fetch live station data for a single provider, in canonical shape.

        Called by the n8n polling workflows (hkev-poll, clp-poll, etc.).
        Returns a JSON array of ``StationUpsertIn`` records — the same shape
        the workflows POST to ``/stations/upsert``.

        Provider adapters:
        - ``clp``  → real CLP API (data.gov.hk Open Data proxy, requires
          ``EVW_DATAGOVHK_API_KEY``)
        - ``epd``  → HK EPD quarterly XLSX (covers all non-CLP / non-Tesla
          operators in one batch; no real-time status)
        - ``hkev`` / ``shell`` / ``tesla`` → return HTTP 503 with
          ``ProviderUnavailable`` + contact email (no public API; see
          docs/research/OPERATORS.md)
        """
        adapter_cls = ADAPTERS.get(provider_code.lower())
        if adapter_cls is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PROVIDER_NOT_REGISTERED",
                    "provider_code": provider_code,
                    "known": sorted(ADAPTERS.keys()),
                },
            )

        async with _get_http_client() as client:
            adapter = adapter_cls(http_client=client)
            try:
                results = await adapter.fetch()
            except ProviderUnavailable as exc:
                _log.warning(
                    "provider %s unavailable: %s (contact=%s)",
                    provider_code, exc, exc.contact_email,
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "PROVIDER_UNAVAILABLE",
                        "provider_code": provider_code,
                        "message": str(exc),
                        "contact_email": exc.contact_email,
                    },
                ) from exc

        _log.info("provider %s returned %d stations", provider_code, len(results))
        return [r.station for r in results]

    # --- n8n ingestion ------------------------------------------------------

    @router.post(
        "/stations/upsert",
        response_model=StationUpsertOut,
        dependencies=[Depends(require_internal_token)],
    )
    async def upsert_station(
        payload: StationUpsertIn,
        db: AsyncSession = Depends(get_db),
    ) -> StationUpsertOut:
        """Upsert a station and its poles.

        Called by the n8n provider polling workflows (Agent F).
        Idempotent on (provider_code, external_id).
        """
        stmt = select(ChargingStation).where(
            ChargingStation.provider_code == payload.provider_code,
            ChargingStation.external_id == payload.external_id,
        )
        result = await db.execute(stmt)
        station = result.scalar_one_or_none()

        now = datetime.now(tz=UTC)
        if station is None:
            station = ChargingStation(
                id=uuid.uuid4(),
                provider_code=payload.provider_code,
                external_id=payload.external_id,
                name=payload.name,
                address=payload.address,
                district=payload.district,
                latitude=payload.latitude,
                longitude=payload.longitude,
                parking_fee_hkd=payload.parking_fee_hkd,
                amenities=payload.amenities,
                raw_payload=payload.raw_payload,
                last_synced_at=now,
            )
            db.add(station)
            await db.flush()
        else:
            station.name = payload.name
            station.address = payload.address
            station.district = payload.district
            station.latitude = payload.latitude
            station.longitude = payload.longitude
            station.parking_fee_hkd = payload.parking_fee_hkd
            station.amenities = payload.amenities
            station.raw_payload = payload.raw_payload
            station.last_synced_at = now
            await db.flush()

        poles_created = 0
        poles_updated = 0
        for pole_in in payload.poles:
            pole_stmt = select(Pole).where(
                Pole.station_id == station.id,
                Pole.external_id == pole_in.external_id,
            )
            pole_res = await db.execute(pole_stmt)
            pole = pole_res.scalar_one_or_none()

            # If no QR code provided, derive one with an HMAC over the
            # external_id; n8n will recompute the same HMAC on scan.
            if pole_in.qr_code is None:
                settings = get_settings()
                import hashlib as _h

                mac = _h.sha256(
                    (settings.qr_hmac_secret or settings.jwt_secret).encode()
                ).hexdigest()[:16]
                pole_in.qr_code = (
                    f"{payload.provider_code}://{pole_in.external_id}-{payload.external_id}-{mac}"
                )

            if pole is None:
                pole = Pole(
                    id=uuid.uuid4(),
                    station_id=station.id,
                    external_id=pole_in.external_id,
                    connector=pole_in.connector,
                    speed_tier=pole_in.speed_tier,
                    max_kw=pole_in.max_kw,
                    qr_code=pole_in.qr_code,
                    status=pole_in.status,
                    status_updated_at=pole_in.status_updated_at or now,
                )
                db.add(pole)
                poles_created += 1
            else:
                pole.connector = pole_in.connector
                pole.speed_tier = pole_in.speed_tier
                pole.max_kw = pole_in.max_kw
                pole.qr_code = pole_in.qr_code
                if pole_in.status_updated_at:
                    pole.status = pole_in.status
                    pole.status_updated_at = pole_in.status_updated_at
                poles_updated += 1

        await db.commit()
        return StationUpsertOut(
            station_id=station.id,
            external_id=station.external_id,
            poles_created=poles_created,
            poles_updated=poles_updated,
        )

    @router.post(
        "/rates/bulk-upsert",
        response_model=BulkRateUpsertOut,
        dependencies=[Depends(require_internal_token)],
    )
    async def bulk_upsert_rates(
        payload: BulkRateUpsertIn,
        db: AsyncSession = Depends(get_db),
    ) -> BulkRateUpsertOut:
        """Upsert a batch of hourly rates. Idempotent on
        (pole_id, day_of_week, hour_start_local, valid_from).
        """
        from datetime import time as _time

        upserted = 0
        for rate_in in payload.rates:
            station_stmt = select(ChargingStation).where(
                ChargingStation.provider_code == rate_in.provider_code,
                ChargingStation.external_id == rate_in.station_external_id,
            )
            station = (await db.execute(station_stmt)).scalar_one_or_none()
            if station is None:
                continue  # silently skip orphan rates
            pole_stmt = select(Pole).where(
                Pole.station_id == station.id,
                Pole.external_id == rate_in.pole_external_id,
            )
            pole = (await db.execute(pole_stmt)).scalar_one_or_none()
            if pole is None:
                continue

            rate_stmt = select(HourlyRate).where(
                HourlyRate.pole_id == pole.id,
                HourlyRate.day_of_week == rate_in.day_of_week,
                HourlyRate.hour_start_local == _time(hour=rate_in.hour_start_local),
                HourlyRate.valid_from == rate_in.valid_from,
            )
            rate = (await db.execute(rate_stmt)).scalar_one_or_none()

            if rate is None:
                rate = HourlyRate(
                    id=uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF,
                    pole_id=pole.id,
                    day_of_week=rate_in.day_of_week,
                    hour_start_local=_time(hour=rate_in.hour_start_local),
                    price_per_kwh_hkd=rate_in.price_per_kwh_hkd,
                    parking_fee_hkd=rate_in.parking_fee_hkd,
                    valid_from=rate_in.valid_from,
                    valid_to=rate_in.valid_to,
                )
                db.add(rate)
            else:
                rate.price_per_kwh_hkd = rate_in.price_per_kwh_hkd
                rate.parking_fee_hkd = rate_in.parking_fee_hkd
                rate.valid_to = rate_in.valid_to
            upserted += 1

        await db.commit()
        return BulkRateUpsertOut(rates_upserted=upserted)

    # --- Admin views --------------------------------------------------------

    async def require_admin(
        user: User = Depends(current_user),
    ) -> User:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        return user

    @router.get(
        "/stations/list",
        response_model=list[AdminStationRow],
        dependencies=[Depends(require_admin)],
    )
    async def list_stations_admin(
        db: AsyncSession = Depends(get_db),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AdminStationRow]:
        from sqlalchemy import func

        stmt = (
            select(
                ChargingStation,
                func.count(Pole.id).label("pole_count"),
            )
            .outerjoin(Pole, Pole.station_id == ChargingStation.id)
            .group_by(ChargingStation.id)
            .order_by(ChargingStation.last_synced_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [
            AdminStationRow(
                id=station.id,
                external_id=station.external_id,
                provider_code=station.provider_code,
                name=station.name,
                address=station.address,
                district=station.district,
                last_synced_at=station.last_synced_at,
                pole_count=pole_count,
            )
            for station, pole_count in rows
        ]

    @router.get(
        "/wallet/admin/all-transactions",
        response_model=list[AdminTransactionRow],
        dependencies=[Depends(require_admin)],
    )
    async def list_all_transactions_admin(
        db: AsyncSession = Depends(get_db),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AdminTransactionRow]:
        stmt = (
            select(WalletTransaction, User.email)
            .join(User, User.id == WalletTransaction.user_id)
            .order_by(WalletTransaction.posted_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [
            AdminTransactionRow(
                id=txn.id,
                wallet_id=txn.wallet_id,
                user_email=email,
                kind=txn.kind,
                status=txn.status,
                amount=txn.amount,
                currency=txn.currency,
                posted_at=txn.posted_at,
            )
            for txn, email in rows
        ]

    # --- Mobile push-token registration -------------------------------------

    @router.post(
        "/auth/push-tokens",
        response_model=PushTokenRegisterOut,
    )
    async def register_push_token(
        payload: PushTokenRegisterIn,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ) -> PushTokenRegisterOut:
        """Register an APNs/FCM push token for the authenticated user.

        Idempotent: re-registering the same token updates the timestamp.
        """
        existing = (
            await db.execute(select(PushToken).where(PushToken.token == payload.token))
        ).scalar_one_or_none()
        now = datetime.now(tz=UTC)
        if existing is None:
            pt = PushToken(
                id=uuid.uuid4(),
                user_id=user.id,
                token=payload.token,
                platform=payload.platform,
                device_id=payload.device_id,
                created_at=now,
                updated_at=now,
            )
            db.add(pt)
        else:
            existing.user_id = user.id
            existing.platform = payload.platform
            existing.device_id = payload.device_id
            existing.updated_at = now
            pt = existing
        await db.commit()
        return PushTokenRegisterOut(
            id=pt.id, token=pt.token, platform=pt.platform, created_at=pt.created_at
        )

    return router


__all__ = ["build_router", "require_internal_token"]
