"""REST endpoints for charging sessions.

Per ``docs/ARCHITECTURE.md`` §"Charging sessions":

* ``POST /api/v1/charging/sessions`` → 201 ``{session_id, status, ws_url}``
* ``GET  /api/v1/charging/sessions/{id}`` → session detail
* ``POST /api/v1/charging/sessions/{id}/end`` → settle + return summary
* ``WS   /api/v1/charging/sessions/{id}/stream`` → WS hub (see :mod:`.ws`)

The routes are defined at module level so FastAPI's dependency-introspection
can resolve ``Depends(current_user)`` correctly. Agent A owns ``current_user``
and may swap the implementation; we import it lazily at module top — sibling
agents' stub takes effect at test time.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import current_user
from ..config import get_settings
from ..db.models import ChargingSession, Pole, User, Wallet, WalletTransaction
from ..db.session import get_db
from ..errors import (
    ChargingInvalidQR,
    ChargingPreauthExceeded,
    ChargingSessionForbidden,
    ChargingSessionNotFound,
    PoleNotFound,
    PoleUnavailable,
)
from ..logging import get_logger
from ..wallet.reservation import end_session_settle, reserve
from .qr import verify_qr_payload

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas (public surface)
# ---------------------------------------------------------------------------


class StartSessionRequest(BaseModel):
    """Body for ``POST /api/v1/charging/sessions``."""

    qr_code: str = Field(..., description="Scanned QR payload (evwallet://...)")
    target_soc_pct: int | None = Field(
        default=None, ge=0, le=100, description="Stop charging when SOC reaches this %"
    )
    preauth_hkd: Decimal | None = Field(
        default=None, ge=0, description="Pre-auth amount in HKD; defaults to max"
    )


class StartSessionResponse(BaseModel):
    """Response body for ``POST /api/v1/charging/sessions``."""

    session_id: uuid.UUID
    status: str
    ws_url: str
    preauth_hkd: Decimal


class SessionDetail(BaseModel):
    """Response body for ``GET /api/v1/charging/sessions/{id}``."""

    session_id: uuid.UUID
    status: str
    pole_id: uuid.UUID
    target_soc_pct: int | None
    started_at: str
    ended_at: str | None
    kwh_delivered: Decimal
    running_cost_hkd: Decimal
    preauth_hkd: Decimal
    settled_hkd: Decimal | None


class EndSessionResponse(BaseModel):
    """Response body for ``POST /api/v1/charging/sessions/{id}/end``."""

    final_cost_hkd: Decimal
    kwh_delivered: Decimal
    duration_seconds: int
    transaction_id: uuid.UUID | None = None
    refunded_hkd: Decimal | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _idempotency_key(qr_code: str, user_id: uuid.UUID) -> str:
    """Derive a deterministic, idempotent key from the QR + user."""
    h = hashlib.sha256()
    h.update(str(user_id).encode())
    h.update(b"|")
    h.update(qr_code.encode())
    return h.hexdigest()


def _ws_url(session_id: uuid.UUID) -> str:
    """Build the canonical WS URL for the client.

    The mobile/web clients receive a relative URL; they can prepend the
    current origin. The path matches ``docs/ARCHITECTURE.md``.
    """
    return f"/api/v1/charging/sessions/{session_id}/stream?token={{jwt}}"


# ---------------------------------------------------------------------------
# Module-level router so FastAPI's Depends-resolution works for closures.
# ---------------------------------------------------------------------------


router = APIRouter(prefix="/charging/sessions", tags=["charging"])


@router.post(
    "",
    response_model=StartSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_session(
    body: StartSessionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],  # type: ignore[valid-type]
) -> StartSessionResponse:
    """Validate QR → reserve funds → create pending session."""
    # 1. Verify QR + extract pole id.
    try:
        pole_id_str = verify_qr_payload(body.qr_code)
    except ChargingInvalidQR:
        raise
    try:
        pole_uuid = uuid.UUID(pole_id_str)
    except ValueError as exc:
        raise ChargingInvalidQR(
            "Pole id from QR is not a UUID",
            details={"pole_id": pole_id_str},
        ) from exc

    # 2. Look up pole + verify it's operational.
    pole_result = await db.execute(select(Pole).where(Pole.id == pole_uuid))
    pole = pole_result.scalar_one_or_none()
    if pole is None:
        raise PoleNotFound(
            "Pole not found", details={"pole_id": str(pole_uuid)}
        )
    if pole.status in {"offline", "fault"}:
        raise PoleUnavailable(
            f"Pole status is {pole.status!r}",
            details={"pole_id": str(pole_uuid), "status": pole.status},
        )

    # 3. Resolve the user's wallet.
    wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user.id))
    wallet = wallet_result.scalar_one_or_none()
    if wallet is None:
        raise ChargingSessionNotFound(
            "User has no wallet", details={"user_id": str(user.id)}
        )

    # 4. Compute pre-auth amount (capped).
    settings = get_settings()
    cap = Decimal(settings.preauth_max_hkd)
    if body.preauth_hkd is None:
        preauth = cap
    else:
        preauth = min(Decimal(body.preauth_hkd), cap)
    if preauth <= 0:
        raise ChargingPreauthExceeded(
            "Pre-auth amount must be positive",
            details={"preauth_hkd": str(preauth)},
        )

    # 5. Idempotency: reject re-scans of the same QR within the pending
    # window — keeps the user from accidentally reserving twice.
    idem_key = _idempotency_key(body.qr_code, user.id)
    existing = (
        await db.execute(
            select(ChargingSession).where(ChargingSession.idempotency_key == idem_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return StartSessionResponse(
            session_id=existing.id,
            status=existing.status,
            ws_url=_ws_url(existing.id),
            preauth_hkd=Decimal(existing.preauth_hkd),
        )

    # 6. Generate a session id, reserve funds, create the session row.
    session_id = uuid.uuid4()
    try:
        await reserve(
            db,
            wallet_id=wallet.id,
            amount_hkd=preauth,
            session_id=str(session_id),
        )
    except Exception as exc:  # InsufficientFundsError from agent B, etc.
        _log.info("session.start.reserve_failed user=%s err=%s", user.id, exc)
        raise

    # Fetch the just-created txn so the FK column can be set.
    txn_row = (
        await db.execute(
            select(WalletTransaction)
            .where(WalletTransaction.wallet_id == wallet.id)
            .order_by(WalletTransaction.posted_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    session = ChargingSession(
        id=session_id,
        user_id=user.id,
        pole_id=pole.id,
        txn_reserve_id=txn_row.id if txn_row is not None else None,
        status="pending",
        target_soc_pct=body.target_soc_pct,
        started_at=datetime.now(tz=timezone.utc),
        preauth_hkd=preauth,
        idempotency_key=idem_key,
        metadata_json={},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    _log.info(
        "session.start session=%s user=%s pole=%s preauth=%s",
        session.id,
        user.id,
        pole.id,
        preauth,
    )

    return StartSessionResponse(
        session_id=session.id,
        status=session.status,
        ws_url=_ws_url(session.id),
        preauth_hkd=Decimal(session.preauth_hkd),
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_db),
) -> SessionDetail:
    """Return the session detail for the current user."""
    result = await db.execute(
        select(ChargingSession).where(ChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ChargingSessionNotFound(
            "Charging session not found", details={"session_id": str(session_id)}
        )
    if session.user_id != user.id:
        raise ChargingSessionForbidden(
            "Charging session belongs to another user",
            details={"session_id": str(session_id)},
        )
    return SessionDetail(
        session_id=session.id,
        status=session.status,
        pole_id=session.pole_id,
        target_soc_pct=session.target_soc_pct,
        started_at=session.started_at.isoformat() if session.started_at else "",
        ended_at=session.ended_at.isoformat() if session.ended_at else None,
        kwh_delivered=Decimal(session.kwh_delivered or 0),
        running_cost_hkd=Decimal(session.running_cost_hkd or 0),
        preauth_hkd=Decimal(session.preauth_hkd or 0),
        settled_hkd=Decimal(session.settled_hkd) if session.settled_hkd is not None else None,
    )


@router.post("/{session_id}/end", response_model=EndSessionResponse)
async def end_session(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_db),
) -> EndSessionResponse:
    """Force-end a session: settle the wallet and release any remainder."""
    result = await db.execute(
        select(ChargingSession).where(ChargingSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ChargingSessionNotFound(
            "Charging session not found", details={"session_id": str(session_id)}
        )
    if session.user_id != user.id:
        raise ChargingSessionForbidden(
            "Charging session belongs to another user",
            details={"session_id": str(session_id)},
        )
    if session.status in {"completed", "cancelled"}:
        return EndSessionResponse(
            final_cost_hkd=Decimal(session.settled_hkd or 0),
            kwh_delivered=Decimal(session.kwh_delivered or 0),
            duration_seconds=0,
        )

    # Resolve wallet.
    wallet_result = await db.execute(
        select(Wallet).where(Wallet.user_id == user.id)
    )
    wallet = wallet_result.scalar_one_or_none()
    if wallet is None:
        raise ChargingSessionNotFound(
            "User has no wallet", details={"user_id": str(user.id)}
        )

    # Settle via Agent B's reservation module.
    now = datetime.now(tz=timezone.utc)
    started = session.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    duration = (
        max(0, int((now - started).total_seconds())) if started is not None else 0
    )
    final_cost = Decimal(session.running_cost_hkd or 0)
    kwh = Decimal(session.kwh_delivered or 0)
    rate = (final_cost / kwh) if kwh > 0 else Decimal("9.20")
    if rate <= 0:
        rate = Decimal("9.20")
    result_settle, _release_txn = await end_session_settle(
        db,
        wallet_id=wallet.id,
        final_kwh=kwh,
        rate_hkd_per_kwh=rate,
        session_id=str(session_id),
    )
    session.status = "completed"
    session.ended_at = now
    session.settled_hkd = Decimal(result_settle.amount)
    await db.commit()

    _log.info(
        "session.end session=%s user=%s cost=%s kwh=%s duration=%s",
        session_id,
        user.id,
        final_cost,
        kwh,
        duration,
    )
    return EndSessionResponse(
        final_cost_hkd=Decimal(result_settle.amount),
        kwh_delivered=Decimal(kwh),
        duration_seconds=duration,
        transaction_id=result_settle.id,
        refunded_hkd=Decimal("0"),
    )


@router.websocket("/{session_id}/stream")
async def stream(
    websocket: Any,
    session_id: uuid.UUID,
) -> None:
    """Forward to the WS hub. The hub is responsible for JWT validation."""
    from .ws import handle_session_stream

    app = websocket.app
    redis = getattr(app.state, "redis", None)
    await handle_session_stream(websocket, session_id, redis)


def build_router() -> APIRouter:
    """Return the charging sessions router (already constructed at module load)."""
    return router


__all__ = ["build_router", "router"]
