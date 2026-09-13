"""Time-of-use (TOU) rate lookup.

Per ``docs/ARCHITECTURE.md`` the HourlyRate table is keyed by
``(pole_id, day_of_week, hour_start_local)`` and stores ``price_per_kwh_hkd``
plus an optional parking fee. Rates are localized to Asia/Hong_Kong.

Public entry points:
    :func:`get_pole_rates_window` — 24 hours starting at 00:00 HKT on a date
    :func:`get_current_rate` — the rate in effect at a given moment
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HourlyRate
from ..errors import PoleNotFound

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _hk_date_to_day_of_week(d: date) -> int:
    """Return ``0=Mon … 6=Sun`` matching ``HourlyRate.day_of_week``."""
    return d.weekday()


async def get_pole_rates_window(
    db: AsyncSession,
    *,
    pole_id: uuid.UUID,
    date_local: date,
) -> list[dict[str, Any]]:
    """Return the 24 TOU rates starting at 00:00 HKT on ``date_local``.

    Args:
        db: An async SQLAlchemy session.
        pole_id: The pole id.
        date_local: The local date in Asia/Hong_Kong.

    Returns:
        A list of 24 dicts ordered by ``hour_start_local``. Missing hours
        are filled with ``None`` placeholders so the consumer always sees a
        full 24-hour window.

    Raises:
        PoleNotFound: If no pole matches ``pole_id``. (We can't easily tell
            poles vs stations apart without a query, so we just confirm the
            pole exists before returning rate data.)
    """
    day_of_week = _hk_date_to_day_of_week(date_local)
    valid_from_anchor = datetime(date_local.year, date_local.month, date_local.day, tzinfo=timezone.utc)
    next_day = valid_from_anchor + timedelta(days=1)

    # Fetch any rates that overlap this local day. ``valid_to`` may be NULL
    # (open-ended) so we use ``OR`` carefully.
    stmt = select(HourlyRate).where(
        HourlyRate.pole_id == pole_id,
        HourlyRate.day_of_week == day_of_week,
        HourlyRate.valid_from < next_day,
        or_(HourlyRate.valid_to.is_(None), HourlyRate.valid_to > valid_from_anchor),
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        # Distinguish "no pole" from "no rates" by counting any row at all
        # for this pole id. Cheaper than a second join.
        from ..db.models import Pole as PoleModel

        pole_exists = (
            await db.execute(select(PoleModel.id).where(PoleModel.id == pole_id))
        ).first()
        if pole_exists is None:
            raise PoleNotFound(
                "Pole not found", details={"pole_id": str(pole_id)}
            )

    by_hour: dict[int, HourlyRate] = {}
    for r in rows:
        # If multiple rates cover the same hour, prefer the most recent
        # (largest valid_from).
        existing = by_hour.get(r.hour_start_local.hour)
        if existing is None or r.valid_from > existing.valid_from:
            by_hour[r.hour_start_local.hour] = r

    result: list[dict[str, Any]] = []
    for hour in range(24):
        rate = by_hour.get(hour)
        if rate is None:
            result.append(
                {
                    "pole_id": str(pole_id),
                    "date_local": date_local.isoformat(),
                    "hour_start_local": time(hour=hour).isoformat(timespec="hours"),
                    "price_per_kwh_hkd": None,
                    "parking_fee_hkd": None,
                    "missing": True,
                }
            )
        else:
            result.append(
                {
                    "pole_id": str(rate.pole_id),
                    "date_local": date_local.isoformat(),
                    "hour_start_local": rate.hour_start_local.isoformat(timespec="hours"),
                    "price_per_kwh_hkd": str(Decimal(rate.price_per_kwh_hkd)),
                    "parking_fee_hkd": str(Decimal(rate.parking_fee_hkd or 0)),
                    "valid_from": rate.valid_from.isoformat(),
                    "valid_to": rate.valid_to.isoformat() if rate.valid_to else None,
                    "missing": False,
                }
            )
    return result


async def get_current_rate(
    db: AsyncSession,
    *,
    pole_id: uuid.UUID,
    ts_local: datetime,
) -> dict[str, Any] | None:
    """Return the rate in effect at ``ts_local`` (Asia/Hong_Kong).

    Args:
        db: An async SQLAlchemy session.
        pole_id: The pole id.
        ts_local: A datetime in Asia/Hong_Kong (or any timezone — the
            function will normalize to HKT).

    Returns:
        The matching rate dict, or ``None`` if no row covers the requested
        moment. Always returns ``None`` (rather than raising) so the caller
        can fall back to a default rate.
    """
    if ts_local.tzinfo is None:
        ts_local = ts_local.replace(tzinfo=HK_TZ)
    ts_local = ts_local.astimezone(HK_TZ)
    local_date = ts_local.date()
    dow = _hk_date_to_day_of_week(local_date)
    hour = ts_local.hour

    stmt = select(HourlyRate).where(
        HourlyRate.pole_id == pole_id,
        HourlyRate.day_of_week == dow,
        HourlyRate.valid_from <= ts_local,
        or_(HourlyRate.valid_to.is_(None), HourlyRate.valid_to > ts_local),
    )
    rows = (await db.execute(stmt)).scalars().all()
    candidates = [
        r for r in rows if r.hour_start_local.hour == hour
    ]
    if not candidates:
        return None
    rate = max(candidates, key=lambda r: r.valid_from)
    return {
        "pole_id": str(rate.pole_id),
        "date_local": local_date.isoformat(),
        "hour_start_local": rate.hour_start_local.isoformat(timespec="hours"),
        "price_per_kwh_hkd": str(Decimal(rate.price_per_kwh_hkd)),
        "parking_fee_hkd": str(Decimal(rate.parking_fee_hkd or 0)),
        "valid_from": rate.valid_from.isoformat(),
        "valid_to": rate.valid_to.isoformat() if rate.valid_to else None,
        "missing": False,
    }


__all__ = ["get_current_rate", "get_pole_rates_window"]
