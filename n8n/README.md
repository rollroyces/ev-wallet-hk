# EV Wallet HK — n8n workflows (Agent F)

This directory holds the n8n workflow JSON files that keep the canonical
station / pole / hourly-rate tables in sync with the three external HK EV
charging providers. They are imported into the n8n instance defined in
`../../docker-compose.yml` (image `n8nio/n8n:1.94.1`).

## Files

| File                                  | Trigger                | What it does                                                         |
|---------------------------------------|------------------------|-----------------------------------------------------------------------|
| `workflows/hkev-poll.json`            | Cron `*/30 * * * *`    | Pulls hkev.com.hk station catalogue and upserts into canonical schema. |
| `workflows/clp-poll.json`             | Cron `*/30 * * * *`    | Same shape, CLP ChargePoint.                                          |
| `workflows/shell-poll.json`           | Cron `*/30 * * * *`    | Same shape, Shell Recharge.                                           |
| `workflows/rate-normalize.json`       | Webhook POST           | On-demand rate push from providers, normalized then upserted.         |

All four workflows carry the tag `ev-wallet` so they group together in the
Admin UI.

## Data-flow contract

```
┌──────────────────┐  poll  ┌──────────────┐  /api/v1/internal/...   ┌──────────────┐
│ external provider│ ─────► │  n8n (Cron)  │ ──────────────────────► │ FastAPI      │
└──────────────────┘        │  + Code node │                          │ + Postgres   │
                            │  + HMAC QR   │ ◄────────────────────── └──────────────┘
┌──────────────────┐  push  └──────────────┘
│ external provider│ ─────────────────────►  workflow rate-normalize
└──────────────────┘        (Webhook)
```

We deliberately go **through FastAPI** rather than write directly to Postgres:

- FastAPI owns the `provider_code`/`external_id` → UUID mapping and the
  `(provider_code, external_id)` uniqueness dance.
- FastAPI logs every upsert and increments a Prometheus counter
  (`station_sync_total{provider, outcome}`) — the n8n layer stays dumb.
- Swapping providers (or layering a paid aggregator) only changes the FastAPI
  proxy endpoint, not these workflows.

The trade-off: if the FastAPI service is down, polling workflows accumulate
failures in n8n until it comes back. That is acceptable because the next
successful poll is idempotent and refreshes the data.

## Canonical shape produced

Every upsert payload mirrors `src/evwallet/db/models.py` from Agent A:

```json
{
  "station": {
    "external_id": "<provider-side id>",
    "provider_code": "hkev" | "clp" | "shell" | "tesla",
    "name": "...", "address": "...", "district": "...",
    "latitude": "22.3", "longitude": "114.2",
    "parking_fee_hkd": "0.00",
    "amenities": [],
    "raw_payload": { ...provider-native... }
  },
  "poles": [
    {
      "external_id": "<pole id>",
      "connector": "ccs2" | "type2" | "chademo" | "tesla",
      "speed_tier": "ac_slow" | "ac_fast" | "dc_fast" | "dc_ultra",
      "max_kw": "50.0",
      "qr_code": "<provider>://<POLE>-<STATION>-<HMAC>",
      "status": "unknown",
      "status_updated_at": "ISO-8601",
      "hourly_rates": [ ... ]
    }
  ],
  "replace_rates": true
}
```

## QR code format

`<provider>://<POLE_EXTERNAL_ID>-<STATION_EXTERNAL_ID>-<HMAC_SHA256_TRUNC_8>`

`<HMAC_SHA256_TRUNC_8>` is computed as
`HMAC_SHA256(QR_HMAC_SECRET, POLE_EXTERNAL_ID + ':' + STATION_EXTERNAL_ID).slice(0, 16)` —
16 hex chars = 64-bit truncation, sufficient to prevent spoofing.

The mobile app (Agent D) parses via the QR scanner in `mobile/app/scan.tsx`,
sends the raw `qr_code` string to `POST /api/v1/charging/sessions`, and the
charging module (Agent C, `src/evwallet/charging/qr.py`) recomputes the same
HMAC server-side and rejects mismatches.

## Idempotency

Every workflow is safe to re-run on the same hour — they all upsert rather
than insert, and the hourly-rate upsert key
`(pole_id, day_of_week, hour_start_local, valid_from)` ensures re-deliveries
of the same payload are no-ops on data.

## How to import these workflows into n8n

### Option A — Admin UI (recommended)

1. Sign in to your n8n instance (`https://n8n.evwallet.com.hk`).
2. Left sidebar → **Workflows** → **New** (top right) → **Import from File…**
3. Pick `workflows/hkev-poll.json` (then repeat for the other three).
4. After import, set the **environment variables** listed in
   `credentials/README.md` — n8n resolves `$env.X` at node start.
5. Toggle the workflow **Active**.
6. Repeat for each workflow.

### Option B — REST API (scripted)

n8n exposes a REST endpoint that accepts the same JSON shape on disk. From a
machine with an n8n API key:

```bash
N8N_URL=https://n8n.evwallet.com.hk
N8N_API_KEY=...   # Settings → API → Personal API Key

for f in workflows/*.json; do
  curl -fsS -X POST "$N8N_URL/api/v1/workflows" \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @"$f"
done
```

After the import, hit `POST /api/v1/workflows/<id>/activate` for each.

### Option C — CLI (n8n-cli)

```bash
npx n8n import:workflow --input=workflows/hkev-poll.json
```

## Validation

```bash
cd n8n
for f in workflows/*.json; do echo "=== $f ==="; jq empty "$f" && echo "valid JSON"; done
```

We also try `npx n8n-workflow-parser workflows/*.json` if the parser is
available — it checks a few extra shape rules specific to n8n.
