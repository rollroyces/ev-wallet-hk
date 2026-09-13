"""Geo + filter search for charging stations.

MVP approach: fetch a bounding box from Postgres in SQL, then refine by
exact haversine distance + connector/kW filters in Python. This avoids
PostGIS without giving up exact ordering.

The bounding box is widened by the requested radius so a point on the
exact radius edge still passes the SQL filter; the Python pass then
strips anything outside the radius.

Public entry points:
    :func:`search_stations`
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ChargingStation, Pole

# Earth radius in kilometres (mean).
_EARTH_R_KM = 6371.0088

# Padding added to the bounding box so the in-Python haversine step sees
# every candidate (it would otherwise miss stations exactly on the radius).
_BBOX_PADDING_KM = 1.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two (lat, lng) pairs in kilometres."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return _EARTH_R_KM * c


def _bbox(
    lat: float, lng: float, radius_km: float
) -> tuple[float, float, float, float]:
    """Return ``(lat_min, lat_max, lng_min, lng_max)`` covering ``radius_km``.

    ``lat``-degree distance is constant in km; ``lng``-degree distance is
    scaled by ``cos(lat)``. We pad by ``_BBOX_PADDING_KM`` to compensate
    for the bounding box being a square.
    """
    lat_pad = radius_km / 111.32 + (_BBOX_PADDING_KM / 111.32)
    lat_min = lat - lat_pad
    lat_max = lat + lat_pad
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    lng_pad = (radius_km / (111.32 * cos_lat)) + (_BBOX_PADDING_KM / (111.32 * cos_lat))
    lng_min = lng - lng_pad
    lng_max = lng + lng_pad
    return lat_min, lat_max, lng_min, lng_max


async def search_stations(
    db: AsyncSession,
    *,
    lat: float,
    lng: float,
    radius_km: float = 5.0,
    connector: str | None = None,
    min_kw: float | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return stations within ``radius_km`` of ``(lat, lng)``, ordered by distance.

    Args:
        db: An async SQLAlchemy session.
        lat: Latitude in decimal degrees (WGS-84).
        lng: Longitude in decimal degrees (WGS-84).
        radius_km: Maximum distance in kilometres. Defaults to 5.
        connector: Optional pole-connector filter (``ccs2``/``type2``/etc).
        min_kw: Optional minimum pole ``max_kw`` filter.
        limit: Maximum number of stations to return.

    Returns:
        A list of station dicts ordered by ascending haversine distance.
        Each dict carries the station fields plus ``distance_km`` and
        ``matched_poles`` (the poles that matched the filters, if any).

    Notes:
        The function runs two queries: one for stations inside the bounding
        box, and (when filters are set) one for the candidate poles. The
        in-memory haversine step is then applied to keep ordering exact.
    """
    if radius_km <= 0:
        radius_km = 0.1
    if limit <= 0:
        limit = 50
    if limit > 200:
        limit = 200

    lat_min, lat_max, lng_min, lng_max = _bbox(lat, lng, radius_km)

    # Stations inside the bounding box.
    stmt = select(ChargingStation).where(
        ChargingStation.latitude >= Decimal(str(lat_min)),
        ChargingStation.latitude <= Decimal(str(lat_max)),
        ChargingStation.longitude >= Decimal(str(lng_min)),
        ChargingStation.longitude <= Decimal(str(lng_max)),
    )
    station_rows = (await db.execute(stmt)).scalars().all()

    if not station_rows:
        return []

    station_ids = [s.id for s in station_rows]

    # Optional pole-level filter.
    pole_stmt = select(Pole).where(Pole.station_id.in_(station_ids))
    if connector is not None:
        pole_stmt = pole_stmt.where(Pole.connector == connector)
    if min_kw is not None:
        pole_stmt = pole_stmt.where(Pole.max_kw >= Decimal(str(min_kw)))
    pole_rows = (await db.execute(pole_stmt)).scalars().all()

    poles_by_station: dict[uuid.UUID, list[Pole]] = {}
    for pole in pole_rows:
        poles_by_station.setdefault(pole.station_id, []).append(pole)

    results: list[dict[str, Any]] = []
    for station in station_rows:
        distance = haversine_km(
            lat,
            lng,
            float(station.latitude),
            float(station.longitude),
        )
        if distance > radius_km:
            continue
        matched = poles_by_station.get(station.id, [])
        # If filters were supplied, drop stations without matching poles.
        if (connector is not None or min_kw is not None) and not matched:
            continue
        results.append(
            {
                "id": str(station.id),
                "external_id": station.external_id,
                "provider_code": station.provider_code,
                "name": station.name,
                "address": station.address,
                "district": station.district,
                "latitude": float(station.latitude),
                "longitude": float(station.longitude),
                "parking_fee_hkd": float(station.parking_fee_hkd or 0),
                "amenities": list(station.amenities or []),
                "distance_km": round(distance, 4),
                "matched_pole_count": len(matched),
            }
        )

    results.sort(key=lambda s: s["distance_km"])
    return results[:limit]


__all__ = ["haversine_km", "search_stations"]
