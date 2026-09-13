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

## From Agent E (web portal) — DONE

### Contract deviations (web also added 2 methods beyond ARCHITECTURE.md)

- [ ] `getSession(id)` — same as Agent D; verify Agent A endpoint exists
- [ ] `getSessions(limit?)` — GET `/api/v1/charging/sessions?limit=N`; Agent A must add this list endpoint

### Pre-ship TODOs

- [ ] `web/app/login/login-form.tsx` Apple/Google buttons POST placeholder ID tokens — wire up real OAuth flows once client IDs are provisioned
- [ ] Live telemetry WS on web — browsers can't carry HttpOnly cookies on cross-origin WS; add Next.js rewrite OR one-shot signed WS token via server action
- [ ] Top-up form UI — `POST /api/v1/wallet/topup` is wired in ApiClient; build the UI when Stripe/Apple Pay/Google Pay integrations land
- [ ] Add `/install` or App Store badge (mobile install link from web)
- [ ] Wire real geolocation for `/stations` (currently passes 0,0)
- [ ] Confirm Agent A always returns `Set-Cookie` from `/auth/login`; remove fallback that writes cookie from JSON body in `web/app/login/actions.tsx`
- [ ] Set `EVW_JWT_SECRET` in production env (middleware currently has a dev-only fallback)
- [ ] Internal admin endpoints Agent A must add: `GET /api/v1/internal/stations/list`, `GET /api/v1/wallet/admin/all-transactions`

## Type parity ✅ (validated)

```
$ diff mobile/lib/types.ts web/lib/types.ts
(identical)
```

Both 322 lines, byte-equal. The mobile+web TS contract holds.

## From Agent A (backend core) — DONE

### Pre-ship TODOs

- [ ] `/api/v1/auth/login` — full bcrypt password verify + lockout (currently returns `AUTH_LOGIN_NOT_IMPLEMENTED`)
- [ ] Production Apple JWKS — currently instantiate `PyJWKClient` lazily; pin URL + cache TTL
- [ ] `/readyz` probe Postgres write capability (currently `SELECT 1` only)
- [ ] Metrics labels for `idp_error_total` — add `domain` label

### Integration gaps from parallel fan-out — MUST FIX in console/validate

- [ ] **14 test failures in Agent B/C scope** — root cause: `AsyncSession` leaking into FastAPI `response_model`. Symptom: `FastAPIError: Invalid args for response field! ... AsyncSession is a valid Pydantic field type`. Fix: find the route handlers returning `AsyncSession` (likely in `src/evwallet/wallet/router.py` and/or `src/evwallet/charging/router.py`) and add `response_model=None` OR return a Pydantic DTO.
- [ ] Verify Agent B's `wallet/router.py` endpoints use canonical error envelope
- [ ] Verify Agent C's `charging/router.py` endpoints validate JWT properly
- [ ] Confirm `Settings.async_database_url` is used everywhere (some siblings may have hardcoded URLs)
- [ ] Shared `tests/conftest.py` was rewritten by Agent C mid-run; confirm both Agent A fixtures and Agent B/C fixtures coexist

### DB fixture strategy

- [x] SQLite in-memory via `aiosqlite` for portability (no Docker required for tests)
- [ ] Real Postgres validation — set `EVW_TEST_DATABASE_URL=postgresql+asyncpg://...` and re-run; required before shipping

## Counts

## From Agent C (charging WS + stations) — DONE

### Pre-ship TODOs

- [ ] `Settings.qr_hmac_secret` — add to config.py; currently falls back to JWT secret via `getattr`
- [ ] `Settings.async_database_url` vs `database_url` — confirm naming with Agent A
- [ ] Global `IDPError` exception handler in `main.py` — Agent C added one in test conftest only; production needs it
- [ ] `models.py` BigInteger PKs — add `.with_variant(Integer, "sqlite")` for sqlite-test compatibility
- [ ] `models.py` `func.gen_random_uuid()` — production needs `CREATE EXTENSION IF NOT EXISTS pgcrypto;` in init_db
- [ ] Idempotency race on `charging_sessions.idempotency_key` — wrap lookup+create in serializable tx
- [ ] CI lint to catch `*** ` tokens in .py files (escape-corruption seen in siblings)
- [ ] Disable synthetic telemetry loop in production; let OCPP bridge publish to Redis
- [ ] `websocket_url` in `StartSessionResponse` is relative — verify matches mobile `startSession()` return type

### Test situation

Agent C's own tests (18 tests): **all green**.
Full suite (with B's tests mid-write): **41 pass / 11 fail**.
Root cause of remaining 11: shared sqlite engine state across test files (Agent C's conftest swaps BigInteger → Integer at runtime; Agent A's tests use a different engine setup). Single fix: unify `tests/conftest.py` fixtures during console/validate.

### Files Agent C wrote (lines)

- charging/__init__.py (21), qr.py (113), telemetry.py (164), ws.py (520), router.py (407)
- stations/__init__.py (21), search.py (166), rates.py (172), router.py (290)
- tests/test_charging_ws.py (341), tests/test_stations.py (112)

### Cross-agent patches Agent C made to Agent A's files

- `src/evwallet/db/models.py` — added `JSONColumn` TypeDecorator + changed 4 JSONB/ARRAY cols (test portability)
- `src/evwallet/db/__init__.py` — re-exported `Base, get_db`
- `src/evwallet/errors.py` — appended 11 domain subclasses (AuthTokenInvalid, Station*, Charging*, etc.)
- `src/evwallet/auth/deps.py` — patched `*** | None` corruption back to `str | None`
- `src/evwallet/wallet/ledger.py` — modified (likely by Agent B mid-flight; will reconcile)