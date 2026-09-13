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

## From Agent D (mobile app) — DONE

### Contract deviations (mobile ApiClient adds 3 methods beyond ARCHITECTURE.md)

- [ ] `registerPushToken(req): Promise<Result>` — POST `/api/v1/auth/push-tokens`. Agent A must add this endpoint.
- [ ] `getSession(id): Promise<ChargingSession>` — GET `/api/v1/charging/sessions/{id}` (actually already in ARCHITECTURE.md, no new endpoint needed; just verify)
- [ ] `getSessions(limit?): Promise<ChargingSession[]>` — GET `/api/v1/charging/sessions?limit=N`. Agent A must add this paginated list endpoint.

### Pre-ship TODOs

- [ ] `mobile/assets/icon.png` + `mobile/assets/splash.png` — referenced in `app.config.ts` but currently commented out; add real PNGs and uncomment
- [ ] `mobile/app.config.ts` `extra.eas.projectId` — replace placeholder UUID with real EAS project ID
- [ ] `mobile/app.config.ts` `android.config.googleMaps.apiKey` — add Google Maps API key for Android map tile rendering
- [ ] APNs key + FCM `google-services.json` — wire through `expo-notifications` plugin before shipping
- [ ] `mobile/app/(auth)/login.tsx` — auth UX is out of scope for this PR; AuthProvider ready, screens pending
- [ ] `web/lib/types.ts` MUST mirror `mobile/lib/types.ts` byte-for-byte (Agent E) — validate with `diff`