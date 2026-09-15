"""One-shot geocoder for EPD stations.

EPD's quarterly XLSX (the data source for ~800 of our stations) gives
us the building name + district but not per-station lat/lng — every
station in the sheet has the same coordinates. This module:

1. Walks every EPD station in the DB
2. Extracts the English building name from the bilingual ``address``
   field (the Chinese part is preserved for display but stripped
   for the geocode query)
3. Calls Nominatim (OpenStreetMap) to get real coordinates
4. Updates the station's ``latitude`` / ``longitude`` in place

Nominatim usage policy:
- Max 1 request / second
- Custom User-Agent identifying the app
- Bulk-geocoding is allowed but you must respect the rate limit
- Cache results in the DB (we do — once geocoded, we never re-call)

We do **not** call this on every request; it's a one-shot job that
n8n can run weekly, or that an operator can run on-demand via
``POST /api/v1/internal/stations/geocode`` (internal-token-gated).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.db.models import ChargingStation

_log = logging.getLogger(__name__)

# EPD's bilingual addresses look like: "English name 中文名稱"
# We extract just the English part for the geocode query because
# Nominatim's HK coverage is heavily English.
_CJK_TRAIL = re.compile(r"[一-鿿]+$")
_WHITESPACE = re.compile(r"\s+")


def _extract_english_name(name: str) -> str:
    """Return the English portion of a bilingual EPD station name.

    Examples:
      "The Centrium 中央廣場" -> "The Centrium"
      "Queensway Government Offices 金鐘道政府合署" -> "Queensway Government Offices"
      "Hong Kong Island 香港島" -> "Hong Kong Island"

    If the name has no CJK characters, return it as-is.
    """
    if not name:
        return ""
    s = name.strip()
    # If the name has CJK somewhere, split on the first CJK char
    m = re.search(r"[一-鿿]", s)
    if not m:
        return _WHITESPACE.sub(" ", s).strip()
    english = s[: m.start()].strip()
    # Some EPD rows have trailing punctuation we don't want
    return _WHITESPACE.sub(" ", english).strip(" ,-")


def _simplify_name(name: str) -> list[str]:
    """Return a list of progressively simpler query strings.

    EPD station names are sometimes too specific for Nominatim to
    match (e.g. "TWO IFC (International Finance Centre) - IFC II" is
    a valid string but OSM just has "Two International Finance
    Centre"). We try the full string first; if that misses, we
    progressively drop parentheticals, trailing qualifiers, "Car
    Park"/"Carpark"/"Lau" suffixes, and reduce to a 4-word lead
    noun phrase.

    Returns up to 4 candidates (most-specific first).
    """
    if not name:
        return []

    cands: list[str] = []

    # 1. As-is (full cleaned name)
    n = _WHITESPACE.sub(" ", name).strip(" ,-")
    if n:
        cands.append(n)

    # 2. Drop parentheticals: "Foo (Bar) - Baz" -> "Foo - Baz"
    n2 = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip(" ,-")
    n2 = _WHITESPACE.sub(" ", n2)
    if n2 and n2 not in cands:
        cands.append(n2)

    # 3. Drop HK-specific venue-type suffixes that OSM doesn't
    #    include in the building name. "Cheung Kong Center Car Park"
    #    in OSM is "Cheung Kong Center"; "Rumsey Street Car Park"
    #    is "Rumsey Street Multi-storey Car Park" but Nominatim
    #    often returns the street address instead. We try a
    #    suffix-stripped variant.
    suffix_pattern = (
        r"\s+(?:car\s*park|carpark|multi[-\s]?storey\s+car\s+park|carpark|lau)\s*$"
    )
    n3 = re.sub(suffix_pattern, "", n2, flags=re.IGNORECASE).strip(" ,-")
    n3 = _WHITESPACE.sub(" ", n3)
    if n3 and n3 not in cands:
        cands.append(n3)

    # 4. Take the first chunk before any " - " separator
    n4 = name.split(" - ")[0].strip(" ,-")
    n4 = _WHITESPACE.sub(" ", n4)
    n4 = re.sub(r"\s*\([^)]*\)\s*", " ", n4).strip(" ,-")
    n4 = re.sub(suffix_pattern, "", n4, flags=re.IGNORECASE).strip(" ,-")
    n4 = _WHITESPACE.sub(" ", n4)
    if n4 and n4 not in cands:
        cands.append(n4)

    # 5. First 5 words (often just the venue type — "International
    #    Finance Centre", "Disneyland", etc.)
    words = n4.split()[:5]
    n5 = " ".join(words)
    if n5 and n5 not in cands and len(n5) > 3:
        cands.append(n5)

    return cands[:5]


def _district_to_english(district: str | None) -> str:
    """EPD district codes like "C & W" or "T W" or "T P" → readable English.

    The EPD sheet uses two-token prefixes for the disambiguated
    districts (T W = Tsuen Wan, T P = Tuen Mun) and single-token
    prefixes for the rest. We match the two-token form first; if
    that doesn't hit, fall back to the single-token map.

    EPD DISTRICT_MAP is the canonical source of truth (lives in
    EPDAdapter); we mirror it here with English place-name output.
    """
    if not district:
        return ""
    # First, try the two-token form (T W, T P, K C, etc.)
    # EPD codes use either: "T W" (Tsuen Wan), "T P" (Tuen Mun),
    # "K C" (Kowloon City), "Y T M" (Yau Tsim Mong), etc. The
    # "first letter + the actual district letter" pattern.
    parts = district.split()
    # Build a key from the first letter of each part (skips the
    # Chinese trailing name)
    first_letters = "".join(p[0] for p in parts if p and p[0].isascii())
    if not first_letters:
        return ""

    # Map from first-letter(s) to readable English
    # Order matters: longer matches first
    letter_map = {
        # Two-letter codes (disambiguating Tsuen Wan vs Tuen Mun etc.)
        "CW": "Central and Western",
        "WC": "Wan Chai",
        "KC": "Kowloon City",
        "KW": "Kwun Tong",  # some sheets use this
        "KT": "Kwun Tong",
        "WT": "Wong Tai Sin",
        "TW": "Tsuen Wan",
        "TP": "Tuen Mun",
        "YL": "Yuen Long",
        "NT": "North",
        "TM": "Tseung Kwan O",
        "MW": "Tseung Kwan O",  # alt code
        "SK": "Sai Kung",
        "IS": "Islands",
        "YT": "Yau Tsim Mong",
        "SS": "Sham Shui Po",  # Sham Shui Po / 深水埗
        "TS": "Tai Po",
        # Three-letter codes
        "YTM": "Yau Tsim Mong",
        # Single-letter codes
        "C": "Central and Western",
        "E": "Eastern",
        "S": "Southern",
        "W": "Wong Tai Sin",
        "Y": "Yuen Long",
        "N": "North",
        "T": "Tsuen Wan",  # ambiguous; some sheets use just T
        "K": "Kowloon City",  # ambiguous
        "M": "Tseung Kwan O",
        "I": "Islands",
    }
    # Try longest match first
    for length in (3, 2, 1):
        if len(first_letters) >= length and first_letters[:length] in letter_map:
            return letter_map[first_letters[:length]]
    return ""


@dataclass(frozen=True)
class GeocodeResult:
    """Result of one Nominatim lookup."""

    found: bool
    latitude: float | None = None
    longitude: float | None = None
    display_name: str = ""
    osm_id: int | None = None
    raw: dict[str, Any] | None = None


def _build_query(name: str, district_english: str) -> str:
    """Build the Nominatim query string.

    We try progressively richer queries downstream (see ``geocode_one``).
    """
    parts: list[str] = [name]
    if district_english:
        parts.append(district_english)
    parts.append("Hong Kong")
    return ", ".join(parts)


async def geocode_one(
    client: httpx.AsyncClient,
    *,
    name: str,
    district: str | None,
) -> GeocodeResult:
    """Look up a single station via Nominatim.

    Strategy: one carefully-chosen query per station, with a smart
    fallback if the first miss is suspicious. We budget up to 3 HTTP
    calls per station to stay well within Nominatim's free-tier
    1 req/sec rate limit when running ``geocode_all`` on 800 rows.

    Query construction:
      1. ``"<simplified name>, <district>, Hong Kong"`` — best when
         EPD's district matches what Nominatim uses
      2. ``"<simplified name>, Hong Kong"`` — fallback when district
         is wrong or ambiguous; lets Nominatim rank by importance
         globally
      3. ``"<street address>, Hong Kong"`` — last-resort, requires
         the station to have a street-shaped name

    Caller is responsible for the per-station sleep
    (``rate_limit_seconds`` in ``geocode_all``).
    """
    english_name = _extract_english_name(name)
    if not english_name:
        return GeocodeResult(found=False, display_name="empty-name")

    district_english = _district_to_english(district)
    candidates = _simplify_name(english_name)

    # Pick the most useful single candidate. The simplified-name
    # cascade is 5 candidates; we try the most-likely-to-succeed one
    # first (with the trailing "Car Park" / "Lau" suffix stripped,
    # since OSM doesn't include those in the building name).
    primary = candidates[0] if candidates else ""
    # Use the suffix-stripped variant for the first query — most HK
    # buildings on OSM are listed without the "Car Park" suffix.
    suffix_pattern = (
        r"\s+(?:car\s*park|carpark|multi[-\s]?storey\s+car\s+park|carpark|lau)\s*$"
    )
    primary_stripped = re.sub(
        suffix_pattern, "", primary, flags=re.IGNORECASE
    ).strip(" ,-")
    if primary_stripped and primary_stripped != primary:
        primary = primary_stripped

    queries: list[str] = []
    if primary:
        if district_english:
            queries.append(_build_query(primary, district_english))
        queries.append(_build_query(primary, ""))

    last_status = "not-found"
    for q in queries[:3]:  # safety cap
        try:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 1},
                headers={
                    "User-Agent": "EVWalletHK/0.6 (https://github.com/rollroyces/ev-wallet-hk)",
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        except Exception as exc:
            _log.warning("nominatim request failed for %r: %s", q, exc)
            return GeocodeResult(found=False, display_name=f"request-error: {exc}")
        if resp.status_code != 200:
            last_status = f"http-{resp.status_code}"
            continue
        try:
            results = resp.json()
        except Exception:
            continue
        if results:
            top = results[0]
            try:
                lat = float(top["lat"])
                lon = float(top["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            return GeocodeResult(
                found=True,
                latitude=lat,
                longitude=lon,
                display_name=top.get("display_name", ""),
                osm_id=int(top.get("osm_id", 0)) or None,
                raw=top,
            )
        last_status = "not-found"

    return GeocodeResult(found=False, display_name=last_status)


async def geocode_all(
    db: AsyncSession,
    *,
    provider_code: str = "epd",
    only_unset: bool = True,
    rate_limit_seconds: float = 1.1,
) -> dict[str, int]:
    """Walk every station for ``provider_code`` and geocode.

    Args:
        db: async SQLAlchemy session
        provider_code: which provider to process (default "epd")
        only_unset: skip stations whose lat/lng already differ from
            the placeholder (22.302711, 114.1772). We use this so
            re-runs only touch the un-geocoded ones.
        rate_limit_seconds: sleep between Nominatim calls. Nominatim's
            usage policy is "absolute maximum 1 request per second";
            1.1s is the safe default. Set lower only if you have a
            commercial agreement or a local Nominatim mirror.

    Returns: dict with counts:
        {
            "scanned": N,
            "updated": N,
            "skipped": N,
            "not_found": N,
            "errors": N,
        }
    """
    PLACEHOLDER_LAT = Decimal("22.302711")
    PLACEHOLDER_LNG = Decimal("114.177216")

    stmt = select(ChargingStation).where(
        ChargingStation.provider_code == provider_code
    )
    rows = (await db.execute(stmt)).scalars().all()

    result = {
        "scanned": len(rows),
        "updated": 0,
        "skipped": 0,
        "not_found": 0,
        "errors": 0,
    }

    async with httpx.AsyncClient() as client:
        for station in rows:
            # Skip already-geocoded stations if requested
            if only_unset and (
                station.latitude != PLACEHOLDER_LAT
                or station.longitude != PLACEHOLDER_LNG
            ):
                result["skipped"] += 1
                continue

            try:
                gr = await geocode_one(
                    client, name=station.name, district=station.district
                )
            except Exception as exc:
                _log.warning("geocode exception for %r: %s", station.name, exc)
                result["errors"] += 1
                await asyncio.sleep(rate_limit_seconds)
                continue

            if gr.found and gr.latitude is not None and gr.longitude is not None:
                station.latitude = Decimal(str(gr.latitude))
                station.longitude = Decimal(str(gr.longitude))
                # Stash the osm_id in raw_payload for traceability
                rp = dict(station.raw_payload or {})
                rp["__geocode__"] = {
                    "osm_id": gr.osm_id,
                    "display_name": gr.display_name,
                }
                station.raw_payload = rp
                result["updated"] += 1
            else:
                result["not_found"] += 1
                _log.info(
                    "geocode miss: %r (%s) — %s",
                    station.name, station.district, gr.display_name,
                )

            await asyncio.sleep(rate_limit_seconds)

    await db.commit()
    return result


__all__ = [
    "GeocodeResult",
    "geocode_all",
    "geocode_one",
]
