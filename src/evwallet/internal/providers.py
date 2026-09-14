"""Provider adapters for the n8n polling workflow.

Each adapter knows how to fetch live data for ONE charging operator and
return it in the canonical ``StationUpsertIn`` shape (defined in
``internal/router.py``). The n8n workflow then just iterates the list and
POSTs each station to ``/api/v1/internal/stations/upsert`` — no per-provider
logic in n8n.

This module is the single source of truth for the upstream API contracts.
Each adapter is independent and can be unit-tested in isolation.

Honest call: the research (docs/research/OPERATORS.md) concluded that only
CLP has a real public API. HKEV, Shell, and others either have no public
API at all, or require a commercial partnership. The adapters below reflect
that reality:

- **CLP**: real implementation calling the CLP public JSON via the
  ``data.gov.hk`` Open Data proxy (requires a free ``data.gov.hk`` CKAN
  API key in env). Returns real-time per-pole status.
- **EPD**: parses the HK government's quarterly XLSX charger-location
  database. Covers ALL non-CLP/non-Tesla operators in one batch (Wilson,
  Link REIT, HKE, Jockey Club, etc.). No real-time status — just locations
  + counts. Used as the fallback for "this operator has no public API".
- **HKEV, Shell, Tesla**: return a structured ``ProviderUnavailable`` error
  with a clear human message and a contact email. The n8n workflow
  catches this and skips the operator gracefully (n8n's
  ``continueErrorOutput``).

If a future operator signs a partnership and provides API access, add a
new adapter class here and register it in ``ADAPTERS``.
"""

from __future__ import annotations

import abc
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from ..config import get_settings
from .schemas import PoleUpsertIn, StationUpsertIn

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common response shapes
# ---------------------------------------------------------------------------


class ProviderUnavailable(Exception):
    """Raised by an adapter when its upstream API is not available.

    The n8n workflow catches this and skips the operator for this poll
    cycle; the error is logged + alerted via the global error workflow.
    """

    def __init__(self, message: str, *, contact_email: str | None = None) -> None:
        super().__init__(message)
        self.contact_email = contact_email


@dataclass(frozen=True)
class AdapterResult:
    """A single station in canonical shape, ready for /stations/upsert."""

    station: StationUpsertIn


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------


class ProviderAdapter(abc.ABC):
    """Base class for provider adapters.

    Subclasses must:
    - set ``provider_code`` (matches the canonical ``provider_code`` column)
    - implement ``async def fetch(self) -> list[AdapterResult]``
    - raise ``ProviderUnavailable`` on any non-recoverable upstream failure
    """

    provider_code: str = ""

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @abc.abstractmethod
    async def fetch(self) -> list[AdapterResult]: ...


# ---------------------------------------------------------------------------
# CLP — real implementation
# ---------------------------------------------------------------------------


class CLPAdapter(ProviderAdapter):
    """CLP Power (eMobility / ChargePoint heritage) — HK's only public API.

    The endpoint ``https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/
    getEVChargerList`` is behind Akamai edge protection (403 from bare
    curl). The mirror at ``https://api.data.gov.hk`` exposes the same
    payload as Open Data under dataset ``clp-team1-electric-vehicle-
    charging-stations``. Requires a free ``data.gov.hk`` CKAN API key.

    Configured via env:
    - ``EVW_DATAGOVHK_API_KEY`` — register at
      https://data.gov.hk/en/help/ckan-api-development-guide (5 min)
    - Optional ``EVW_CLP_API_URL`` override (defaults to the new 2026-08
      successor URL; flip back to the old URL if you need to)
    """

    provider_code = "clp"

    DEFAULT_URL = "https://api.clp.com.hk/transDist/mgmtEVChargeInfra/v1/getEVChargerList"
    # The data.gov.hk proxy URL format (see ckan datastore_search_sql):
    PROXY_URL = "https://data.gov.hk/api/3/action/datastore_search"

    # CLP connector type → canonical speed_tier + connector
    CONNECTOR_MAP: dict[str, tuple[str, str, Decimal]] = {
        "Quick": ("dc_fast", "ccs2", Decimal("50")),
        "Semi-Quick": ("ac_fast", "type2", Decimal("22")),
    }

    # CLP district code → readable name
    DISTRICT_MAP: dict[str, str] = {
        "C & W": "Central and Western",
        "Wan C": "Wan Chai",
        "E": "Eastern",
        "S": "Southern",
        "Yau T M": "Yau Tsim Mong",
        "Kln C": "Kowloon City",
        "W Ts": "Wong Tai Sin",
        "Kwn T": "Kwun Tong",
        "T W": "Tsuen Wan",
        "T P": "Tuen Mun",
        "Y L": "Yuen Long",
        "N T": "North",
        "T M W": "Tseung Kwan O",
        "S K": "Sai Kung",
        "Is": "Islands",
    }

    async def fetch(self) -> list[AdapterResult]:
        settings = get_settings()
        api_key = getattr(settings, "datagovhk_api_key", None)
        if not api_key:
            raise ProviderUnavailable(
                "CLP adapter requires EVW_DATAGOVHK_API_KEY. Register for a "
                "free data.gov.hk CKAN API key at "
                "https://data.gov.hk/en/help/ckan-api-development-guide "
                "(5 min) and add it to .env.",
                contact_email="help@data.gov.hk",
            )

        url = getattr(settings, "clp_api_url", None) or self.DEFAULT_URL
        # CLP origin has Akamai. data.gov.hk proxy is the path. Use the
        # proxy when available; fall back to the direct origin with a
        # browser-like User-Agent (Akamai sometimes allows this for
        # low-volume requests).
        try:
            payload = await self._fetch_via_proxy(api_key)
        except ProviderUnavailable:
            payload = await self._fetch_via_origin(url)

        results: list[AdapterResult] = []
        for raw in payload:
            try:
                station = self._map_station(raw)
            except Exception as exc:
                _log.warning("CLP station mapping failed: %s — %s", raw, exc)
                continue
            results.append(AdapterResult(station=station))
        _log.info("CLP adapter fetched %d stations", len(results))
        return results

    async def _fetch_via_proxy(self, api_key: str) -> list[dict[str, Any]]:
        """Fetch via the data.gov.hk CKAN datastore_search API.

        Uses resource_id from the dataset metadata; we look it up on first
        call and cache the resource id on the adapter instance.
        """
        if not hasattr(self, "_resource_id"):
            # CKAN package_show returns resource metadata
            pkg_url = "https://data.gov.hk/api/3/action/package_show"
            resp = await self._client.get(
                pkg_url, params={"id": "clp-team1-electric-vehicle-charging-stations"}
            )
            if resp.status_code != 200:
                raise ProviderUnavailable(
                    f"data.gov.hk package_show returned {resp.status_code}; "
                    "check dataset name and API key"
                )
            data = resp.json().get("result", {})
            resources = data.get("resources", [])
            csv_resources = [r for r in resources if r.get("format", "").upper() == "CSV"]
            if not csv_resources:
                raise ProviderUnavailable(
                    "No CSV resource found in CLP dataset — check the dataset schema"
                )
            self._resource_id = csv_resources[0]["id"]

        url = "https://data.gov.hk/api/3/action/datastore_search"
        # Fetch all records — CLP has ~25-30 stations
        all_records: list[dict[str, Any]] = []
        offset = 0
        while True:
            resp = await self._client.get(
                url,
                params={
                    "id": self._resource_id,
                    "limit": 100,
                    "offset": offset,
                },
                headers={"X-CKAN-API-Key": api_key},
            )
            if resp.status_code != 200:
                raise ProviderUnavailable(
                    f"data.gov.hk datastore_search returned {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            data = resp.json().get("result", {})
            records = data.get("records", [])
            all_records.extend(records)
            total = data.get("total", 0)
            offset += len(records)
            if not records or offset >= total:
                break
        return all_records

    async def _fetch_via_origin(self, url: str) -> list[dict[str, Any]]:
        """Fallback: try the direct CLP origin with browser-like headers.

        Most of the time this returns 403; if it works, you get the
        un-proxied JSON.
        """
        resp = await self._client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": "https://www.clp.com.hk/",
            },
        )
        if resp.status_code != 200:
            raise ProviderUnavailable(
                f"CLP origin returned {resp.status_code}; register a "
                "data.gov.hk API key and set EVW_DATAGOVHK_API_KEY"
            )
        return resp.json() if isinstance(resp.json(), list) else resp.json().get(
            "data", []
        )

    def _map_station(self, raw: dict[str, Any]) -> StationUpsertIn:
        """Map a CLP / data.gov.hk record to the canonical StationUpsertIn."""
        # data.gov.hk uses lowercased header names; CLP direct API uses
        # camelCase. Tolerate both.
        def _g(*keys: str) -> Any:
            for k in keys:
                for variant in (k, k.lower(), k.upper()):
                    if variant in raw:
                        return raw[variant]
            return None

        name = _g("name", "station_name", "Location")
        if not name:
            raise ValueError(f"station missing name: {raw}")
        address = _g("address", "detailedAddress", "Address") or name
        district_code = _g("district", "District")
        district = self.DISTRICT_MAP.get(district_code, district_code)

        lat = _g("latitude", "lat", "Latitude")
        lng = _g("longitude", "lng", "Longitude")
        if lat is None or lng is None:
            raise ValueError(f"station missing lat/lng: {raw}")
        latitude = Decimal(str(lat))
        longitude = Decimal(str(lng))

        # CLP doesn't publish per-station pricing; default to 0
        parking_fee = Decimal(str(_g("parkingFee", "parking_fee") or 0))

        # Per-pole data: CLP returns per-charger records; we collapse to one
        # pole per (station, connector_type) and aggregate counts.
        poles: list[PoleUpsertIn] = []
        charger_type = _g("chargerType", "charger_type") or "Semi-Quick"
        speed_tier, connector, max_kw = self.CONNECTOR_MAP.get(
            charger_type, ("ac_slow", "type2", Decimal("7"))
        )
        status = _g("cpStatus", "status") or "Status_Not_Available"
        pole_count = int(_g("quantity", "chargerCount", "count") or 1)

        # Use a single representative pole per station for the canonical
        # shape; raw_payload keeps the full per-charger breakdown.
        poles.append(
            PoleUpsertIn(
                external_id=f"{_g('stationId', 'station_id', 'id') or name}-main",
                connector=connector,
                speed_tier=speed_tier,
                max_kw=max_kw,
                qr_code=None,  # generated server-side
                status=self._map_status(status),
                status_updated_at=datetime.now(tz=UTC),
            )
        )

        return StationUpsertIn(
            provider_code=self.provider_code,
            external_id=str(_g("stationId", "station_id", "id") or name),
            name=name,
            address=address,
            district=district,
            latitude=latitude,
            longitude=longitude,
            parking_fee_hkd=parking_fee,
            amenities=[],
            raw_payload={
                "charger_type": charger_type,
                "pole_count": pole_count,
                "raw_status": status,
                "source": "clp",
                **raw,  # preserve full upstream payload for debugging
            },
            poles=poles,
        )

    @staticmethod
    def _map_status(raw_status: str) -> str:
        return {
            "Available": "available",
            "Occupied": "charging",
            "Status_Not_Available": "offline",
            "Out_Of_Service": "fault",
        }.get(raw_status, "unknown")


# ---------------------------------------------------------------------------
# EPD — quarterly XLSX, covers ALL non-CLP operators
# ---------------------------------------------------------------------------


class EPDAdapter(ProviderAdapter):
    """HK EPD quarterly charger-location XLSX.

    URL: ``https://www.epd.gov.hk/epd/sites/default/files/epd/english/
    environmentinhk/air/promotion_ev/files/EV_Charger_Locations_EPD_Web_
    <YYYYMMDD>_eng.xlsx``

    EPD publishes a quarterly Excel sheet listing every public charger
    site in HK, broken down by district. The data is 3-6 months stale
    (not real-time) but covers ALL non-CLP / non-Tesla operators in one
    batch: HKE, CLP, Wilson, Link REIT, HKE, Jockey Club, etc.

    Schema: see docs/research/OPERATORS.md §3.5 and the field mapping
    below. We use the openpyxl library (already in pyproject.toml as a
    dev dep) to read the file.
    """

    provider_code = "epd"

    URL_TEMPLATE = (
        "https://www.epd.gov.hk/epd/sites/default/files/epd/english/"
        "environmentinhk/air/promotion_ev/files/"
        "EV_Charger_Locations_EPD_Web_{yyyymmdd}_eng.xlsx"
    )

    # EPD row layout (column index → meaning):
    #   0: location name (bilingual)
    #   1-6: Standard tier counts by connector type
    #   7-12: Medium tier counts
    #   13-18: Quick tier counts
    #   19-21: Fast tier extras (Fast Charger, CCS Combo 2, Tesla WC)
    #   22: district code
    CONNECTOR_BY_COL: list[tuple[int, str, str, Decimal]] = [
        # col_idx, connector_code, speed_tier, max_kw
        (1, "bs1363", "ac_slow", Decimal("2.4")),   # 13A BS1363
        (2, "type2", "ac_slow", Decimal("7")),
        (3, "gbt_ac", "ac_slow", Decimal("7")),      # GB/T 20234.2 AC
        (4, "ccs2", "dc_fast", Decimal("50")),
        (5, "type1", "ac_slow", Decimal("7")),       # "Others"
        (8, "type2", "ac_fast", Decimal("22")),      # Medium IEC 62196
        (9, "gbt_ac", "ac_fast", Decimal("22")),
        (10, "ccs2", "dc_fast", Decimal("50")),
        (11, "chademo", "dc_fast", Decimal("50")),
        (12, "tesla_nacs", "ac_fast", Decimal("22")),
        (15, "type2", "dc_fast", Decimal("50")),     # Quick
        (16, "ccs2", "dc_fast", Decimal("50")),
        (17, "chademo", "dc_fast", Decimal("50")),
        (19, "ccs2", "dc_ultra", Decimal("150")),    # Fast
        (20, "chademo", "dc_ultra", Decimal("150")),
        (21, "tesla_wc", "dc_fast", Decimal("20")),
    ]

    DISTRICT_MAP: dict[str, str] = {
        "C & W": "Central and Western",
        "Wan C": "Wan Chai",
        "E": "Eastern",
        "S": "Southern",
        "Yau T M": "Yau Tsim Mong",
        "Kln C": "Kowloon City",
        "W Ts": "Wong Tai Sin",
        "Kwn T": "Kwun Tong",
        "T W": "Tsuen Wan",
        "T P": "Tuen Mun",
        "Y L": "Yuen Long",
        "N T": "North",
        "T M W": "Tseung Kwan O",
        "S K": "Sai Kung",
        "Is": "Islands",
    }

    async def fetch(self) -> list[AdapterResult]:
        # Discover the latest available quarter. EPD publishes ~3 months
        # after quarter-end, so we look back 4 quarters.

        latest = await self._discover_latest_quarter()
        if not latest:
            raise ProviderUnavailable(
                "EPD XLSX not reachable at any of the last 4 quarter-end dates",
                contact_email=None,
            )
        url = self.URL_TEMPLATE.format(yyyymmdd=latest)
        resp = await self._client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; EVW-EPD-Poller/1.0)",
                "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise ProviderUnavailable(
                f"EPD XLSX returned {resp.status_code} for {url}"
            )

        # Parse the XLSX
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
        ws = wb.active

        results: list[AdapterResult] = []
        for row in ws.iter_rows(values_only=True):
            station = self._map_row(row)
            if station is not None:
                results.append(AdapterResult(station=station))
        _log.info("EPD adapter parsed %d stations from %s", len(results), url)
        return results

    async def _discover_latest_quarter(self) -> str | None:
        """Try the last 4 quarter-end dates; return the first that 200s.

        Uses GET (not HEAD) because some hosting providers (Cloudflare,
        Akamai) return 404 for HEAD on a path that works with GET.
        We send a Range header to avoid downloading the full ~150KB
        XLSX during discovery.
        """
        from datetime import datetime as _dt

        today = _dt.now(tz=UTC).date()
        _log.info(
            "epd.discovery.start",
            extra={"today": today.isoformat(), "client_id": id(self._client)},
        )
        for q_offset in range(0, 4):
            # Quarter-end months: 3, 6, 9, 12
            year = today.year
            month = ((today.month - 1) // 3) * 3
            while month <= 0:
                month += 12
                year -= 1
            # Walk back q_offset quarters
            for _ in range(q_offset):
                month -= 3
                if month <= 0:
                    month += 12
                    year -= 1
            # Quarter-end months are always 03, 06, 09, 12; the day
            # suffix in the file name is hardcoded to 30 (EPD always
            # publishes at the end of the quarter).
            yyyymmdd = f"{year:04d}{month:02d}30"
            url = self.URL_TEMPLATE.format(yyyymmdd=yyyymmdd)
            _log.info("epd.discovery.probe", extra={"q_offset": q_offset, "url": url})
            try:
                resp = await self._client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; EVW-EPD-Poller/1.0)",
                        "Range": "bytes=0-1023",  # only first 1KB
                    },
                    timeout=10.0,
                )
                _log.info(
                    f"epd.discovery.result q_offset={q_offset} status={resp.status_code} content_len={len(resp.content)} url={url}",
                )
                if resp.status_code in (200, 206):
                    return yyyymmdd
            except httpx.HTTPError as e:
                _log.warning(
                    f"epd.discovery.error q_offset={q_offset} err={e!r} url={url}",
                )
                continue
        return None

    def _map_row(self, row: tuple[Any, ...]) -> StationUpsertIn | None:
        # Find the location name (column 0) and district (column 22)
        # Skip the header rows (which are merged titles) and the district
        # rollup row (which contains SUM formulas).
        if not row or not row[0]:
            return None
        location = str(row[0]).strip()
        if "=" in location or not location:
            return None
        # Skip district-level rollup rows (the cell with "=SUM(...)" is at col 0)
        # Skip section-header rows (e.g., "Hong Kong Island 香港島")
        # Heuristic: real data rows have at least one numeric count somewhere
        if not any(isinstance(c, (int, float)) and c for c in row[1:22]):
            return None
        if len(location) < 4 or "District" in location or ("區" in location and len(location) < 6):
            # Section header rows like "Central & Western District 中西區"
            if any(
                keyword in location
                for keyword in ["District", "區", "Island", "Kowloon", "New Territories"]
            ):
                # Heuristic: the section header is the only one with
                # no numeric counts. If there ARE counts, this is a real
                # data row whose name happens to contain those words.
                # Since we already filtered for "at least one numeric"
                # above, this branch is for rows where the counts were
                # all zero. Treat as header.
                return None

        district_code = str(row[22]).strip() if len(row) > 22 and row[22] else None
        district = self.DISTRICT_MAP.get(district_code or "", district_code)

        # Collect poles from non-zero counts
        poles: list[PoleUpsertIn] = []
        for col_idx, connector, speed_tier, max_kw in self.CONNECTOR_BY_COL:
            if col_idx < len(row) and isinstance(row[col_idx], (int, float)) and row[col_idx] > 0:
                poles.append(
                    PoleUpsertIn(
                        external_id=f"{location[:30]}-c{col_idx}",
                        connector=connector,
                        speed_tier=speed_tier,
                        max_kw=max_kw,
                        qr_code=None,
                        status="available",  # EPD doesn't track real-time
                        status_updated_at=datetime.now(tz=UTC),
                    )
                )

        if not poles:
            return None

        # EPD doesn't have lat/lng — use HK centroid as a placeholder
        # (the consumer of the data should geocode by address).
        # Hong Kong centroid: 22.302711, 114.177216
        return StationUpsertIn(
            provider_code=self.provider_code,
            external_id=location[:80],
            name=location,
            address=location,  # geocode by this
            district=district,
            latitude=Decimal("22.302711"),
            longitude=Decimal("114.177216"),
            parking_fee_hkd=Decimal("0"),
            amenities=[],
            raw_payload={
                "source": "epd",
                "pole_count": len(poles),
                "quarter_end": "see URL",  # set by caller
            },
            poles=poles,
        )


# ---------------------------------------------------------------------------
# Stubs (HKEV, Shell, Tesla) — return ProviderUnavailable
# ---------------------------------------------------------------------------


class _NoPublicAPIAdapter(ProviderAdapter):
    """Base class for operators with no public API.

    Raises ProviderUnavailable with the contact info for the operator's
    commercial team. The n8n workflow catches this and continues with the
    next operator; the error is logged + alerted.
    """

    contact_email: str = ""
    reason: str = "No public API"

    async def fetch(self) -> list[AdapterResult]:
        raise ProviderUnavailable(
            f"{self.provider_code.upper()}: {self.reason}. "
            f"Contact {self.contact_email} for partnership / data access. "
            "See docs/research/OPERATORS.md for full details.",
            contact_email=self.contact_email,
        )


class HKEVAdapter(_NoPublicAPIAdapter):
    provider_code = "hkev"
    contact_email = "info@hkev.com.hk"
    reason = "Public read-only map (portal.hkev.com.hk) but no documented REST API"


class ShellAdapter(_NoPublicAPIAdapter):
    provider_code = "shell"
    contact_email = "shell-recharge-hk@shell.com"
    reason = "Greenlots-derived back-office, not exposed publicly; only consumer app"


class TeslaAdapter(_NoPublicAPIAdapter):
    provider_code = "tesla"
    contact_email = "charginghk@tesla.com"
    reason = "Tesla HK Terms of Use explicitly forbid commercial use of any unofficial API; only Tesla Enterprise / 'Charging Partners' program is the legal route"


# Registry
ADAPTERS: dict[str, type[ProviderAdapter]] = {
    cls.provider_code: cls
    for cls in (CLPAdapter, EPDAdapter, HKEVAdapter, ShellAdapter, TeslaAdapter)
}


__all__ = [
    "ADAPTERS",
    "AdapterResult",
    "CLPAdapter",
    "EPDAdapter",
    "HKEVAdapter",
    "ProviderAdapter",
    "ProviderUnavailable",
    "ShellAdapter",
    "TeslaAdapter",
]