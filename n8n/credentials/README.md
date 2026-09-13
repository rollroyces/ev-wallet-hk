# n8n credentials — EV Wallet HK

These environment variables (or n8n Credentials of the matching type) must be
configured on the n8n instance before any of the workflows in `../workflows/`
will run. They are read inside n8n via `$env.<NAME>` expressions.

## Required variables

| Variable             | What it is                                                                                              | Example value                                       |
|----------------------|----------------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| `EVW_API_BASE_URL`   | Public URL of the EV Wallet FastAPI backend (no trailing slash).                                        | `https://api.evwallet.com.hk`                        |
| `EVW_API_TOKEN`      | Bearer token accepted by the `/api/v1/internal/*` endpoints (long-lived service token, agent A owns).    | `<hex 64 chars>`                                     |
| `QR_HMAC_SECRET`     | Shared secret used to sign every QR code. The same secret must be configured on the FastAPI side.        | `<hex 32+ chars>`                                    |
| `EVW_WEBHOOK_TOKEN`  | Shared secret expected in `X-EVW-Webhook-Token` on rate-normalize webhooks.                              | `<hex 32+ chars>`                                    |
| `PROVIDER_API_KEY`   | Provider API key forwarded as `X-Provider-Key`. Set per-workflow as the correct key for that workflow.   | `<provider-issued key>`                              |

## Per-provider variables (preferred over a single `PROVIDER_API_KEY`)

If you prefer one key per provider — recommended — set these instead and edit
each workflow's `X-Provider-Key` expression to reference the right one:

| Variable           | Used by workflow                  |
|--------------------|------------------------------------|
| `HKEV_API_KEY`     | `workflows/hkev-poll.json`         |
| `CLP_API_KEY`      | `workflows/clp-poll.json`          |
| `SHELL_API_KEY`    | `workflows/shell-poll.json`        |

## Alternative: write directly to Postgres

If you would rather not depend on the FastAPI `/api/v1/internal/*` endpoints,
swap each `HTTP — POST /stations/upsert` and `HTTP — POST /rates/bulk-upsert`
node for an `n8n-nodes-base.postgres` node that runs the equivalent
`INSERT ... ON CONFLICT (provider_code, external_id) DO UPDATE ...` statement
against the schema in `src/evwallet/db/models.py`. The credentials then become:

| Variable                | What it is                                              |
|-------------------------|----------------------------------------------------------|
| `POSTGRES_DIRECT_HOST`  | Postgres host (default `postgres` from docker-compose)   |
| `POSTGRES_DIRECT_PORT`  | `5432`                                                  |
| `POSTGRES_DIRECT_USER`  | `evwallet`                                              |
| `POSTGRES_DIRECT_PASS`  | from `.env`                                             |
| `POSTGRES_DIRECT_DB`    | `evwallet`                                              |

The HTTP-via-FastAPI path is the recommended default — it keeps the n8n layer
free of SQL and lets Agent A own the upsert semantics — but the Postgres path
is fine for greenfield deployments where the FastAPI surface isn't ready yet.

## How to set the variables in n8n

1. **Docker Compose (recommended for prod)** — add the variables to
   `docker-compose.yml` under the `n8n` service's `environment:` block, then
   `docker compose up -d --force-recreate n8n`.
2. **Hosted n8n** — Settings → Variables → add each as a global variable.
3. **Local dev** — `n8n start --tunnel` reads `.env` from the working directory.
