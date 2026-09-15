# Geocoding EPD stations — what this does, what it doesn't, and what's next

## Background

The EPD quarterly XLSX is a **summary sheet** — it lists buildings
and how many chargers each building has, but **every row shares the
same placeholder coordinates** (HK Central, 22.302711, 114.177216).
This is fine for a count summary but useless for a map: 800 stations
all pile up on a single pin in Central.

This module geocodes each EPD station individually using
[Nominatim](https://nominatim.org/) (OpenStreetMap's free geocoder)
and writes real lat/lng back to the row.

## Usage

```bash
# One-off: enrich a single station by name
python -c "
import asyncio, httpx
from evwallet.internal.geocoder import geocode_one
async def go():
    async with httpx.AsyncClient() as client:
        r = await geocode_one(
            client,
            name='Cyberport 數碼港',
            district='S 南區',
        )
        print(r)
asyncio.run(go())
"

# Bulk: trigger via the internal endpoint (1 HTTP call per
# station; will hit Nominatim's rate limit around 100-200 stations
# unless you run at exactly 1 req/sec and back off on 429s).
# NOT recommended for full backfills — use OCM/CLP instead.
curl -X POST http://localhost:8001/api/v1/internal/stations/geocode \
    -H "Content-Type: application/json" \
    -H "X-Internal-Token: $EVW_INTERNAL_TOKEN" \
    -d '{"provider_code": "epd", "rate_limit_seconds": 1.5}'
# Returns: {"scanned": 797, "updated": ~30, "skipped": 0,
#          "not_found": ~700, "errors": 0}
```

The endpoint IS still useful for re-running after Nominatim's
1-hour ban clears — already-geocoded rows are skipped, and
you can pick up where you left off.

## What to expect: honest match rate

Nominatim is a **free public service** with strict rate limits and
**incomplete coverage** for HK buildings. Realistic numbers from
our test runs:

| EPD entry type | Approx match rate | Notes |
|---|---|---|
| Famous landmarks (Times Square, IFC, Pacific Place) | ~95% | OSM has them as named buildings |
| Government buildings (courts, offices) | ~70% | Often in OSM, sometimes by the government name not the building name |
| Car parks ("Foo Street Car Park") | ~30% | OSM often has them as "Multi-storey car park" with a different name |
| Private residential towers | ~10% | Mostly not in OSM |
| Hong Kong Island area overall | ~50% | Better than NT, which has ~20% |

So a full backfill will leave ~40-50% of EPD stations at the
placeholder coords. The map will show real pins for the matches and
clusters at HK Central for the rest. The latter is honest about the
data we have; the alternative is making up coords.

## The real fix: OCM (or CLP), not Nominatim

This module is a **stopgap** for when you don't have OCM/CLP data.
The two better sources — both of which we have adapter code for — give
real coordinates per station:

- **Open Charge Map** — `docs/operations/OCM_SETUP.md`. Free API key,
  ~30 min of setup. OCM data has real lat/lng per POI because
  contributors physically visit the site and add it to OSM.
- **CLP Power** — `docs/operations/CLP_OUTREACH_DRAFT.md`. Sends an
  email to CLP; they have per-station coordinates in their
  eMobility backend.

Once you have either of those, the corresponding adapter bypasses
Nominatim entirely and the map just works.

## Rate limit handling

If Nominatim returns `HTTP 429`, the geocoder logs the failure and
moves to the next station (no retry — Nominatim bans for an hour
on burst). The job is safe to re-run an hour later; already-geocoded
stations are skipped.

If you need to do a full backfill without waiting:
- **Use a paid geocoder** (Google Geocoding API, Mapbox, HERE) —
  ~$5 per 1000 lookups
- **Self-host Nominatim** — heavyweight (PostgreSQL + 200GB of OSM
  planet file) but free at any volume
- **Use a different User-Agent + rotate IPs** — against Nominatim's
  ToS, don't do this

## What we extract per query

Given a row like:
```
name: "TWO IFC (International Finance Centre) - IFC II 國際金融中心二期"
district: "C & W 中西區"
```

The geocoder builds the following candidates (in order):

1. `"Two Ifc International Finance Centre Ifc Ii, Central and Western, Hong Kong"`
2. `"Two Ifc International Finance Centre Ifc Ii, Hong Kong"` (no district)
3. `"Two Ifc International Finance Centre Ifc Ii"` (just the simplified name)

For each candidate it tries up to 3 HTTP calls (district, no
district, last-resort). On a hit, it stores `lat`, `lng`, and the
`osm_id` in `raw_payload["__geocode__"]` for traceability.

Trailing suffixes like "Car Park", "Carpark", "Lau" (大廈 = building)
are stripped before the query — OSM usually lists the building
without those qualifiers.

## Files

- `src/evwallet/internal/geocoder.py` — the geocoder module
- `src/evwallet/internal/router.py::build_geocoder_router` — the
  internal endpoint
- `tests/test_geocoder.py` — unit tests for the name extraction +
  district mapping (the live HTTP loop is verified manually here)
- `src/evwallet/main.py` — wires `build_geocoder_router()` into the app
