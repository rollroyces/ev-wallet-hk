"""Telemetry writer — append-only session frames + Redis publish.

The OCPP bridge (Agent F) writes telemetry here for every 2-second frame the
charger reports. The WebSocket hub (:mod:`evwallet.charging.ws`) subscribes
to the Redis channel for its session and forwards each frame to the mobile
client.

Tables written:
    :class:`evwallet.db.models.SessionTelemetry` — append-only.

Redis channels published:
    ``charging:session:{session_id}`` — JSON frame.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SessionTelemetry
from ..logging import get_logger

_log = get_logger(__name__)

# Redis channel pattern (must match the WS hub).
_CHANNEL_FMT = "charging:session:{session_id}"


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def channel_for(session_id: uuid.UUID | str) -> str:
    """Return the Redis channel name for a session id."""
    return _CHANNEL_FMT.format(session_id=session_id)


async def write_frame(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    kwh_cumulative: Decimal,
    kw_instant: Decimal,
    soc_pct: int | None,
    cost_hkd_cumulative: Decimal,
    raw: dict[str, Any] | None = None,
) -> SessionTelemetry:
    """Append a telemetry frame for ``session_id``.

    Args:
        db: An async SQLAlchemy session.
        session_id: The charging session id.
        kwh_cumulative: Lifetime kWh delivered in this session, decimal.
        kw_instant: Instantaneous charging power (kW), decimal.
        soc_pct: State-of-charge percent (0..100), ``None`` if unknown.
        cost_hkd_cumulative: Cumulative session cost in HKD, decimal.
        raw: Optional passthrough dict from the OCPP bridge.

    Returns:
        The newly inserted :class:`SessionTelemetry` row.

    Notes:
        This function never updates an existing row — telemetry is strictly
        append-only. If the caller needs a "latest" view they should query
        with ``ORDER BY ts DESC LIMIT 1``.
    """
    frame = SessionTelemetry(
        session_id=session_id,
        ts=_now_utc(),
        kwh_cumulative=Decimal(kwh_cumulative),
        kw_instant=Decimal(kw_instant),
        soc_pct=int(soc_pct) if soc_pct is not None else None,
        cost_hkd_cumulative=Decimal(cost_hkd_cumulative),
        raw=raw or {},
    )
    db.add(frame)
    await db.flush()
    _log.debug(
        "telemetry.write session=%s kwh=%s kw=%s soc=%s cost=%s",
        session_id,
        kwh_cumulative,
        kw_instant,
        soc_pct,
        cost_hkd_cumulative,
    )
    return frame


def frame_to_wire(
    frame: SessionTelemetry | dict[str, Any],
    *,
    running_total_hkd: Decimal | None = None,
) -> dict[str, Any]:
    """Serialize a frame into the canonical WS protocol JSON shape.

    See ``docs/ARCHITECTURE.md`` §"WS protocol" for the exact envelope.
    """
    if isinstance(frame, SessionTelemetry):
        cost = Decimal(frame.cost_hkd_cumulative)
        payload: dict[str, Any] = {
            "type": "telemetry",
            "ts": frame.ts.isoformat() if frame.ts else _now_utc().isoformat(),
            "kwh_cumulative": str(Decimal(frame.kwh_cumulative)),
            "kw_instant": str(Decimal(frame.kw_instant)),
            "soc_pct": frame.soc_pct,
            "cost_hkd_cumulative": str(cost),
            "running_total_hkd": str(running_total_hkd if running_total_hkd is not None else cost),
        }
        return payload
    # dict path — used by the WS hub's synthetic frame loop.
    cost = Decimal(frame.get("cost_hkd_cumulative", "0"))
    return {
        "type": "telemetry",
        "ts": frame.get("ts") or _now_utc().isoformat(),
        "kwh_cumulative": str(Decimal(frame.get("kwh_cumulative", "0"))),
        "kw_instant": str(Decimal(frame.get("kw_instant", "0"))),
        "soc_pct": frame.get("soc_pct"),
        "cost_hkd_cumulative": str(cost),
        "running_total_hkd": str(running_total_hkd if running_total_hkd is not None else cost),
    }


async def publish_to_redis(
    redis: Any,
    *,
    session_id: uuid.UUID,
    frame_dict: dict[str, Any],
) -> int:
    """Publish a frame to the per-session Redis channel.

    Args:
        redis: A :class:`redis.asyncio.Redis` instance.
        session_id: The session id.
        frame_dict: The wire-format frame (as returned by :func:`frame_to_wire`
            or any dict matching the WS protocol).

    Returns:
        The number of subscribers that received the publish.
    """
    channel = channel_for(session_id)
    payload = json.dumps(frame_dict, default=str, ensure_ascii=False)
    count = await redis.publish(channel, payload)
    _log.debug(
        "telemetry.publish session=%s channel=%s subscribers=%s",
        session_id,
        channel,
        count,
    )
    return count


__all__ = [
    "channel_for",
    "frame_to_wire",
    "publish_to_redis",
    "write_frame",
]
