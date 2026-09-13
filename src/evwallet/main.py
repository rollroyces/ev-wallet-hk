"""FastAPI application factory for EV Wallet HK.

Exposes:
    * :func:`create_app` — the ASGI app factory.
    * :func:`run` — console entry point invoked by ``evwallet`` CLI.

The factory wires:

1. :class:`~fastapi.middleware.cors.CORSMiddleware` with origins from
   ``EVW_CORS_ORIGINS``.
2. Trace-ID middleware — assigns a fresh ULID to every request and stores
   it on ``request.state.trace_id`` so handlers / logs / error envelopes
   can include it.
3. JWT bearer auth middleware — decodes ``Authorization: Bearer *** ``
   and stashes the payload on ``request.state.jwt_claims`` (does NOT
   enforce — that's the job of the ``current_user`` dependency, which
   also does the DB lookup).
4. A unified :class:`IDPError` exception handler that emits the
   canonical ``{error: {code, message, details, trace_id}}`` envelope.
5. Lifespan that opens / closes the SQLAlchemy engine and Redis pool.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import ulid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from evwallet import __version__
from evwallet.auth.jwt import decode_jwt
from evwallet.auth.router import router as auth_router
from evwallet.config import get_settings, settings_as_dict
from evwallet.db.session import dispose_engine, healthcheck_db
from evwallet.errors import BackendUnavailableError, IDPError
from evwallet.logging import configure as configure_logging
from evwallet.metrics import metrics

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Attach a fresh ULID ``trace_id`` to every request.

    The value is stored on ``request.state.trace_id`` and pushed onto a
    context-local that :func:`evwallet.logging._TraceIdFilter` reads. If
    the inbound request already has a ``X-Trace-Id`` header (e.g. from
    Cloudflare) we keep it for end-to-end correlation; otherwise we mint
    a new one.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get("x-trace-id")
        trace_id = incoming if incoming else str(ulid.ULID())
        request.state.trace_id = trace_id
        with _trace_id_var(trace_id):
            response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response


class _TraceIdContext:
    """Tiny contextvar holder so log records pick up the active trace_id."""

    def __init__(self) -> None:
        import contextvars

        self._var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

    def set(self, value: str) -> Any:
        return self._var.set(value)

    def reset(self, token: Any) -> None:
        self._var.reset(token)

    def get(self) -> str:
        return self._var.get()


_trace_ctx = _TraceIdContext()


class _TraceIdLoggingFilter(logging.Filter):
    """Inject the current trace_id (from contextvar) into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_ctx.get()
        return True


def _trace_id_var(value: str):  # type: ignore[no-untyped-def]
    """Context manager: push *value* onto the trace-id contextvar."""

    class _CM:
        def __enter__(self) -> None:
            self._token = _trace_ctx.set(value)

        def __exit__(self, *exc: Any) -> None:
            _trace_ctx.reset(self._token)

    return _CM()


class JwtBearerMiddleware(BaseHTTPMiddleware):
    """Decode ``Authorization: Bearer *** `` (if present) and stash claims.

    This middleware is OPTIONAL — endpoints that need auth declare
    ``Depends(current_user)``. Public endpoints (health/ready/metrics/
    auth/login) skip the decode silently.

    Decode errors here are NOT raised; they are stored on
    ``request.state.jwt_error`` so the endpoint can decide. This keeps
    public endpoints public even with a malformed header.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request.state.jwt_claims = None
        request.state.jwt_error = None
        auth_header = request.headers.get("authorization")
        if auth_header:
            parts = auth_header.strip().split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                try:
                    request.state.jwt_claims = decode_jwt(parts[1])
                except IDPError as exc:
                    request.state.jwt_error = exc
        return await call_next(request)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _idp_error_handler(request: Request, exc: IDPError) -> JSONResponse:
    """Map an :class:`IDPError` to its canonical JSON envelope.

    The HTTP status comes from ``exc.status`` (default 400; each
    subclass sets its own).
    """
    trace_id = getattr(request.state, "trace_id", None)
    envelope = exc.to_envelope(trace_id=trace_id)
    _log.warning(
        "idp_error code=%s status=%s message=%s trace_id=%s",
        exc.code,
        exc.status,
        exc.message,
        trace_id,
    )
    metrics.inc("idp_error_total", code=exc.code)
    return JSONResponse(status_code=exc.status, content=envelope)


def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler: convert anything else into a 500 envelope.

    Prevents leaking stack traces or internal error details to clients.
    """
    trace_id = getattr(request.state, "trace_id", None)
    _log.exception("unhandled error trace_id=%s type=%s", trace_id, type(exc).__name__)
    metrics.inc("unhandled_error_total")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "details": {},
                "trace_id": trace_id,
            }
        },
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open DB + Redis pools on startup; close them on shutdown.

    Also runs the initial log-line + ensures the trace-id filter is
    installed on the root logger so every log record carries the active
    trace_id.
    """
    configure_logging()
    settings = get_settings()
    _log.info(
        "evwallet starting version=%s env=%s settings=%s",
        __version__,
        settings.env,
        settings_as_dict(settings),
    )

    # Install the trace-id filter on the root logger.
    root_logger = logging.getLogger()
    if not any(isinstance(f, _TraceIdLoggingFilter) for f in root_logger.filters):
        root_logger.addFilter(_TraceIdLoggingFilter())

    # Eager DB engine creation so misconfiguration surfaces at startup.
    try:
        from evwallet.db.session import get_engine  # local import

        get_engine()
    except Exception:
        _log.exception("db engine initialisation failed")
        raise

    # Eager Redis client (lazy-create the connection pool).
    try:
        await _redis_ping()
    except BackendUnavailableError:
        _log.warning("redis ping failed at startup; will retry on /readyz")
    except Exception:
        _log.warning("redis ping unexpected error at startup; will retry on /readyz")

    try:
        yield
    finally:
        _log.info("evwallet shutting down version=%s", __version__)
        await dispose_engine()
        try:
            await _redis_close()
        except Exception:
            _log.warning("redis close failed during shutdown", exc_info=True)


async def _redis_ping() -> bool:
    """Ping the Redis backend; raise BackendUnavailableError on failure."""
    import redis.asyncio as redis

    from evwallet.config import get_settings

    settings = get_settings()
    # Default to a localhost URL when unset so the ping fails predictably
    # rather than crashing the import chain.
    redis_dsn = settings.redis_url or (
        f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/0"
    )
    client = redis.from_url(redis_dsn, decode_responses=True)
    try:
        pong = await client.ping()
        return bool(pong)
    except Exception as exc:
        raise BackendUnavailableError(f"redis unreachable: {exc}") from exc
    finally:
        await client.aclose()


async def _redis_close() -> None:
    """No-op placeholder; Redis client lifecycle is per-request.

    Kept as a named function so the lifespan finally-block can call it
    and any future module-level client can hook in cleanly.
    """
    return None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and return a fully-wired :class:`FastAPI` instance.

    Returns:
        A FastAPI app with all middleware, exception handlers, routers,
        and lifespan hooks installed.
    """
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="EV Wallet HK",
        version=__version__,
        docs_url="/api/docs" if settings.env != "production" else None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    # CORS
    origins = settings.cors_origin_list
    if origins or settings.env != "production":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins or ["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Trace-Id"],
        )

    # Custom middleware (added so TraceIdMiddleware is outermost)
    app.add_middleware(JwtBearerMiddleware)
    app.add_middleware(TraceIdMiddleware)

    # Exception handlers
    app.add_exception_handler(IDPError, _idp_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)  # type: ignore[arg-type]

    # Routers
    app.include_router(auth_router)

    # Domain routers (added during console/validate to wire all agents together)
    from evwallet.charging.router import build_router as _build_charging_router
    from evwallet.stations.router import build_router as _build_stations_router
    from evwallet.wallet.router import build_router as _build_wallet_router

    app.include_router(_build_wallet_router(), prefix="/api/v1")
    app.include_router(_build_charging_router(), prefix="/api/v1")
    app.include_router(_build_stations_router(), prefix="/api/v1")

    # Internal endpoints (n8n ingestion + admin views)
    from evwallet.internal.router import build_router as _build_internal_router

    app.include_router(_build_internal_router(), prefix="/api/v1")

    # --- Health / readiness / version / metrics -----------------------

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> PlainTextResponse:
        """Liveness probe — always returns 200 if the process is up."""
        return PlainTextResponse("ok")

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        """Readiness probe — 200 only if DB+Redis are reachable."""
        from fastapi.responses import JSONResponse as _JR

        try:
            await healthcheck_db()
            await _redis_ping()
        except IDPError as exc:
            return _JR(
                status_code=503,
                content={"status": "not_ready", "reason": exc.code},
            )
        return PlainTextResponse("ready")

    @app.get("/version", include_in_schema=False)
    async def version() -> PlainTextResponse:
        """Return the package version."""
        return PlainTextResponse(__version__)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        """Expose the in-process metrics registry in Prometheus format."""
        return Response(
            content=metrics.export_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


# Module-level ASGI app for `uvicorn evwallet.main:app` use.
app = create_app()


def run() -> None:
    """Console entry point — invoked by ``evwallet`` script.

    Reads host/port/workers from :class:`Settings` and launches uvicorn.
    """
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "evwallet.main:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.workers,
        proxy_headers=True,
        forwarded_allow_ips=",".join(settings.trusted_proxy_list) or "127.0.0.1",
        log_config=None,
    )


__all__ = ["app", "create_app", "run"]
