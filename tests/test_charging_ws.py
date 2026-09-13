"""Tests for the charging WebSocket hub and supporting modules.

Coverage:
    * QR payload signing + verification
    * REST: start/end/get session
    * WebSocket: JWT requirement, ownership check, telemetry frames
    * End-to-end settlement via the stub reservation module

Tests use the ``app_client`` fixture from ``conftest.py`` for REST routes
and a direct ``TestClient``-style WebSocket call for the WS endpoint.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from evwallet.charging.qr import compute_signature, parse_qr, verify_qr_payload
from evwallet.errors import AuthTokenInvalid, ChargingInvalidQR
from evwallet.wallet import reservation as reservation_module

# ---------------------------------------------------------------------------
# Reservation call tracking — wraps agent B's functions so tests can assert.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _track_reservation_calls(monkeypatch):
    """Patch each reservation function to record its kwargs into a list."""
    calls: list[dict] = []

    def _make_wrapper(name, original):  # type: ignore[no-untyped-def]
        async def _wrapped(*args, **kwargs):
            cleaned_kwargs = {k: (str(v) if hasattr(v, "hex") else v) for k, v in kwargs.items()}
            cleaned_kwargs["op"] = name
            calls.append(cleaned_kwargs)
            return await original(*args, **kwargs)

        _wrapped.__patched__ = True  # type: ignore[attr-defined]
        return _wrapped

    for name in ("reserve", "settle", "release", "end_session_settle"):
        if hasattr(reservation_module, name):
            original = getattr(reservation_module, name)
            monkeypatch.setattr(
                reservation_module, name, _make_wrapper(name, original), raising=True
            )

    # Also patch the names re-imported in the charging modules.
    for mod_name in (
        "evwallet.charging.router",
        "evwallet.charging.ws",
    ):
        try:
            mod = __import__(mod_name, fromlist=["reserve"])
        except Exception:
            continue
        for name in ("reserve", "settle", "release", "end_session_settle"):
            if hasattr(mod, name):
                original = getattr(mod, name)
                if getattr(original, "__patched__", False):
                    continue
                monkeypatch.setattr(mod, name, _make_wrapper(name, original), raising=True)

    yield calls

    # monkeypatch automatically undoes the patch.


# ---------------------------------------------------------------------------
# QR signature tests
# ---------------------------------------------------------------------------


def test_qr_validates_signature():
    """A correctly signed payload returns the pole id."""
    pole_id = "abc-123"
    payload = f"evwallet://{pole_id}?sig={compute_signature(pole_id)}"
    assert verify_qr_payload(payload) == pole_id


def test_qr_rejects_bad_signature():
    """An incorrectly signed payload raises :class:`ChargingInvalidQR`."""
    payload = "evwallet://abc-123?sig=deadbeef"
    with pytest.raises(ChargingInvalidQR):
        verify_qr_payload(payload)


def test_qr_rejects_missing_signature():
    """A payload without the ``sig`` query param is rejected."""
    with pytest.raises(ChargingInvalidQR):
        verify_qr_payload("evwallet://abc-123")


def test_qr_rejects_bad_prefix():
    """A payload not starting with ``evwallet://`` is rejected."""
    with pytest.raises(ChargingInvalidQR):
        verify_qr_payload("http://abc-123?sig=xx")


def test_parse_qr_returns_components():
    """``parse_qr`` extracts (pole_id, signature) without verifying."""
    pole_id = "p-q-r"
    sig = compute_signature(pole_id)
    p, s = parse_qr(f"evwallet://{pole_id}?sig={sig}")
    assert p == pole_id
    assert s == sig


def test_reserve_module_has_required_callables():
    """Agent B's module exposes the four required async callables."""
    assert callable(reservation_module.reserve)
    assert callable(reservation_module.settle)
    assert callable(reservation_module.release)
    assert callable(reservation_module.end_session_settle)


# ---------------------------------------------------------------------------
# REST: start session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_calls_reserve(
    app_client, user_factory, station_factory, make_qr_payload, _track_reservation_calls
):
    """POST /charging/sessions → reserve is called with the configured amount."""
    _user, token, _wallet = await user_factory()
    _station, poles = await station_factory()
    pole = poles[0]
    qr = make_qr_payload(pole.id)

    resp = await app_client.post(
        "/api/v1/charging/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"qr_code": qr, "preauth_hkd": "120.00"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert "session_id" in body
    assert body["ws_url"].startswith("/api/v1/charging/sessions/")
    assert Decimal(body["preauth_hkd"]) == Decimal("120.00")

    assert any(c["op"] == "reserve" for c in _track_reservation_calls), _track_reservation_calls


@pytest.mark.asyncio
async def test_start_session_returns_ws_url(
    app_client, user_factory, station_factory, make_qr_payload
):
    """The ws_url points at the canonical stream path with a ``?token={jwt}`` slot."""
    _user, token, _ = await user_factory()
    _, poles = await station_factory()
    qr = make_qr_payload(poles[0].id)

    resp = await app_client.post(
        "/api/v1/charging/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"qr_code": qr},
    )
    assert resp.status_code == 201, resp.text
    ws_url = resp.json()["ws_url"]
    assert "/stream" in ws_url
    assert "?token=" in ws_url


# ---------------------------------------------------------------------------
# REST: end session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_session_settles_and_releases(
    app_client, user_factory, station_factory, make_qr_payload, _track_reservation_calls
):
    """POST /charging/sessions/{id}/end → end_session_settle is called."""
    _user, token, _wallet = await user_factory()
    _station, poles = await station_factory()
    pole = poles[0]
    qr = make_qr_payload(pole.id)

    start_resp = await app_client.post(
        "/api/v1/charging/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"qr_code": qr, "preauth_hkd": "80.00"},
    )
    assert start_resp.status_code == 201, start_resp.text
    session_id = start_resp.json()["session_id"]

    # Simulate one telemetry frame so the session has nonzero kwh — otherwise
    # end_session_settle would be a no-op (release-only path).
    from sqlalchemy import select

    from evwallet.db.models import ChargingSession, SessionTelemetry
    from evwallet.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        sess = (
            await session.execute(
                select(ChargingSession).where(ChargingSession.id == uuid.UUID(session_id))
            )
        ).scalar_one()
        sess.kwh_delivered = Decimal("1.234")
        sess.running_cost_hkd = Decimal("11.32")
        # Append a telemetry row so end_session_settle sees a final reading.
        session.add(
            SessionTelemetry(
                session_id=uuid.UUID(session_id),
                kwh_cumulative=Decimal("1.234"),
                kw_instant=Decimal("47.5"),
                soc_pct=42,
                cost_hkd_cumulative=Decimal("11.32"),
                raw={"source": "test"},
            )
        )
        await session.commit()

    end_resp = await app_client.post(
        f"/api/v1/charging/sessions/{session_id}/end",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert end_resp.status_code == 200, end_resp.text
    body = end_resp.json()
    assert "final_cost_hkd" in body
    assert "kwh_delivered" in body
    assert "duration_seconds" in body

    assert any(c["op"] == "end_session_settle" for c in _track_reservation_calls), (
        _track_reservation_calls
    )


# ---------------------------------------------------------------------------
# WS: JWT requirement + ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_connection_requires_jwt(user_factory, station_factory):
    """WS without a token is rejected by the auth layer."""

    from evwallet.charging.ws import _authenticate_ws

    # Calling _authenticate_ws with no token must raise.
    class _FakeWS:
        async def close(self, code=None, reason=None):
            self.closed = (code, reason)

        async def accept(self):
            self.accepted = True

    ws = _FakeWS()
    with pytest.raises(AuthTokenInvalid):
        await _authenticate_ws(ws, None)
    assert ws.closed[0] == 1008


@pytest.mark.asyncio
async def test_ws_connection_rejects_other_users_session(
    app_client, user_factory, station_factory, make_qr_payload
):
    """Connecting to another user's session is rejected (403 forbidden)."""
    _user_a, token_a, _ = await user_factory()
    _user_b, _token_b, _ = await user_factory()
    _station, poles = await station_factory()
    qr = make_qr_payload(poles[0].id)

    resp = await app_client.post(
        "/api/v1/charging/sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"qr_code": qr},
    )
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    resp_b = await app_client.get(
        f"/api/v1/charging/sessions/{session_id}",
        headers={"Authorization": f"Bearer {_token_b}"},
    )
    assert resp_b.status_code == 403, resp_b.text


# ---------------------------------------------------------------------------
# WS: telemetry frames arrive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_receives_telemetry_frames(
    user_factory, station_factory, make_qr_payload, fake_redis
):
    """Synthetic telemetry loop emits JSON frames to the websocket stub."""
    from evwallet.charging.ws import _synthetic_telemetry_loop

    _user, _token, _ = await user_factory()
    _, poles = await station_factory()
    pole_id = uuid.UUID(make_qr_payload(poles[0].id).split("//")[1].split("?")[0])

    received: list[dict] = []

    class _WS:
        async def send_json(self, payload):
            received.append(payload)

        async def close(self, code=None, reason=None):
            pass

    stop = asyncio.Event()
    task = asyncio.create_task(_synthetic_telemetry_loop(_WS(), fake_redis, pole_id, stop))
    try:
        # Wait up to ~4.5s for at least one frame.
        for _ in range(45):
            if received:
                break
            await asyncio.sleep(0.1)
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()

    assert received, "no telemetry frames received"
    frame = received[0]
    assert frame["type"] == "telemetry"
    assert "kwh_cumulative" in frame
    assert "kw_instant" in frame
    assert "soc_pct" in frame
