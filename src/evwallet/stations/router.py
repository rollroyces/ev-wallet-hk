"""REST endpoints for stations.

Per ``docs/ARCHITECTURE.md`` §"Stations":

* ``GET /api/v1/stations`` — search with lat/lng/radius + filters
* ``GET /api/v1/stations/{id}`` — full station + poles + next-24h rates
* ``GET /api/v1/stations/{id}/rates?date=YYYY-MM-DD`` — 24-hour TOU window

Routes are mounted under ``/api/v1/stations``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ChargingStation, Pole
from ..db.session import get_db
from ..errors import StationNotFound
from ..logging import get_logger
from .rates import HK_TZ, get_pole_rates_window
from .search import search_stations

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas (public surface)
# ---------------------------------------------------------------------------


class StationSummary(BaseModel):
    """Search-result entry."""

    id: uuid.UUID
    name: str
    address: str
    district: str | None
    latitude: float
    longitude: float
    parking_fee_hkd: float
    amenities: list[str]
    distance_km: float
    matched_pole_count: int


class StationSearchResponse(BaseModel):
    """Response body for ``GET /api/v1/stations``."""

    stations: list[StationSummary]
    total: int


class PoleSummary(BaseModel):
    """A pole attached to a station."""

    id: uuid.UUID
    connector: str
    speed_tier: str
    max_kw: Decimal
    status: str


class HourlyRateOut(BaseModel):
    """A single hour in a 24-hour TOU window."""

    hour_start_local: str
    price_per_kwh_hkd: Decimal | None
    parking_fee_hkd: Decimal | None
    missing: bool = False


class StationDetailResponse(BaseModel):
    """Response body for ``GET /api/v1/stations/{id}``."""

    id: uuid.UUID
    name: str
    address: str
    district: str | None
    latitude: float
    longitude: float
    parking_fee_hkd: Decimal
    amenities: list[str]
    poles: list[PoleSummary]
    next_24h_rates: list[HourlyRateOut]


class StationRatesResponse(BaseModel):
    """Response body for ``GET /api/v1/stations/{id}/rates``."""

    station_id: uuid.UUID
    pole_id: uuid.UUID
    date_local: str
    rates: list[HourlyRateOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _station_to_summary(row: dict[str, Any]) -> StationSummary:
    return StationSummary(
        id=uuid.UUID(row["id"]),
        name=row["name"],
        address=row["address"],
        district=row.get("district"),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        parking_fee_hkd=float(row.get("parking_fee_hkd", 0)),
        amenities=list(row.get("amenities") or []),
        distance_km=float(row["distance_km"]),
        matched_pole_count=int(row.get("matched_pole_count", 0)),
    )


def _pole_to_summary(pole: Pole) -> PoleSummary:
    return PoleSummary(
        id=pole.id,
        connector=pole.connector,
        speed_tier=pole.speed_tier,
        max_kw=Decimal(pole.max_kw),
        status=pole.status,
    )


def _rate_to_out(d: dict[str, Any]) -> HourlyRateOut:
    return HourlyRateOut(
        hour_start_local=d["hour_start_local"],
        price_per_kwh_hkd=Decimal(d["price_per_kwh_hkd"])
        if d.get("price_per_kwh_hkd") is not None
        else None,
        parking_fee_hkd=Decimal(d["parking_fee_hkd"])
        if d.get("parking_fee_hkd") is not None
        else None,
        missing=bool(d.get("missing", False)),
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_router() -> APIRouter:
    """Build the stations router (prefix ``/api/v1/stations``)."""
    router = APIRouter(prefix="/stations", tags=["stations"])

    @router.get("", response_model=StationSearchResponse)
    async def search(
        lat: Annotated[float, Query(ge=-90, le=90)],
        lng: Annotated[float, Query(ge=-180, le=180)],
        radius_km: Annotated[float, Query(gt=0, le=200)] = 5.0,
        connector: Annotated[str | None, Query()] = None,
        min_kw: Annotated[float | None, Query(gt=0)] = None,
        limit: Annotated[int, Query(gt=0, le=200)] = 50,
        db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    ) -> StationSearchResponse:
        """Search stations by location + optional filters."""
        rows = await search_stations(
            db,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
            connector=connector,
            min_kw=min_kw,
            limit=limit,
        )
        return StationSearchResponse(
            stations=[_station_to_summary(r) for r in rows],
            total=len(rows),
        )

    @router.get("/{station_id}", response_model=StationDetailResponse)
    async def get_station(
        station_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> StationDetailResponse:
        """Return full station detail (poles + next 24h rates)."""
        station = (
            await db.execute(select(ChargingStation).where(ChargingStation.id == station_id))
        ).scalar_one_or_none()
        if station is None:
            raise StationNotFound("Station not found", details={"station_id": str(station_id)})

        poles = (
            (await db.execute(select(Pole).where(Pole.station_id == station_id))).scalars().all()
        )
        pole_summaries = [_pole_to_summary(p) for p in poles]

        # Next-24h rates: aggregate the first pole's rates for the demo.
        # In a multi-pole station, the response shape can be extended.
        next_24h: list[HourlyRateOut] = []
        if poles:
            local_today = datetime.now(tz=HK_TZ).date()
            try:
                rows = await get_pole_rates_window(db, pole_id=poles[0].id, date_local=local_today)
                next_24h = [_rate_to_out(r) for r in rows]
            except Exception as exc:
                _log.warning(
                    "stations.get.next_24h_failed station=%s pole=%s err=%s",
                    station_id,
                    poles[0].id,
                    exc,
                )

        return StationDetailResponse(
            id=station.id,
            name=station.name,
            address=station.address,
            district=station.district,
            latitude=float(station.latitude),
            longitude=float(station.longitude),
            parking_fee_hkd=Decimal(station.parking_fee_hkd or 0),
            amenities=list(station.amenities or []),
            poles=pole_summaries,
            next_24h_rates=next_24h,
        )

    @router.get("/{station_id}/rates", response_model=StationRatesResponse)
    async def get_rates(
        station_id: uuid.UUID,
        date_param: Annotated[
            date | None,
            Query(
                alias="date",
                description="Local date (Asia/Hong_Kong). Defaults to today.",
            ),
        ] = None,
        pole_id: Annotated[
            uuid.UUID | None,
            Query(description="Specific pole; defaults to the first pole."),
        ] = None,
        db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    ) -> StationRatesResponse:
        """Return the 24-hour TOU window for a station's first pole."""
        station = (
            await db.execute(select(ChargingStation).where(ChargingStation.id == station_id))
        ).scalar_one_or_none()
        if station is None:
            raise StationNotFound("Station not found", details={"station_id": str(station_id)})

        if pole_id is None:
            first_pole = (
                await db.execute(
                    select(Pole).where(Pole.station_id == station_id).order_by(Pole.id).limit(1)
                )
            ).scalar_one_or_none()
            if first_pole is None:
                raise StationNotFound(
                    "Station has no poles",
                    details={"station_id": str(station_id)},
                )
            pole_id = first_pole.id

        local_date = date_param or datetime.now(tz=HK_TZ).date()
        rows = await get_pole_rates_window(db, pole_id=pole_id, date_local=local_date)
        return StationRatesResponse(
            station_id=station_id,
            pole_id=pole_id,
            date_local=local_date.isoformat(),
            rates=[_rate_to_out(r) for r in rows],
        )

    return router


__all__ = ["build_router"]
