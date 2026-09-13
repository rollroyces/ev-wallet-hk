"""WebSocket hub — live charging telemetry.

Per ``docs/ARCHITECTURE.md`` §"WS protocol":

* Server URL: ``wss://api.evwallet.com.hk/api/v1/charging/sessions/{id}/stream``
* Auth: JWT as query param ``?token=<jwt>`` — validated BEFORE accept
* Subscription: server subscribes to Redis ``charging:session:{id}`` and
  forwards each frame as JSON to the client.
* Synthetic telemetry: every 2s while the session is active, the hub
  publishes a stubbed frame (``kwh += 0.025``, ``kw_instant ≈ 47.5``,
  ``soc_pct`` ramp). In production the OCPP bridge publishes to the same
  channel.
* Client frames:
    - ``{"type": "ping"}`` → server sends ``{"type": "ping"}``
    - ``{"type": "set_target_soc", "soc_pct": 80}`` → updates
      ``session.target_soc_pct``
    - ``{"type": "end_session"}`` → settles via reservation, closes WS

Disconnect handling:
* Cancel background tasks (telemetry generator + redis listener).
* Unsubscribe from redis pubsub.
* Log connect/disconnect with the trace_id.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..auth.jwt import decode_jwt
from ..config import get_settings
from ..db.models import ChargingSession, SessionTelemetry, Wallet
from ..db.session import get_sessionmaker
from ..errors import (
    AuthTokenInvalid,
    ChargingSessionForbidden,
    ChargingSessionNotFound,
)
from ..logging import get_logger
from ..wallet.reservation import end_session_settle
from .telemetry import channel_for, publish_to_redis, write_frame

_log = get_logger(__name__)

# WebSocket close codes (RFC 6455).
_CLOSE_NORMAL = 1000
_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_GOING_AWAY = 1001
_CLOSE_INTERNAL_ERROR = 1011

# Synthetic telemetry cadence (seconds). Production will be driven by OCPP.
_TICK_SECONDS = 2.0
_TICK_KWH_DELTA = Decimal("0.025")
_TICK_KW_BASE = Decimal("47.5")


# ---------------------------------------------------------------------------
# Connection auth
# ---------------------------------------------------------------------------


async def _authenticate_ws(
    websocket: WebSocket,
    token: str | None,
) -> tuple[uuid.UUID, str]:
    """Validate the JWT BEFORE accepting the upgrade.

    Returns ``(user_id, trace_id)`` on success. Rejects with 1008 on failure.

    The caller is responsible for awaiting ``websocket.accept()`` only after
    this returns successfully.
    """
    trace_id = uuid.uuid4().hex
    if not token:
        _log.info("ws.reject reason=missing_token trace_id=%s", trace_id)
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="missing token")
        raise AuthTokenInvalid("Missing token")
    try:
        payload = decode_jwt(token)
        user_id = uuid.UUID(str(payload["sub"]))
    except (AuthTokenInvalid, KeyError, ValueError) as exc:
        _log.info("ws.reject reason=bad_token trace_id=%s err=%s", trace_id, exc)
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="invalid token")
        raise
    return user_id, trace_id


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _load_session(session_id: uuid.UUID, user_id: uuid.UUID) -> ChargingSession:
    factory = get_sessionmaker()
    async with factory() as db:
        result = await db.execute(
            select(ChargingSession).where(ChargingSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ChargingSessionNotFound(
                "Charging session not found", details={"session_id": str(session_id)}
            )
        if session.user_id != user_id:
            raise ChargingSessionForbidden(
                "Charging session belongs to another user",
                details={"session_id": str(session_id)},
            )
        return session


async def _get_user_wallet(user_id: uuid.UUID) -> Wallet:
    factory = get_sessionmaker()
    async with factory() as db:
        result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
        wallet = result.scalar_one_or_none()
        if wallet is None:
            raise ChargingSessionNotFound(
                "User has no wallet", details={"user_id": str(user_id)}
            )
        return wallet


async def _update_session(
    session_id: uuid.UUID, **fields: Any
) -> ChargingSession | None:
    factory = get_sessionmaker()
    async with factory() as db:
        result = await db.execute(
            select(ChargingSession).where(ChargingSession.id == session_id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            return None
        for key, value in fields.items():
            setattr(sess, key, value)
        await db.commit()
        await db.refresh(sess)
        return sess


# Local import for select (kept here to avoid polluting module top-level).
from sqlalchemy import desc, select  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic telemetry generator
# ---------------------------------------------------------------------------


async def _synthetic_telemetry_loop(
    websocket: WebSocket,
    redis: Any,
    session_id: uuid.UUID,
    stop_event: asyncio.Event,
) -> None:
    """Emit a stubbed telemetry frame every ``_TICK_SECONDS`` seconds.

    Replaces the OCPP bridge for dev. State is in-memory per connection.
    """
    kwh = Decimal("0")
    soc = 30
    cost_per_kwh = Decimal("9.20")  # HK EV mid-tier; good enough for a stub
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_TICK_SECONDS)
            break  # stop requested mid-wait
        except asyncio.TimeoutError:
            pass
        kwh = kwh + _TICK_KWH_DELTA
        kw = _TICK_KW_BASE + Decimal(str(random.uniform(-0.8, 0.8)))
        soc = min(100, soc + 1)
        cost = (kwh * cost_per_kwh).quantize(Decimal("0.01"))
        ts = datetime.now(tz=timezone.utc).isoformat()
        frame = {
            "type": "telemetry",
            "ts": ts,
            "kwh_cumulative": str(kwh),
            "kw_instant": str(kw.quantize(Decimal("0.01"))),
            "soc_pct": soc,
            "cost_hkd_cumulative": str(cost),
            "running_total_hkd": str(cost),
        }
        try:
            factory = get_sessionmaker()
            async with factory() as db:
                await write_frame(
                    db,
                    session_id=session_id,
                    kwh_cumulative=kwh,
                    kw_instant=kw,
                    soc_pct=soc,
                    cost_hkd_cumulative=cost,
                    raw={"source": "synthetic"},
                )
                await db.commit()
            await publish_to_redis(redis, session_id=session_id, frame_dict=frame)
        except Exception as exc:  # noqa: BLE001 — telemetry loop must not crash the WS
            _log.warning(
                "ws.telemetry.publish_failed session=%s err=%s", session_id, exc
            )
        with suppress(Exception):
            await websocket.send_json(frame)
        if soc >= 100:
            break


# ---------------------------------------------------------------------------
# Redis pub/sub listener
# ---------------------------------------------------------------------------


async def _redis_listener_loop(
    websocket: WebSocket,
    redis: Any,
    session_id: uuid.UUID,
    stop_event: asyncio.Event,
) -> None:
    """Forward frames published on the session's Redis channel to the client."""
    pubsub = redis.pubsub()
    channel = channel_for(session_id)
    try:
        await pubsub.subscribe(channel)
        _log.debug("ws.redis.subscribed session=%s channel=%s", session_id, channel)
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            data = msg.get("data")
            if not data:
                continue
            try:
                frame = json.loads(data)
            except (TypeError, ValueError):
                continue
            with suppress(Exception):
                await websocket.send_json(frame)
            if (
                frame.get("type") in {"status", "error"}
                and frame.get("status") in {"completed", "failed"}
            ):
                break
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(channel)
        with suppress(Exception):
            await pubsub.close()


# ---------------------------------------------------------------------------
# End-of-session settlement
# ---------------------------------------------------------------------------


async def _settle_session(session_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    """Call :func:`end_session_settle` and return the canonical wire summary.

    Adapts the canonical Agent B signature ``(final_kwh, rate_hkd_per_kwh)``
    to the user-facing summary fields requested in ``docs/ARCHITECTURE.md``.
    """
    factory = get_sessionmaker()
    async with factory() as db:
        result = await db.execute(
            select(ChargingSession).where(ChargingSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ChargingSessionNotFound(
                "Charging session not found", details={"session_id": str(session_id)}
            )
        if session.user_id != user_id:
            raise ChargingSessionForbidden(
                "Charging session belongs to another user",
                details={"session_id": str(session_id)},
            )
        # Compute final kWh + average rate from the latest telemetry row.
        latest = (
            await db.execute(
                select(SessionTelemetry)
                .where(SessionTelemetry.session_id == session_id)
                .order_by(desc(SessionTelemetry.ts))
                .limit(1)
            )
        ).scalar_one_or_none()
        final_kwh = (
            Decimal(latest.kwh_cumulative) if latest is not None else Decimal(session.kwh_delivered or 0)
        )
        rate = (
            Decimal(latest.cost_hkd_cumulative) / final_kwh
            if latest is not None and final_kwh > 0
            else Decimal("9.20")
        )
        if rate <= 0:
            rate = Decimal("9.20")
        # Compute duration.
        now = datetime.now(tz=timezone.utc)
        started = session.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration = max(0, int((now - started).total_seconds())) if started else 0
        # Mark session completed locally for the response.
        session.status = "completed"
        session.ended_at = now
        session.kwh_delivered = final_kwh
        await db.flush()
        # Resolve the user's wallet.
        wallet_row = (
            await db.execute(select(Wallet).where(Wallet.user_id == user_id))
        ).scalar_one_or_none()
        if wallet_row is None:
            raise ChargingSessionNotFound(
                "User has no wallet", details={"user_id": str(user_id)}
            )
        result_settle, _release_txn = await end_session_settle(
            db,
            wallet_id=wallet_row.id,
            final_kwh=final_kwh,
            rate_hkd_per_kwh=rate,
            session_id=str(session_id),
        )
        await db.commit()
        final_cost = (final_kwh * rate).quantize(Decimal("0.01"))
        return {
            "final_cost_hkd": str(final_cost),
            "kwh_delivered": str(final_kwh.quantize(Decimal("0.001"))),
            "duration_seconds": duration,
            "rate_hkd_per_kwh": str(rate.quantize(Decimal("0.01"))),
            "transaction_id": str(result_settle.id),
        }


# ---------------------------------------------------------------------------
# Main WS entrypoint
# ---------------------------------------------------------------------------


async def handle_session_stream(
    websocket: WebSocket,
    session_id: uuid.UUID,
    redis: Any,
) -> None:
    """Top-level coroutine bound to the FastAPI route.

    Validates JWT, accepts the upgrade, runs the main loop until disconnect.

    Args:
        websocket: The incoming WebSocket.
        session_id: The session id parsed from the URL.
        redis: A :class:`redis.asyncio.Redis` instance (shared pool).
    """
    token = websocket.query_params.get("token")
    user_id, trace_id = await _authenticate_ws(websocket, token)

    # Load + verify session ownership BEFORE accept.
    try:
        session = await _load_session(session_id, user_id)
    except (ChargingSessionNotFound, ChargingSessionForbidden):
        _log.info(
            "ws.reject reason=forbidden session=%s user=%s trace_id=%s",
            session_id,
            user_id,
            trace_id,
        )
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="forbidden")
        return

    await websocket.accept()
    _log.info(
        "ws.connect session=%s user=%s trace_id=%s status=%s",
        session_id,
        user_id,
        trace_id,
        session.status,
    )
    await websocket.send_json({"type": "status", "status": session.status})

    stop_event = asyncio.Event()
    gen_task = asyncio.create_task(
        _synthetic_telemetry_loop(websocket, redis, session_id, stop_event),
        name=f"ws-gen-{session_id}",
    )
    sub_task = asyncio.create_task(
        _redis_listener_loop(websocket, redis, session_id, stop_event),
        name=f"ws-redis-{session_id}",
    )

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except (json.JSONDecodeError, ValueError):
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "CHARGING_BAD_FRAME",
                        "message": "invalid json",
                    }
                )
                continue
            mtype = (msg.get("type") or "").lower()
            if mtype == "ping":
                await websocket.send_json({"type": "ping"})
                continue
            if mtype == "set_target_soc":
                target = msg.get("soc_pct")
                if isinstance(target, int) and 0 <= target <= 100:
                    sess = await _update_session(session_id, target_soc_pct=target)
                    if sess is not None:
                        await websocket.send_json({"type": "target_reached", "soc_pct": target})
                    else:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "CHARGING_SESSION_NOT_FOUND",
                                "message": "session vanished",
                            }
                        )
                else:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": "CHARGING_BAD_FRAME",
                            "message": "soc_pct must be int 0..100",
                        }
                    )
                continue
            if mtype == "end_session":
                summary = await _settle_session(session_id, user_id)
                await websocket.send_json({"type": "status", "status": "completed"})
                await websocket.send_json({"type": "session_summary", **summary})
                await websocket.close(code=_CLOSE_NORMAL)
                stop_event.set()
                break
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "CHARGING_UNKNOWN_FRAME",
                    "message": f"unknown type {mtype!r}",
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "ws.error session=%s user=%s trace_id=%s err=%s",
            session_id,
            user_id,
            trace_id,
            exc,
        )
        with suppress(Exception):
            await websocket.close(code=_CLOSE_INTERNAL_ERROR)
    finally:
        stop_event.set()
        for t in (gen_task, sub_task):
            if t and not t.done():
                t.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await t
        _log.info(
            "ws.disconnect session=%s user=%s trace_id=%s",
            session_id,
            user_id,
            trace_id,
        )


# ---------------------------------------------------------------------------
# Lifespan helpers (open / close shared Redis pool)
# ---------------------------------------------------------------------------


async def open_redis_pool() -> Any:
    """Open a shared :class:`redis.asyncio.Redis` pool.

    Called from the FastAPI lifespan handler (Agent A's main.py).
    """
    settings = get_settings()
    try:
        import redis.asyncio as redis_async
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("redis package is required") from exc
    url = _redis_url(settings)
    pool = redis_async.Redis.from_url(url, decode_responses=True, encoding="utf-8")
    with suppress(Exception):
        await pool.ping()
    return pool


def _redis_url(settings: Any) -> str:
    """Build the redis URL from Settings (Agent A doesn't expose this)."""
    if getattr(settings, "redis_url_override", None):
        return settings.redis_url_override
    password = settings.redis_password
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{settings.redis_host}:{settings.redis_port}/0"


async def close_redis_pool(redis: Any) -> None:
    """Close the shared Redis pool."""
    if redis is None:
        return
    with suppress(Exception):
        await redis.aclose()


__all__ = [
    "close_redis_pool",
    "handle_session_stream",
    "open_redis_pool",
]
