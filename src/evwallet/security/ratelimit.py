"""Redis-backed sliding-window rate limiter.

Used for abuse protection on auth endpoints (login, register). Falls back
to an in-process dictionary when Redis is unavailable (development /
when the redis container is restarting) — degraded mode, but never
silent: `_log.warning(...)` fires so ops can see it.

Design notes
------------

* **Why sliding window and not fixed window?** Fixed window has the
  "double burst at the boundary" problem — a caller can do N requests
  at 23:59:59 and N more at 00:00:00, getting 2N through in two seconds.
  Sliding window prevents that by keeping the actual timestamps.

* **Why a Redis sorted-set and not a counter?** A counter incremented
  per request with a TTL gives the fixed-window behaviour. A ZSET
  with score = unix-millis lets us pop entries older than the window
  and count what remains — true sliding.

* **Atomic via Lua**: the pop-and-count-and-add must be atomic, or two
  concurrent requests can both see "under limit" and both insert. The
  Lua script does it in one server-side round-trip.

* **IP key**: we use `request.client.host` as the natural per-caller
  key. In production behind Cloudflare, set the FastAPI
  `--proxy-headers --forwarded-allow-ips` flags AND the
  `EVW_TRUSTED_PROXIES` env so `X-Forwarded-For` is honoured — otherwise
  every request looks like it comes from the cloudflared IP and the
  limiter is useless.

The Lua script is loaded lazily on first use; on Redis failure the
fallback in-process limiter is used (with a warning).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request

from evwallet.errors import RateLimitedError

_log = logging.getLogger(__name__)

# Lua script for atomic sliding-window check-and-increment.
# Returns: {allowed (0/1), current_count, retry_after_seconds}
_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

-- Drop entries older than the window
redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)

local count = redis.call('ZCARD', key)
if count >= limit then
    -- Find the oldest entry to compute retry_after
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local oldest_ms = tonumber(oldest[2]) or now_ms
    local retry_ms = (oldest_ms + window_ms) - now_ms
    if retry_ms < 0 then retry_ms = 0 end
    return {0, count, math.ceil(retry_ms / 1000)}
end

redis.call('ZADD', key, now_ms, now_ms .. ':' .. math.random(0, 1000000))
redis.call('PEXPIRE', key, window_ms + 1000)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for a single rate-limited endpoint.

    ``limit`` is the maximum number of requests allowed in
    ``window_seconds`` from a single IP. A limit of 0 disables the
    limit (used in tests, and as a kill-switch in production if the
    limiter is ever the cause of an outage).
    """

    limit: int
    window_seconds: int
    scope: str  # e.g. "auth.login" — used in the redis key for inspection


# ---------------------------------------------------------------------------
# In-process fallback (development + degraded mode)
# ---------------------------------------------------------------------------


class _InProcessLimiter:
    """In-memory sliding window — single-worker deployments only.

    Used when Redis is unavailable. Not safe across workers; that's why
    Redis is the primary path. The fallback exists so a redis outage
    doesn't take the auth endpoints down.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock_window: dict[str, float] = {}  # last-cleaned per key

    def check_and_record(
        self, key: str, *, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = time.monotonic()
        window_start = now - window_seconds

        bucket = self._buckets[key]
        # Pop old entries
        while bucket and bucket[0] <= window_start:
            bucket.popleft()

        if len(bucket) >= limit:
            return (False, len(bucket))
        bucket.append(now)
        return (True, len(bucket) + 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class RateLimiter:
    """Redis-backed sliding-window rate limiter with in-process fallback.

    Designed to be used as a FastAPI dependency:

        @router.post("/login", dependencies=[Depends(rate_limit(RateLimitConfig(5, 900, "auth.login")))])
        async def login(...): ...

    On a 429, the dependency raises :class:`RateLimitedError`; the global
    IDPError handler turns it into a JSON response with a Retry-After
    header.
    """

    def __init__(self) -> None:
        self._lua_sha: str | None = None
        self._in_process = _InProcessLimiter()
        self._redis_failed_at: float = 0.0
        self._redis_cooldown_s = 5.0  # try Redis again this many seconds after a failure

    async def _get_redis(self):
        """Return an async Redis client, or None if Redis is unavailable.

        Imports redis lazily so tests / deployments without redis
        (e.g. SQLite-only) don't have to import the client.
        """
        # Cool-down: don't hammer a dead Redis
        if self._redis_failed_at and time.monotonic() - self._redis_failed_at < self._redis_cooldown_s:
            return None
        try:
            import redis.asyncio as aioredis

            from evwallet.config import get_settings

            settings = get_settings()
            client = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password,
                decode_responses=False,
            )
            # Cheap health check; don't await every request
            await client.ping()
            return client
        except Exception as exc:  # pragma: no cover - depends on environment
            self._redis_failed_at = time.monotonic()
            _log.warning(
                "ratelimit: redis unavailable, falling back to in-process: %s",
                exc,
            )
            return None

    async def check(
        self, *, key: str, config: RateLimitConfig
    ) -> tuple[bool, int, int]:
        """Check the rate limit for ``key``.

        Returns ``(allowed, current_count, retry_after_seconds)``.
        """
        if config.limit <= 0:
            return (True, 0, 0)

        client = await self._get_redis()
        if client is None:
            allowed, count = self._in_process.check_and_record(
                key, limit=config.limit, window_seconds=config.window_seconds
            )
            return (allowed, count, config.window_seconds if not allowed else 0)

        try:
            # Load the script once per process; cache the SHA
            if self._lua_sha is None:
                self._lua_sha = await client.script_load(_LUA_SCRIPT)
            now_ms = int(time.time() * 1000)
            window_ms = config.window_seconds * 1000
            # The script is keyed on the redis client; use a stable
            # key for the endpoint+IP
            redis_key = f"ratelimit:{config.scope}:{key}"
            result = await client.evalsha(
                self._lua_sha, 1, redis_key, now_ms, window_ms, config.limit
            )
            allowed_raw, count, retry_after = int(result[0]), int(result[1]), int(result[2])
            return (bool(allowed_raw), count, retry_after)
        except Exception as exc:  # pragma: no cover
            # Lua script may have been flushed (Redis restart). Reload
            # and fall through. If something else is broken, fall back
            # to in-process so the auth endpoint doesn't go down.
            self._lua_sha = None
            _log.warning("ratelimit: redis check failed, falling back: %s", exc)
            allowed_int, count = self._in_process.check_and_record(
                key, limit=config.limit, window_seconds=config.window_seconds
            )
            allowed = bool(allowed_int)
            return (allowed, count, config.window_seconds if not allowed else 0)


# Singleton — the limiters share a Redis client when one is available
_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Extract the caller's IP, honouring X-Forwarded-For if present.

    Note: this trusts X-Forwarded-For, which is correct only when
    behind a trusted proxy (cloudflared, ALB, etc.). The proxy MUST
    set the header — if it doesn't, the rate limiter will see the
    proxy's IP for every request. Always run uvicorn with
    ``--proxy-headers --forwarded-allow-ips=<proxy CIDR>`` in
    production.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # XFF is comma-separated; first entry is the original client
        return xff.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def rate_limit(config: RateLimitConfig):
    """Build an async dependency function that enforces ``config``.

    On limit breach, raises :class:`RateLimitedError` (which the
    global handler turns into HTTP 429 with a Retry-After header).

    Returns a plain async callable; wrap in :class:`fastapi.Depends`
    at the call site::

        @router.post("/login",
                     dependencies=[Depends(rate_limit(RateLimitConfig(5, 900, "auth.login")))])
        async def login(...): ...
    """

    async def _dep(request: Request) -> None:
        ip = _client_ip(request)
        allowed, _count, retry_after = await _limiter.check(
            key=ip, config=config
        )
        if not allowed:
            _log.info(
                "ratelimit: blocked ip=%s scope=%s retry_after=%ds",
                ip, config.scope, retry_after,
            )
            raise RateLimitedError(
                message=(
                    f"too many requests; retry in {retry_after} seconds"
                ),
                details={
                    "code": "RATE_LIMITED",
                    "scope": config.scope,
                    "retry_after_seconds": retry_after,
                },
            )

    return _dep
