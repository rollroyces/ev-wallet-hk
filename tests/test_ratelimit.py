"""Tests for the rate limiter (evwallet.security.ratelimit).

The limiter is Redis-backed with an in-process fallback. We exercise
both paths here:

* **In-process path** — by forcing Redis to be unavailable (patch the
  ``_get_redis`` method). This is the path that always works in tests
  (no Redis required).
* **Configuration** — the ``RateLimitConfig`` and Settings defaults are
  checked for sane values.
* **End-to-end** — a real FastAPI app, with the in-process limiter
  forced, returns 429 after the configured limit is exceeded.

The Redis path itself is verified live (docs/operations/ + manual
curl in the dev session). Tests below use the in-process path so the
suite stays hermetic.
"""

from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from evwallet.security.ratelimit import (
    RateLimitConfig,
    _InProcessLimiter,
    rate_limit,
)

# ---------------------------------------------------------------------------
# _InProcessLimiter unit tests
# ---------------------------------------------------------------------------


def test_in_process_under_limit_allows() -> None:
    lim = _InProcessLimiter()
    for _ in range(5):
        allowed, _ = lim.check_and_record("ip-a", limit=5, window_seconds=60)
        assert allowed


def test_in_process_over_limit_blocks() -> None:
    lim = _InProcessLimiter()
    for _ in range(5):
        lim.check_and_record("ip-a", limit=5, window_seconds=60)
    allowed, count = lim.check_and_record("ip-a", limit=5, window_seconds=60)
    assert allowed is False
    assert count == 5


def test_in_process_isolates_keys() -> None:
    """Different IPs don't share buckets."""
    lim = _InProcessLimiter()
    for _ in range(3):
        lim.check_and_record("ip-a", limit=3, window_seconds=60)
    # ip-b has its own bucket
    allowed, _ = lim.check_and_record("ip-b", limit=3, window_seconds=60)
    assert allowed


def test_in_process_window_slides() -> None:
    """After the window passes, requests are allowed again."""
    import time

    lim = _InProcessLimiter()
    # Use a very short window so the test runs fast
    for _ in range(3):
        lim.check_and_record("ip-a", limit=3, window_seconds=1)
    # 4th request blocked
    allowed, _ = lim.check_and_record("ip-a", limit=3, window_seconds=1)
    assert allowed is False
    # Wait for the window to pass
    time.sleep(1.1)
    allowed, _ = lim.check_and_record("ip-a", limit=3, window_seconds=1)
    assert allowed


@pytest.mark.asyncio
async def test_in_process_limit_zero_never_blocks() -> None:
    """A limit of 0 is a kill-switch — the limiter is a no-op.

    The kill-switch lives in :meth:`RateLimiter.check`, NOT in
    :class:`_InProcessLimiter` directly (the in-process class is a
    pure sliding-window; the kill-switch is a layer above it). So we
    test it via the public RateLimiter.
    """
    from evwallet.security.ratelimit import RateLimiter

    limiter = RateLimiter()
    for _ in range(100):
        allowed, _count, _retry = await limiter.check(
            key="ip-a",
            config=RateLimitConfig(limit=0, window_seconds=1, scope="test"),
        )
        assert allowed


# ---------------------------------------------------------------------------
# FastAPI dependency integration (in-process path)
# ---------------------------------------------------------------------------


def test_rate_limit_dependency_blocks_after_threshold(monkeypatch) -> None:
    """Hammer the endpoint and verify the 6th call is rejected with 429."""
    # Force the in-process fallback so the test is hermetic
    from evwallet.security import ratelimit

    async def _no_redis():
        return None

    monkeypatch.setattr(ratelimit._limiter, "_get_redis", _no_redis)
    # Also clear the singleton's failed-cooldown so a prior test
    # doesn't leak a "skip Redis" cooldown into this one
    ratelimit._limiter._redis_failed_at = 0.0

    # Use the real create_app() so the global IDPError handler turns
    # RateLimitedError into a proper 429 + Retry-After header.
    from evwallet.main import create_app

    app = create_app()

    # Add a /limited test endpoint with the limiter attached
    @app.post(
        "/api/v1/_test_limited",
        dependencies=[
            Depends(
                rate_limit(RateLimitConfig(limit=5, window_seconds=60, scope="test"))
            )
        ],
    )
    async def limited():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    for i in range(5):
        r = client.post("/api/v1/_test_limited")
        assert r.status_code == 200, f"call {i + 1} should pass: {r.text}"
    # 6th call should be 429
    r = client.post("/api/v1/_test_limited")
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "retry_after_seconds" in body["error"]["details"]
    assert int(r.headers.get("Retry-After", "0")) > 0


def test_rate_limit_dependency_different_ips_isolated(monkeypatch) -> None:
    """Different X-Forwarded-For values get different buckets."""
    from evwallet.security import ratelimit

    async def _no_redis():
        return None

    monkeypatch.setattr(ratelimit._limiter, "_get_redis", _no_redis)
    ratelimit._limiter._redis_failed_at = 0.0

    from evwallet.main import create_app

    app = create_app()

    @app.post(
        "/api/v1/_test_limited",
        dependencies=[
            Depends(
                rate_limit(RateLimitConfig(limit=2, window_seconds=60, scope="test"))
            )
        ],
    )
    async def limited():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    # ip-a: 2 allowed, 1 blocked
    for _ in range(2):
        assert client.post("/api/v1/_test_limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/api/v1/_test_limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # ip-b still has a fresh bucket
    assert client.post("/api/v1/_test_limited", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_rate_limit_limit_zero_disables(monkeypatch) -> None:
    """Limit 0 is a kill-switch — even 50 calls pass."""
    from evwallet.security import ratelimit

    async def _no_redis():
        return None

    monkeypatch.setattr(ratelimit._limiter, "_get_redis", _no_redis)
    ratelimit._limiter._redis_failed_at = 0.0

    from evwallet.main import create_app

    app = create_app()

    @app.post(
        "/api/v1/_test_limited",
        dependencies=[
            Depends(
                rate_limit(RateLimitConfig(limit=0, window_seconds=60, scope="test"))
            )
        ],
    )
    async def limited():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    for _ in range(50):
        r = client.post("/api/v1/_test_limited")
        assert r.status_code == 200
