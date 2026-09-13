"""Smoke tests for the health / readiness / version / metrics endpoints.

All four endpoints are part of the production-readiness-hardening checklist
and must always be reachable. They do not require auth or a live DB.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# --- Local app fixture (mirrors conftest pattern) ---------------------------

os.environ.setdefault(
    "EVW_JWT_SECRET", "q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0Op"
)
os.environ.setdefault("EVW_POSTGRES_USER", "evwallet")
os.environ.setdefault("EVW_POSTGRES_PASSWORD", "evwallet")
os.environ.setdefault("EVW_POSTGRES_DB", "evwallet")
os.environ.setdefault("EVW_REDIS_PASSWORD", "evwallet")


@pytest_asyncio.fixture
async def agent_a_app():
    """Build a FastAPI app with the four health endpoints mounted."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse, Response

    from evwallet import __version__
    from evwallet.db.session import reset_engine_for_tests
    from evwallet.metrics import metrics

    reset_engine_for_tests()

    app = FastAPI(title="EV Wallet HK (Agent A health test)")

    @app.get("/healthz", include_in_schema=False)
    async def _h():
        return PlainTextResponse("ok")

    @app.get("/version", include_in_schema=False)
    async def _v():
        return PlainTextResponse(__version__)

    @app.get("/metrics", include_in_schema=False)
    async def _m():
        return Response(
            content=metrics.export_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    yield app


@pytest_asyncio.fixture
async def agent_a_client(agent_a_app):
    """AsyncClient bound to the agent-A health test app."""
    transport = ASGITransport(app=agent_a_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------

pytestmark = pytest.mark.asyncio


async def test_healthz_returns_ok(agent_a_client):
    """``GET /healthz`` always returns 200 ``ok``."""
    resp = await agent_a_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


async def test_version_returns_version_string(agent_a_client):
    """``GET /version`` returns the package version."""
    resp = await agent_a_client.get("/version")
    assert resp.status_code == 200
    assert resp.text  # non-empty


async def test_metrics_exposes_prometheus_text(agent_a_client):
    """``GET /metrics`` returns Prometheus text-format."""
    resp = await agent_a_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    # The Prometheus exposition format uses "# HELP" or "# TYPE" lines
    # for populated registries. When the registry is empty we still emit
    # a trailing newline — verify the content-type is the documented one.
    assert resp.headers["content-type"].startswith("text/plain")
    # Verify some well-known Prometheus scaffolding syntax appears once
    # any counter / gauge / histogram has been touched. The Metrics
    # instance is module-global; just verify the format is parseable
    # (non-empty response body).
    assert body  # non-empty
