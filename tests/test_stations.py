"""Tests for the stations search and rate modules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from evwallet.stations.search import haversine_km, search_stations

# ---------------------------------------------------------------------------
# search_stations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_stations_within_radius(db_session, station_factory):
    """Stations within ``radius_km`` are returned."""
    _station_a, _ = await station_factory(
        lat=Decimal("22.3000"), lng=Decimal("114.2000"), name="nearby"
    )
    _station_b, _ = await station_factory(
        lat=Decimal("22.3100"), lng=Decimal("114.2100"), name="also-near"
    )
    _station_far, _ = await station_factory(
        lat=Decimal("22.5000"), lng=Decimal("114.5000"), name="far"
    )
    results = await search_stations(
        db_session, lat=22.30, lng=114.20, radius_km=5.0
    )
    names = {r["name"] for r in results}
    assert "nearby" in names
    assert "also-near" in names
    assert "far" not in names


@pytest.mark.asyncio
async def test_search_stations_filters_by_connector(db_session, station_factory):
    """``connector=`` excludes stations whose poles don't match."""
    _ccs, _ = await station_factory(
        lat=Decimal("22.3000"), lng=Decimal("114.2000"),
        connectors=["ccs2"], name="ccs-station",
    )
    _type2, _ = await station_factory(
        lat=Decimal("22.3005"), lng=Decimal("114.2005"),
        connectors=["type2"], name="type2-station",
    )
    results = await search_stations(
        db_session, lat=22.30, lng=114.20, radius_km=5.0, connector="ccs2"
    )
    names = {r["name"] for r in results}
    assert "ccs-station" in names
    assert "type2-station" not in names


@pytest.mark.asyncio
async def test_search_stations_orders_by_distance(db_session, station_factory):
    """Results are sorted ascending by haversine distance."""
    await station_factory(
        lat=Decimal("22.3010"), lng=Decimal("114.2010"), name="closer"
    )
    await station_factory(
        lat=Decimal("22.3050"), lng=Decimal("114.2050"), name="farther"
    )
    results = await search_stations(
        db_session, lat=22.30, lng=114.20, radius_km=5.0
    )
    distances = [r["distance_km"] for r in results]
    assert distances == sorted(distances)


def test_haversine_known_pairs():
    """Spot-check the haversine formula against known city pairs."""
    # Hong Kong → Macau (~60 km).
    d = haversine_km(22.302, 114.177, 22.199, 113.544)
    assert 50 < d < 80
    # Same point → 0.
    assert haversine_km(22.30, 114.20, 22.30, 114.20) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Rates window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pole_rates_window_returns_24_hours(db_session, station_factory):
    """The TOU window always returns 24 entries, one per hour."""
    _, poles = await station_factory()
    pole = poles[0]
    from evwallet.stations.rates import get_pole_rates_window

    rows = await get_pole_rates_window(
        db_session, pole_id=pole.id, date_local=datetime(2026, 9, 14, tzinfo=UTC).date()
    )
    assert len(rows) == 24
    seen_hours = {r["hour_start_local"] for r in rows}
    assert len(seen_hours) == 24


@pytest.mark.asyncio
async def test_get_current_rate_at_specific_hour(db_session, station_factory):
    """``get_current_rate`` returns the row for the matching hour-of-day."""
    _, poles = await station_factory(rate_per_kwh=Decimal("12.50"))
    pole = poles[0]
    from evwallet.stations.rates import get_current_rate

    ts = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)  # 14:00 UTC
    rate = await get_current_rate(db_session, pole_id=pole.id, ts_local=ts)
    assert rate is not None
    assert Decimal(rate["price_per_kwh_hkd"]) == Decimal("12.50")
