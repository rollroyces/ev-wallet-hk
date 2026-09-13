# Architecture Contract — what every module must agree on

This document is the **shared contract** between all sub-agents building the EV Wallet HK system. Every module reads this and builds to the shapes defined here. If you find yourself needing to invent a new shape, **STOP** and add it to this document first, so other agents can adapt.

## Repo layout (final state)

```
ev-wallet-hk/
├── docker-compose.yml           # already shipped
├── Caddyfile                    # already shipped
├── deploy/                      # already shipped
├── docs/
│   ├── ARCHITECTURE.md          # THIS FILE (the contract)
│   ├── API.md                   # agent A owns
│   ├── BACKUP.md                # already shipped
│   └── LEDGER.md                # agent B owns
├── src/evwallet/                # all Python code lives here
│   ├── __init__.py
│   ├── main.py                  # FastAPI factory (agent A)
│   ├── config.py                # Pydantic Settings (agent A)
│   ├── logging.py               # structured logging (agent A)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py           # async engine, get_db (agent A)
│   │   └── models.py            # SQLAlchemy models (agent A — see "DB Models" below)
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── jwt.py               # encode/decode tokens (agent A)
│   │   ├── deps.py              # current_user dep (agent A)
│   │   ├── apple.py             # Apple Sign-In verifier (agent A)
│   │   ├── google.py            # Google OAuth verifier (agent A)
│   │   └── router.py            # /auth/{login,apple,google,me} (agent A)
│   ├── wallet/
│   │   ├── __init__.py
│   │   ├── ledger.py            # post_entry(), get_balance() — the CORE (agent B)
│   │   ├── topup.py             # Stripe/Apple Pay/Google Pay (agent B)
│   │   ├── reservation.py       # preauth/settle/release (agent B)
│   │   └── router.py            # /wallet/* (agent B)
│   ├── charging/
│   │   ├── __init__.py
│   │   ├── ws.py                # WebSocket hub, Redis pub/sub (agent C)
│   │   ├── telemetry.py         # session_telemetry writer (agent C)
│   │   ├── qr.py                # QR payload validation (agent C)
│   │   └── router.py            # /charging/sessions/* REST (agent C)
│   ├── stations/
│   │   ├── __init__.py
│   │   ├── search.py            # geo + filter (agent C)
│   │   ├── rates.py             # TOU rate lookup (agent C)
│   │   └── router.py            # /stations/* (agent C)
│   ├── payments/
│   │   ├── __init__.py
│   │   ├── stripe.py            # Stripe webhooks (agent B)
│   │   └── apple_google.py      # native sheet validators (agent B)
│   ├── errors.py                # IDPError hierarchy (agent A)
│   └── metrics.py               # Prometheus exporter (agent A)
├── alembic/                     # agent A owns
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py
├── tests/
│   ├── conftest.py              # shared fixtures (agent A)
│   ├── test_auth.py
│   ├── test_wallet_ledger.py    # agent B — CRITICAL
│   ├── test_stations.py
│   ├── test_charging_ws.py      # agent C
│   └── test_health.py
├── mobile/                      # agent D (Expo)
│   ├── package.json
│   ├── tsconfig.json
│   ├── app.config.ts
│   ├── app/
│   │   ├── _layout.tsx
│   │   ├── (tabs)/
│   │   │   ├── _layout.tsx
│   │   │   ├── index.tsx        # Map
│   │   │   ├── wallet.tsx
│   │   │   ├── activity.tsx
│   │   │   └── profile.tsx
│   │   ├── scan.tsx             # QR scanner
│   │   └── session/[id].tsx     # Live session monitor
│   ├── lib/
│   │   ├── api.ts               # shared API client
│   │   ├── types.ts             # shared TypeScript types
│   │   └── auth.ts              # SecureStore token mgmt
│   └── components/
│       └── ...
├── web/                         # agent E (Next.js)
│   ├── package.json
│   ├── next.config.js
│   ├── pages/
│   │   ├── _app.tsx
│   │   ├── index.tsx
│   │   ├── login.tsx
│   │   └── admin/
│   │       ├── stations.tsx
│   │       └── ledger.tsx
│   └── lib/
│       ├── api.ts
│       └── auth.ts              # HTTP-only cookie session
├── n8n/                         # agent F
│   └── workflows/
│       ├── hkev-poll.json       # HK EV Power polling
│       ├── clp-poll.json        # CLP ChargePoint polling
│       └── rate-normalize.json  # common normalizer
├── deploy/                      # already shipped (don't touch)
├── scripts/                     # already shipped (don't touch)
├── pyproject.toml               # agent A owns
└── README.md                    # already shipped
```

## Module names matter

Sub-agents MUST use these exact Python module names — `evwallet.wallet.ledger`, etc. The shared `lib/api.ts` in mobile and `lib/api.ts` in web MUST import from `mobile/lib/types.ts` and `web/lib/types.ts` respectively, which both reflect the same TypeScript shape.

## Database models (canonical SQLAlchemy 2.0)

**Source of truth: agent A**. Other agents read `src/evwallet/db/models.py` and import from there. If B/C needs a field that doesn't exist, agent A must add it; B/C does not invent fields.

Tables (in dependency order, all with `id UUID PK default gen_random_uuid()`, `created_at`, `updated_at` where natural):

### `users`
```python
class User(Base):
    id: Mapped[uuid.UUID]
    email: Mapped[str | None]  # unique, indexed
    phone_e164: Mapped[str | None]  # unique, indexed
    display_name: Mapped[str] = ""
    locale: Mapped[str] = "zh-Hant"
    is_active: Mapped[bool] = True
    is_admin: Mapped[bool] = False
    # one wallet (1:1)
```

### `social_accounts`
```python
class SocialAccount(Base):
    id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # FK users.id, indexed
    provider: Mapped[str]  # 'apple' | 'google' | 'email'
    provider_subject: Mapped[str]  # email for 'email' provider
    # unique on (provider, provider_subject)
```

### `wallets`
```python
class Wallet(Base):
    id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # FK, unique (1:1)
    available_credits: Mapped[Decimal] = 0   # HKD, Numeric(12,4)
    reserved_credits: Mapped[Decimal] = 0    # HKD, Numeric(12,4)
    currency: Mapped[str] = "HKD"
    version: Mapped[int] = 0                  # optimistic lock counter
```

### `wallet_transactions`
```python
class WalletTransaction(Base):
    id: Mapped[uuid.UUID]
    wallet_id: Mapped[uuid.UUID]  # FK
    user_id: Mapped[uuid.UUID]    # FK (denormalized for fast user-side queries)
    kind: Mapped[str]             # 'topup' | 'charge' | 'refund' | 'fee'
    status: Mapped[str] = "posted"  # 'pending' | 'posted' | 'reversed'
    amount: Mapped[Decimal]        # HKD
    currency: Mapped[str] = "HKD"
    external_ref: Mapped[str | None]  # unique, idempotency key
    description: Mapped[str] = ""
    metadata_json: Mapped[dict] = {}  # column name `metadata`
    posted_at: Mapped[datetime]
```

### `ledger_entries`
**This is the double-entry journal. The integrity invariants live here.**
```python
class LedgerEntry(Base):
    id: Mapped[int]  # BIGINT autoincrement PK (high-volume journal)
    txn_id: Mapped[uuid.UUID]  # FK wallet_transactions.id
    wallet_id: Mapped[uuid.UUID]  # FK
    entry_type: Mapped[str]  # enum (see below)
    amount: Mapped[Decimal]   # signed, HKD; SUM(amount) over a txn MUST be 0
    bucket: Mapped[str]       # 'available' | 'reserved' | 'external'
    posted_at: Mapped[datetime]
```
`entry_type` enum: `'topup_debit', 'charge_credit', 'reserve', 'release', 'settle', 'refund', 'external_clearing'`.

### `charging_stations`
```python
class ChargingStation(Base):
    id: Mapped[uuid.UUID]
    external_id: Mapped[str]
    provider_code: Mapped[str]  # 'hkev' | 'clp' | 'shell' | 'tesla'
    name: Mapped[str]
    address: Mapped[str]
    district: Mapped[str | None]
    latitude: Mapped[Decimal]
    longitude: Mapped[Decimal]
    parking_fee_hkd: Mapped[Decimal] = 0
    amenities: Mapped[list[str]] = []
    raw_payload: Mapped[dict] = {}  # column name `raw`
    last_synced_at: Mapped[datetime]
    # unique on (provider_code, external_id)
```

### `poles`
```python
class Pole(Base):
    id: Mapped[uuid.UUID]
    station_id: Mapped[uuid.UUID]  # FK
    external_id: Mapped[str]
    connector: Mapped[str]  # 'ccs2' | 'type2' | 'chademo' | 'tesla'
    speed_tier: Mapped[str]  # 'ac_slow' | 'ac_fast' | 'dc_fast' | 'dc_ultra'
    max_kw: Mapped[Decimal]
    qr_code: Mapped[str]  # unique, indexed — scanned value
    status: Mapped[str] = "unknown"  # 'unknown' | 'available' | 'charging' | 'offline' | 'fault'
    status_updated_at: Mapped[datetime]
```

### `hourly_rates`
```python
class HourlyRate(Base):
    id: Mapped[int]  # BIGINT
    pole_id: Mapped[uuid.UUID]  # FK
    day_of_week: Mapped[int]   # 0=Mon … 6=Sun
    hour_start_local: Mapped[time]
    price_per_kwh_hkd: Mapped[Decimal]
    parking_fee_hkd: Mapped[Decimal] = 0
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]
    # unique on (pole_id, day_of_week, hour_start_local, valid_from)
```

### `charging_sessions`
```python
class ChargingSession(Base):
    id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # FK
    pole_id: Mapped[uuid.UUID]  # FK
    txn_reserve_id: Mapped[uuid.UUID | None]  # FK wallet_transactions.id
    status: Mapped[str] = "pending"  # 'pending' | 'active' | 'completed' | 'failed' | 'cancelled'
    target_soc_pct: Mapped[int | None]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    kwh_delivered: Mapped[Decimal] = 0
    peak_kw: Mapped[Decimal] = 0
    running_cost_hkd: Mapped[Decimal] = 0
    preauth_hkd: Mapped[Decimal]
    settled_hkd: Mapped[Decimal | None]
    idempotency_key: Mapped[str]  # unique
    metadata_json: Mapped[dict] = {}
```

### `session_telemetry`
```python
class SessionTelemetry(Base):
    id: Mapped[int]  # BIGINT
    session_id: Mapped[uuid.UUID]  # FK
    ts: Mapped[datetime]
    kwh_cumulative: Mapped[Decimal]
    kw_instant: Mapped[Decimal]
    soc_pct: Mapped[int | None]
    cost_hkd_cumulative: Mapped[Decimal]
    raw: Mapped[dict] = {}
```

## API surface (canonical HTTP/WS endpoints)

All endpoints under `/api/v1/`. All responses are JSON unless noted. All authenticated endpoints require `Authorization: Bearer <jwt>` except where noted. JWT contains `sub: uuid`, `exp: int`, `iat: int`. Token TTL: 720h (30 days).

### Auth (agent A)
- `POST /api/v1/auth/login` body=`{email, password}` → 200 `{access_token, expires_at, user}`
- `POST /api/v1/auth/apple` body=`{identity_token, authorization_code?, full_name?, email?}` → 200 same
- `POST /api/v1/auth/google` body=`{id_token}` → 200 same
- `GET  /api/v1/auth/me` → 200 `{user, wallet: {available_hkd, reserved_hkd}}`

### Wallet (agent B)
- `GET  /api/v1/wallet` → `{available_hkd, reserved_hkd, currency, recent_transactions: [...]}`
- `POST /api/v1/wallet/topup` body=`{amount_hkd, source: 'stripe'|'apple_pay'|'google_pay', source_payload}` → 200 `{transaction_id, status: 'pending'|'posted'}`
- `GET  /api/v1/wallet/transactions?limit=20&cursor=...` → `{transactions: [...], next_cursor}`
- `GET  /api/v1/wallet/balance` → `{available_hkd, reserved_hkd}`

### Stations (agent C)
- `GET  /api/v1/stations?lat=22.3&lng=114.2&radius_km=5&connector=ccs2&min_kw=50` → `{stations: [...], total}`
- `GET  /api/v1/stations/{id}` → full station + poles + next-24h rates
- `GET  /api/v1/stations/{id}/rates?date=2026-09-13` → 24-hour TOU window

### Charging sessions (agent C)
- `POST /api/v1/charging/sessions` body=`{qr_code, target_soc_pct?, preauth_hkd?}` → 201 `{session_id, status, ws_url}`
- `GET  /api/v1/charging/sessions/{id}` → session detail
- `POST /api/v1/charging/sessions/{id}/end` → 200 `{final_cost_hkd, kwh_delivered, duration_seconds}`
- `WS   /api/v1/charging/sessions/{id}/stream` — see "WS protocol" below

### Payments (agent B)
- `POST /api/v1/payments/stripe/webhook` (unauthenticated, signature-verified) → 200

### Health (agent A)
- `GET /healthz` → 200 "ok"
- `GET /readyz` → 200 "ready" if DB+Redis reachable, else 503
- `GET /version` → 200 plain text
- `GET /metrics` → 200 Prometheus text

### Error envelope (canonical)
All error responses use this shape:
```json
{"error": {"code": "WALLET_INSUFFICIENT_FUNDS", "message": "...", "details": {...}, "trace_id": "..."}}
```
Error codes are SCREAMING_SNAKE_CASE. Agents define their own codes within their domain (`WALLET_*`, `STATION_*`, `CHARGING_*`, `AUTH_*`).

## WS protocol (canonical)

WS endpoint: `wss://api.evwallet.com.hk/api/v1/charging/sessions/{id}/stream`

Connection: client sends `Authorization: Bearer <jwt>` as a subprotocol or query param `?token=<jwt>`. Server validates JWT and session ownership before upgrade.

Server-pushed frames (JSON, every 2 seconds while active):
```json
{
  "type": "telemetry",
  "ts": "2026-09-13T13:50:00+08:00",
  "kwh_cumulative": "1.234",
  "kw_instant": "47.5",
  "soc_pct": 42,
  "cost_hkd_cumulative": "11.32",
  "running_total_hkd": "11.32"
}
```

Other server frames:
```json
{"type": "status", "status": "active"|"completed"|"failed"}
{"type": "target_reached", "soc_pct": 80}
{"type": "error", "code": "...", "message": "..."}
{"type": "ping"}
```

Client-to-server frames (optional):
```json
{"type": "ping"}
{"type": "set_target_soc", "soc_pct": 80}
{"type": "end_session"}
```

Server publishes telemetry to Redis channel `charging:session:{id}`; the WS hub subscribes per connection.

## Settings (canonical env vars — agent A owns `config.py`)

All prefixed `EVW_`. Loaded via Pydantic Settings. Fail at startup if any required var is missing or invalid.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVW_", env_file=".env", extra="ignore")

    env: str = "production"
    log_level: str = "INFO"
    log_format: str = "human"  # or "json"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    workers: int = 3

    # Auth
    jwt_secret: str            # required, min length 32
    jwt_expiry_hours: int = 720

    # Trusted proxy IPs for X-Forwarded-For (Cloudflare edge IPs)
    trusted_proxies: str = ""

    # DB
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: str
    postgres_db: str

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str

    # Wallet
    preauth_max_hkd: Decimal = Decimal("500.00")

    # Payments
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    apple_pay_merchant_id: str | None = None
    google_pay_merchant_id: str | None = None
```

## Test fixtures (shared)

`tests/conftest.py` provides:
- `db_session` — async SQLAlchemy session against a fresh test DB
- `client` — FastAPI `AsyncClient` against the app
- `user_factory` — creates a user with wallet, returns (user, jwt)
- `station_factory` — creates a station with poles and rates
- `redis_client` — flushes DB between tests

## Telemetry / metrics (agent A owns `metrics.py`)

Counters: `auth_login_total{provider}`, `wallet_topup_total{source}`, `wallet_reservation_total{outcome}`, `charging_session_started_total`, `charging_session_ended_total{outcome}`, `ws_frames_sent_total{type}`

Histograms: `charging_session_duration_seconds`, `charging_ws_frame_latency_ms`, `wallet_ledger_post_latency_ms`

Exposed at `GET /metrics` in Prometheus text format.

## Logging (canonical format)

Every log line is structured JSON OR human-readable depending on `EVW_LOG_FORMAT`. Always include: `ts`, `level`, `logger`, `message`, `trace_id` (request-scoped). Agent A owns the middleware that sets `trace_id` (ULID) on every request.

## Mobile + web API client contract

Both `mobile/lib/api.ts` and `web/lib/api.ts` implement the same TypeScript interface:

```typescript
interface ApiClient {
  login(email: string, password: string): Promise<Session>
  loginApple(identityToken: string): Promise<Session>
  loginGoogle(idToken: string): Promise<Session>
  me(): Promise<{user: User; wallet: WalletSummary}>

  getStations(params: StationSearch): Promise<PaginatedStations>
  getStation(id: string): Promise<StationDetail>
  getStationRates(id: string, date: string): Promise<HourlyRate[]>

  startSession(qrCode: string, opts?: {targetSocPct?: number}): Promise<{sessionId: string; wsUrl: string}>
  endSession(id: string): Promise<SessionEndResult>

  getWallet(): Promise<Wallet>
  getWalletTransactions(cursor?: string): Promise<PaginatedTransactions>
  topUp(req: TopUpRequest): Promise<TopUpResult>
}
```

Both implementations use `mobile/lib/types.ts` and `web/lib/types.ts` respectively — types are **identical** and mirror the backend Pydantic models. Agent D owns `mobile/lib/types.ts`. Agent E owns `web/lib/types.ts`. They must agree (run `diff mobile/lib/types.ts web/lib/types.ts` at validation time — should be empty).

## What agents must NOT do

- **No sub-agent invents a new env var** without adding it to this contract.
- **No sub-agent changes a Pydantic field name or type** without updating this contract AND the mobile/web types AND the alembic migration.
- **No sub-agent uses `pydantic.BaseModel` with a field literally named `schema`** — use `schema_` with alias.
- **No sub-agent uses `print()` for logging** — always `get_logger(__name__)`.
- **No sub-agent reads environment variables directly** — use `Settings`.
- **No sub-agent writes to /Users/hermes or /tmp from inside their work** — only operate inside the repo path they're given.
- **No sub-agent pushes to git** — only commits locally on their own branch / working copy. Parent merges.

## Validation gates (parent runs after all agents return)

1. `ruff check src/` — no errors
2. `mypy src/ --ignore-missing-imports` — no errors (strict optional, no)
3. `pytest -q` — all green
4. `docker compose -f docker-compose.yml --env-file .env.test config` — valid
5. `curl http://localhost:8000/healthz` — 200 (when stack is up)
6. `mobile` and `web` `lib/types.ts` byte-equal
7. `cd mobile && npx tsc --noEmit` — clean
8. `cd web && npx tsc --noEmit` — clean
9. `cd n8n/workflows && for f in *.json; do jq empty "$f" && echo "$f ok"; done` — all valid
10. `git diff --stat main` — only files expected from this round