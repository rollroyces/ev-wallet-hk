# Console / Validate TODOs — gathered from agent reports

This file aggregates open TODOs from sub-agent reports. Address during the
final integration phase.

## From Agent F (n8n workflows) — DONE

- [ ] Swap placeholder provider URLs (`api.hkev.com.hk`, `api.clp.com.hk`, `api.shell.com.hk`) with real partnership endpoints once contracts signed
- [ ] Agent A must create `POST /api/v1/internal/stations/upsert` and `POST /api/v1/internal/rates/bulk-upsert` and `/api/v1/internal/providers/{code}/stations` proxy endpoints
  - Update ARCHITECTURE.md and API.md to document these
- [ ] FastAPI must implement QR HMAC validation using `QR_HMAC_SECRET` in `src/evwallet/charging/qr.py` (Agent C) — coordinate value with `n8n/credentials/README.md`
- [ ] Register `evw-global-error-workflow` in n8n to receive all four workflows' failures
- [ ] Set `HKEV_API_KEY` / `CLP_API_KEY` / `SHELL_API_KEY` (or single `PROVIDER_API_KEY`) in n8n deployment env
- [ ] Decide `PROVIDER_CODE` source per workflow (env vs sticky note) during validate phase
- [ ] Replace vendored SHA-256 in JS Code node with proper Function node when n8n 1.94.x exposes `crypto`

## Will be appended as other agents return