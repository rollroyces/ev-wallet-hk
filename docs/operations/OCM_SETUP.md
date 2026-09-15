# Open Charge Map (OCM) — setup

[Open Charge Map](https://openchargemap.org) is a free, open-data
registry of EV charging locations worldwide. We use it as a second
data source alongside the HK EPD quarterly XLSX, giving us:

- **Different freshness** — OCM is updated daily by community
  submissions; EPD publishes quarterly.
- **Different operator attribution** — OCM records the network per
  POI (HKE, Shell, etc.); EPD aggregates by site name.
- **Worldwide coverage** if we ever expand beyond HK.

## Get an API key (5 minutes, free)

1. **Create an account** at
   <https://openchargemap.org/site/profile/register> (email + password).
2. **Sign in**, then go to **My Profile → My Apps** in the top-right
   menu.
3. Click **Register An Application**, fill in:
   - Name: `EV Wallet HK`
   - Description: `Local EV charging aggregator for Hong Kong drivers`
   - Website: `https://github.com/rollroyces/ev-wallet-hk`
   - Callback URL: leave blank
4. Submit. The API key is shown immediately on the confirmation page —
   copy it.
5. Set it in your `.env`:
   ```bash
   EVW_OCM_API_KEY=paste-your-key-here
   ```
6. Restart the backend (`pkill -f uvicorn` then re-launch — see
   `LOCAL_DEV.md`).
7. Verify the adapter went live:
   ```bash
   curl http://localhost:8001/api/v1/providers/availability
   ```
   The `ocm` entry's `status` should now read `"live"`.
8. Trigger an ingestion to pull OCM data into the local DB:
   ```bash
   curl -X POST http://localhost:8001/api/v1/internal/stations/upsert \
       -H "Content-Type: application/json" \
       -H "X-Internal-Token: $EVW_INTERNAL_TOKEN" \
       -d '{"stations":[],"rates":[]}'
   # First call the OCM endpoint to get the JSON, then upsert each:
   curl http://localhost:8001/api/v1/internal/providers/ocm/stations \
       -H "X-Internal-Token: $EVW_INTERNAL_TOKEN" | jq '.[]' | \
       curl -X POST http://localhost:8001/api/v1/internal/stations/upsert \
           -H "Content-Type: application/json" \
           -H "X-Internal-Token: $EVW_INTERNAL_TOKEN" \
           -d @- -d '{"stations": [], "rates": []}'
   # (The above one-liner is a sketch — the real n8n workflow handles
   # the iteration. For a one-off, write a small Python script.)
   ```

## Free-tier limits

- ~10 requests / minute
- Daily quota (resets at 00:00 UTC)

We only call the OCM API from the n8n ingestion workflow (every 6
hours), well within the limit. The 5xx / 429 paths in the adapter
turn into `ProviderUnavailable` with a clear log line; the n8n
workflow catches it and skips the cycle without alerting.

## What we extract per POI

| OCM field | Mapped to |
|---|---|
| `ID` | `external_id` (primary dedupe key) |
| `AddressInfo.Title` | `name` |
| `AddressInfo.AddressLine1` + Town + StateOrProvince | `address` |
| `AddressInfo.StateOrProvince` | `district` |
| `AddressInfo.Latitude/Longitude` | `latitude` / `longitude` |
| `Connections[].ConnectionTypeID` | `pole.connector` (via local map of 30+ types) |
| `Connections[].Quantity` | (informational only — one pole row per ConnectionInfo) |
| `StatusTypeID` | `pole.status` (available / offline / planned) |

We request `compact=true&verbose=false` to keep the payload small.
In compact mode OCM returns reference data as integer IDs (e.g.
`ConnectionTypeID: 4` for CCS2). We have a local map for the common
30+ types in `src/evwallet/internal/providers.py::OCMAdapter`.
Anything not in the map is recorded as `connector=unknown` —
the row still appears in the DB, but the operator may want to
extend the map for better attribution.

## What we DON'T extract (yet)

- **Pricing** — OCM's pricing data is sparse and inconsistently
  formatted. We skip it; the `rate` table is populated manually
  or from operator feeds.
- **Photos** — OCM POIs often have user-submitted photos. We don't
  store them. If a future "view photos" feature is needed, we can
  fetch the OCM POI page on demand.
- **Check-ins / comments** — social features, out of scope.

## Data license

OCM data is published under the
[Open Data Commons Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
You can use it freely as long as you attribute OCM as the source and
share-alike if you redistribute derived data. Our coverage card
implicitly attributes OCM via the "Open Charge Map" name; if we
later ship a public map or export, we'll add an explicit
"Data © OpenChargeMap contributors" line.
