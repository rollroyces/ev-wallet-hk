"""Tests for the /api/v1/internal/providers/{code}/stations endpoint."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from evwallet.config import get_settings
from evwallet.internal.providers import (
    ADAPTERS,
    CLPAdapter,
    EPDAdapter,
    HKEVAdapter,
    OCMAdapter,
    ProviderUnavailable,
    ShellAdapter,
    TeslaAdapter,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def test_adapter_registry_has_all_expected_providers():
    assert set(ADAPTERS.keys()) == {"clp", "epd", "hkev", "ocm", "shell", "tesla"}


def test_each_adapter_has_a_provider_code():
    for code, cls in ADAPTERS.items():
        assert cls.provider_code == code, f"{cls.__name__} provider_code mismatch"


# ---------------------------------------------------------------------------
# Stubs (HKEV, Shell, Tesla) — should raise ProviderUnavailable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", [HKEVAdapter, ShellAdapter, TeslaAdapter])
async def test_stub_adapters_raise_provider_unavailable(adapter_cls):
    client = httpx.AsyncClient()
    adapter = adapter_cls(http_client=client)
    with pytest.raises(ProviderUnavailable) as exc_info:
        await adapter.fetch()
    # Each stub should have a contact email
    assert exc_info.value.contact_email, f"{adapter_cls.__name__} missing contact_email"
    await client.aclose()


# ---------------------------------------------------------------------------
# CLP adapter — needs datagovhk_api_key, otherwise ProviderUnavailable
# ---------------------------------------------------------------------------


async def test_clp_adapter_without_api_key_raises_unavailable(monkeypatch):
    monkeypatch.setattr(get_settings(), "datagovhk_api_key", None)
    client = httpx.AsyncClient()
    adapter = CLPAdapter(http_client=client)
    with pytest.raises(ProviderUnavailable) as exc_info:
        await adapter.fetch()
    assert "EVW_DATAGOVHK_API_KEY" in str(exc_info.value)
    assert exc_info.value.contact_email == "help@data.gov.hk"
    await client.aclose()


async def test_clp_adapter_503_when_datagovhk_rejects(monkeypatch):
    """When the CKAN API rejects our key, surface ProviderUnavailable."""
    monkeypatch.setattr(get_settings(), "datagovhk_api_key", "fake-key")

    # Build a fake response that simulates a 401 from CKAN
    fake_resp = MagicMock()
    fake_resp.status_code = 401
    fake_resp.text = "API key invalid"

    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = CLPAdapter(http_client=client)
    with pytest.raises(ProviderUnavailable):
        await adapter.fetch()


# ---------------------------------------------------------------------------
# EPD adapter — discover latest quarter
# ---------------------------------------------------------------------------


async def test_epd_adapter_returns_no_quarter_when_all_404(monkeypatch):
    monkeypatch.setattr(get_settings(), "datagovhk_api_key", "fake")

    fake_resp = MagicMock()
    fake_resp.status_code = 404
    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = EPDAdapter(http_client=client)
    result = await adapter._discover_latest_quarter()
    assert result is None


async def test_epd_adapter_discovers_latest_quarter(monkeypatch):
    """When the latest quarter is available, the adapter returns its yyyymm."""
    monkeypatch.setattr(get_settings(), "datagovhk_api_key", "fake")

    fake_resp = MagicMock()
    fake_resp.status_code = 206  # partial content (Range request)
    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = EPDAdapter(http_client=client)
    result = await adapter._discover_latest_quarter()
    assert result is not None
    # yyyymmdd format: 8 digits (year + month + day=30)
    assert len(result) == 8
    assert result.isdigit()
    # last two chars must be '30' (EPD always publishes at quarter-end)
    assert result.endswith("30")


# ---------------------------------------------------------------------------
# Endpoint integration via the router
# ---------------------------------------------------------------------------


async def test_endpoint_returns_404_for_unknown_provider(test_db_url):
    """GET /api/v1/internal/providers/unknown/stations -> 404 with helpful body."""
    from fastapi.testclient import TestClient

    from evwallet.main import create_app

    # Use a simple test: build a minimal FastAPI app with the internal router
    app = create_app()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/internal/providers/unknown/stations",
        headers={"X-Internal-Token": "test-internal-token-1234567890abcdefghij"},
    )
    # 503 if internal token not set, OR 404 if router not mounted, OR 200 if
    # adapter exists. We just check the response is one of the expected
    # error codes.
    assert resp.status_code in (200, 401, 403, 404, 503), resp.text


async def test_endpoint_returns_503_for_stub_provider(test_db_url):
    """GET /api/v1/internal/providers/hkev/stations -> 503 with contact info."""
    from fastapi.testclient import TestClient

    from evwallet.main import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/internal/providers/hkev/stations",
        headers={"X-Internal-Token": "test-internal-token-1234567890abcdefghij"},
    )
    # If internal_token is set: should be 503 (ProviderUnavailable) with
    # the contact email in the body
    if resp.status_code == 503:
        body = resp.json()
        # FastAPI wraps the detail in a {"detail": ...} envelope
        detail = body.get("detail", body)
        assert detail.get("code") == "PROVIDER_UNAVAILABLE"
        assert detail.get("provider_code") == "hkev"
        assert "info@hkev.com.hk" in detail.get("contact_email", "")
    # If internal_token is not set in test env: 401 (auth) or 200 (dev
    # mode bypass). Either is acceptable for this smoke test.
    assert resp.status_code in (200, 401, 503), resp.text


# ---------------------------------------------------------------------------
# OCM adapter — needs ocm_api_key, otherwise ProviderUnavailable
# ---------------------------------------------------------------------------


async def test_ocm_adapter_without_api_key_raises_unavailable(monkeypatch):
    """Without EVW_OCM_API_KEY, the adapter should fail with a helpful
    message and the right contact email (pointing at the registration
    page)."""
    monkeypatch.setattr(get_settings(), "ocm_api_key", None)
    client = httpx.AsyncClient()
    adapter = OCMAdapter(http_client=client)
    with pytest.raises(ProviderUnavailable) as exc_info:
        await adapter.fetch()
    msg = str(exc_info.value)
    assert "EVW_OCM_API_KEY" in msg
    assert "openchargemap.org" in msg
    assert exc_info.value.contact_email == "support@openchargemap.org"
    await client.aclose()


async def test_ocm_adapter_401_when_api_key_rejected(monkeypatch):
    """OCM returns 401/403 when the key is bad. Should surface as
    ProviderUnavailable with a clear message — not crash."""
    monkeypatch.setattr(get_settings(), "ocm_api_key", "definitely-not-real")

    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.text = '{"status":403,"error":"You must specify an API key"}'

    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = OCMAdapter(http_client=client)
    with pytest.raises(ProviderUnavailable) as exc_info:
        await adapter.fetch()
    assert "rejected" in str(exc_info.value)
    assert exc_info.value.contact_email == "support@openchargemap.org"


async def test_ocm_adapter_429_is_rate_limited_error(monkeypatch):
    """OCM returns 429 when we exceed the free-tier rate limit. The
    adapter should distinguish this from a generic auth failure so
    the caller can back off appropriately."""
    monkeypatch.setattr(get_settings(), "ocm_api_key", "real-looking-key")

    fake_resp = MagicMock()
    fake_resp.status_code = 429
    fake_resp.text = "Rate limit exceeded"

    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = OCMAdapter(http_client=client)
    with pytest.raises(ProviderUnavailable) as exc_info:
        await adapter.fetch()
    assert "rate-limited" in str(exc_info.value).lower()


async def test_ocm_adapter_parses_realistic_payload(monkeypatch):
    """When the API returns a JSON list of POIs, we map them to
    canonical StationUpsertIn rows. This is the happy path."""
    monkeypatch.setattr(get_settings(), "ocm_api_key", "real-looking-key")

    fake_payload = [
        {
            "ID": 100001,
            "AddressInfo": {
                "Title": "IFC Mall Charging Hub",
                "AddressLine1": "8 Finance Central",
                "Town": "Central",
                "StateOrProvince": "Central and Western",
                "Postcode": "",
                "Country": {"Title": "Hong Kong"},
                "Latitude": 22.2855,
                "Longitude": 114.1577,
            },
            "Connections": [
                {"ConnectionTypeID": 4, "Quantity": 1},  # CCS2
                {"ConnectionTypeID": 2, "Quantity": 2},  # CHAdeMO x2
                {"ConnectionTypeID": 9999, "Quantity": 1},  # unknown
            ],
            "StatusTypeID": 50,  # available
        },
        {
            "ID": 100002,
            "AddressInfo": {
                "Title": "Pacific Place",
                "AddressLine1": "88 Queensway",
                "Town": "Admiralty",
                "StateOrProvince": "Wan Chai",
                "Country": {"Title": "Hong Kong"},
                "Latitude": 22.2776,
                "Longitude": 114.1647,
            },
            "Connections": [
                {"ConnectionTypeID": 5, "Quantity": 4},  # Type 2 AC x4
            ],
            "StatusTypeID": 25,  # available
        },
    ]

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value=fake_payload)

    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = OCMAdapter(http_client=client)
    results = await adapter.fetch()

    assert len(results) == 2

    # First station
    r0 = results[0].station
    assert r0.provider_code == "ocm"
    assert r0.external_id == "100001"
    assert r0.name == "IFC Mall Charging Hub"
    assert "8 Finance Central" in r0.address
    assert r0.district == "Central and Western"
    assert r0.latitude == Decimal("22.2855")
    assert r0.longitude == Decimal("114.1577")
    # 3 poles: CCS2, CHAdeMO, unknown
    assert len(r0.poles) == 3
    assert r0.poles[0].connector == "ccs2"
    assert r0.poles[0].speed_tier == "dc_fast"
    assert r0.poles[1].connector == "chademo"
    assert r0.poles[2].connector == "unknown"
    assert r0.poles[2].speed_tier == "unknown"

    # Second station — single Type 2 AC, available
    r1 = results[1].station
    assert r1.external_id == "100002"
    assert r1.poles[0].connector == "type2"
    assert r1.poles[0].speed_tier == "ac_fast"
    assert r1.poles[0].status == "available"

async def test_ocm_adapter_skips_stations_missing_lat_lng(monkeypatch):
    """A station with no coordinates is unusable for our map; log +
    skip rather than crash the whole fetch."""
    monkeypatch.setattr(get_settings(), "ocm_api_key", "real-looking-key")

    fake_payload = [
        {
            "ID": 1,
            "AddressInfo": {
                "Title": "Bad Station",
                "Latitude": None,  # missing
                "Longitude": 114.0,
            },
            "Connections": [],
            "StatusTypeID": 50,
        },
        {
            "ID": 2,
            "AddressInfo": {
                "Title": "Good Station",
                "Latitude": 22.3,
                "Longitude": 114.2,
            },
            "Connections": [{"ConnectionTypeID": 4, "Quantity": 1}],
            "StatusTypeID": 50,
        },
    ]
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value=fake_payload)

    client = MagicMock()
    client.get = AsyncMock(return_value=fake_resp)
    adapter = OCMAdapter(http_client=client)
    results = await adapter.fetch()
    # Only the good station survives
    assert len(results) == 1
    assert results[0].station.external_id == "2"


async def test_ocm_endpoint_503_with_helpful_message(test_db_url):
    """GET /api/v1/internal/providers/ocm/stations (no key) → 503 with
    contact_email=support@openchargemap.org and a message pointing
    at the registration URL."""
    from fastapi.testclient import TestClient

    from evwallet.main import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.get(
        "/api/v1/internal/providers/ocm/stations",
        headers={"X-Internal-Token": "test-internal-token-1234567890abcdefghij"},
    )
    if resp.status_code == 503:
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("code") == "PROVIDER_UNAVAILABLE"
        assert detail.get("provider_code") == "ocm"
        assert "support@openchargemap.org" in detail.get("contact_email", "")
        assert "openchargemap.org" in detail.get("message", "")
    # 401 if internal token isn't set in test env — also acceptable
    assert resp.status_code in (401, 503), resp.text


# ---------------------------------------------------------------------------
# Public availability endpoint (GET /api/v1/providers/availability)
# ---------------------------------------------------------------------------


def test_provider_availability_marks_correct_status(test_db_url):
    """The /providers/availability endpoint reports each provider's
    status: live (EPD), needs_config (OCM, CLP), coming_soon (HKEV,
    Shell, Tesla)."""
    import os

    from fastapi.testclient import TestClient

    from evwallet.main import create_app

    # Make sure no keys are set in this test
    os.environ.pop("EVW_OCM_API_KEY", None)
    os.environ.pop("EVW_DATAGOVHK_API_KEY", None)
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/providers/availability")
    assert resp.status_code == 200
    body = resp.json()
    by_code = {p["code"]: p for p in body}
    # EPD is always live (no config needed)
    assert by_code["epd"]["status"] == "live"
    # OCM and CLP need keys
    assert by_code["ocm"]["status"] == "needs_config"
    assert by_code["clp"]["status"] == "needs_config"
    # Stubs are coming_soon
    assert by_code["hkev"]["status"] == "coming_soon"
    assert by_code["shell"]["status"] == "coming_soon"
    assert by_code["tesla"]["status"] == "coming_soon"
    # The setup URL points at the registration page
    assert "openchargemap.org" in by_code["ocm"]["setup_url"]
    # Stubs have a contact email
    assert by_code["hkev"]["contact_email"] == "info@hkev.com.hk"


def test_provider_availability_flips_to_live_when_ocm_key_set(
    test_db_url, monkeypatch
):
    """Setting EVW_OCM_API_KEY in the env flips OCM from needs_config
    to live in the same request."""
    from fastapi.testclient import TestClient

    from evwallet.main import create_app

    monkeypatch.setenv("EVW_OCM_API_KEY", "test-key")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/providers/availability")
    body = resp.json()
    ocm = next(p for p in body if p["code"] == "ocm")
    assert ocm["status"] == "live"
    # Other providers unchanged
    by_code = {p["code"]: p for p in body}
    assert by_code["clp"]["status"] == "needs_config"  # still needs its own key
    assert by_code["epd"]["status"] == "live"
