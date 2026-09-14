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

## From Agent C (provisioning + bilingual docs) — NEW

These TODOs are unblocked by the operator completing the corresponding step in [`PROVISIONING.md`](PROVISIONING.md).

- [ ] **Apple Pay merchant .cer / .p8** — upload via Settings after operator finishes Step E of `PROVISIONING.md`. Currently `EVW_APPLE_PAY_MERCHANT_CERT_PATH` / `EVW_APPLE_PAY_MERCHANT_KEY_PATH` are unset; FastAPI will accept the env vars but the `apple_google.py` structural validator only runs in stub mode without them.
- [ ] **Google service account JSON** — upload via Settings after operator finishes Step F. `EVW_GOOGLE_SA_KEY_PATH` currently unset; Google Pay path is gated.
- [ ] **Stripe live keys + webhook signing secret** — paste `sk_live_***`, `pk_live_***`, and `whsec_***` into `.env` after operator finishes Step G. `topup_stripe` currently runs in stub mode when `EVW_STRIPE_SECRET_KEY` is empty (see Agent B's pre-ship TODO).
- [ ] **OCPP bridge** — defer until HK provider chosen + charger hardware available. Not in scope for first release; `Disable synthetic telemetry loop in production` (Agent C TODO) remains blocked on this.
- [ ] **Bilingual docs: remaining pages** — translate `CONSOLE_VALIDATE_TODOS.md` and any future `docs/*.md`. See [`docs/en/INDEX.md`](en/INDEX.md) for the canonical pending-translations list. Current translated set: README, ARCHITECTURE, BACKUP, PROVISIONING.

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

## From Agent B (wallet ledger) — DONE

### Verified end-to-end

- [x] 17/18 wallet tests pass (1 skipped: `test_concurrent_reserve_no_double_spend` requires real Postgres)
- [x] Smoke script proves money flows correctly:
  - topup 500 → available=500, reserved=0
  - reserve 120 → available=380, reserved=120
  - settle 12.5 kWh @ 8.40 = 105 + release 15 remainder → available=395, reserved=0
  - idempotent topup with same external_ref → same txn id
  - over-reserve raises WALLET_INSUFFICIENT_FUNDS
  - reconcile detects tampered wallet row

### Pre-ship TODOs

- [ ] Remove `tests/_wallet_setup.py` once conftest.py handles SQLite/aiosqlite natively
- [ ] Run `test_concurrent_reserve_no_double_spend` against real Postgres before shipping (validates the SELECT FOR UPDATE lock actually serializes)
- [ ] Apple Pay / Google Pay structural validators only; need real merchant certs for prod
- [ ] `evwallet.payments.stripe` runs in stub mode when `EVW_STRIPE_SECRET_KEY` unset — remove stub in prod
- [ ] `evwallet.auth.deps.current_user` is a stub (treats bearer as literal UUID); Agent A replaces with real JWT validation

### Wallet smoke script

Run: `python scripts/smoke_wallet_ledger.py` — proves the ledger invariants hold against a real (sqlite) DB.

## Console/validate plan (consolidated)

The 6-agent fan-out is complete. To close the integration gaps, console/validate will:

1. **Unify `tests/conftest.py`** — pick Agent C's strategy (JSONColumn + BigInteger Integer swap + register gen_random_uuid SQL function) as canonical. Remove Agent B's `_wallet_setup.py`. Re-run full suite; expect ~50 green.
2. **Reconcile sibling clobbers** of `errors.py`, `models.py`, `config.py`, `auth/deps.py`, `db/__init__.py`:
   - errors.py: keep Agent A's 5 core + append Agent C's 11 domain subclasses + Agent B's `InsufficientFundsError`
   - models.py: keep Agent A's 10 tables + Agent C's `JSONColumn` decorator + Agent B's sqlite patches
   - config.py: Agent A owns; verify all callers use canonical names
3. **Add `Settings.qr_hmac_secret`** to config.py (Agent C wants it)
4. **Add global `IDPError` exception handler in `main.py`** mapping to canonical error envelope
5. **Add `pyproject.toml` missing deps** (uv sync works but the optional-deps `[dev]` table is incomplete)
6. **Add agent-D endpoints** to Agent A's router if missing:
   - `POST /api/v1/auth/push-tokens`
   - `GET /api/v1/charging/sessions` (paginated list)
7. **Add agent-F internal endpoints** (referenced by n8n workflows):
   - `POST /api/v1/internal/stations/upsert`
   - `POST /api/v1/internal/rates/bulk-upsert`
   - `GET /api/v1/internal/stations/list`
   - `GET /api/v1/wallet/admin/all-transactions`
8. **Bring up docker-compose stack** (`docker compose up -d`) and `curl https://api.evwallet.com.hk/healthz`
9. **Run real Postgres tests** (`EVW_TEST_DATABASE_URL=postgresql+asyncpg://...` + `pytest`)
10. **Final commit with `[verified]` prefix**, tag v0.1.0

---

# After v0.1.0 — outstanding operator TODOs

These TODOs are unblocked by either operator signup work (in `docs/PROVISIONING.md`) or a follow-up dev turn.

## From Agent D (Stripe webhooks) — DONE, follow-ups:

- [ ] **Mount the payments router in `main.py`** — `app.include_router(payments_router, prefix="/api/v1")` next to the other `include_router` calls. Agent D avoided `main.py` per scope; the wiring is a one-liner.
- [ ] **Run `alembic upgrade head` against the real Postgres** so the `stripe_webhook_events` table exists in production (tests use SQLite + `Base.metadata.create_all`).
- [ ] **Stripe dashboard config** — set the webhook endpoint to `https://api.evwallet.com.hk/api/v1/payments/stripe/webhook` and copy the signing secret into `EVW_STRIPE_WEBHOOK_SECRET`.
- [ ] **Mobile/web PaymentIntent creation** — the client must call `stripe.paymentIntents.create({ amount, currency: 'hkd', metadata: { wallet_id } })` on Stripe's servers and pass the resulting `client_secret` to Stripe.js. The `wallet_id` metadata field is what the webhook uses to credit the right wallet. (Client-side work; not in backend scope.)

## Provisioning pre-flight:

- [ ] Run `./scripts/provision_check.sh` on the Mac mini after Steps A–G of `PROVISIONING.md` to catch missing tools, placeholder secrets, or unconfigured launchd plists before `docker compose up`. Script is read-only; exits 0 on ready, 1 on any blocking issue.

## From Agent C (provisioning + bilingual docs) — done:

- [ ] Apple Pay merchant .cer / .p8 — upload via Settings after operator finishes Step E of `PROVISIONING.md`
- [ ] Google service account JSON — upload via Settings after operator finishes Step F
- [ ] Stripe live keys + webhook signing secret — after operator finishes Step G
- [ ] OCPP bridge — defer until HK provider chosen + charger hardware available
- [ ] Bilingual docs: remaining pages (`CONSOLE_VALIDATE_TODOS`, etc.) — current translated set: README, ARCHITECTURE, BACKUP, PROVISIONING