# 架構合約 — 各模組必須共同遵守的介面

本文件是 EV Wallet HK 系統中**所有 sub-agent 之間的共用合約（contract）**。每個模組都依此文件所定義的形狀（shape）來設計實作。如果你發現自己需要發明新形狀，**請先停下來**，把新形狀加進本文件，讓其他 agent 可以同步調整。

## 最終的 repo 結構

```
ev-wallet-hk/
├── docker-compose.yml           # 已交付
├── Caddyfile                    # 已交付
├── deploy/                      # 已交付
├── docs/
│   ├── ARCHITECTURE.md          # 本檔案（合約本身）
│   ├── API.md                   # agent A 負責
│   ├── BACKUP.md                # 已交付
│   └── LEDGER.md                # agent B 負責
├── src/evwallet/                # 所有 Python 程式碼
│   ├── __init__.py
│   ├── main.py                  # FastAPI factory（agent A）
│   ├── config.py                # Pydantic Settings（agent A）
│   ├── logging.py               # 結構化 logging（agent A）
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py           # async engine、get_db（agent A）
│   │   └── models.py            # SQLAlchemy models（agent A，請見 "DB Models" 一節）
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── jwt.py               # encode/decode token（agent A）
│   │   ├── deps.py              # current_user dep（agent A）
│   │   ├── apple.py             # Apple Sign-In 驗證器（agent A）
│   │   ├── google.py            # Google OAuth 驗證器（agent A）
│   │   └── router.py            # /auth/{login,apple,google,me}（agent A）
│   ├── wallet/
│   │   ├── __init__.py
│   │   ├── ledger.py            # post_entry()、get_balance() — 核心邏輯（agent B）
│   │   ├── topup.py             # Stripe/Apple Pay/Google Pay（agent B）
│   │   ├── reservation.py       # preauth/settle/release（agent B）
│   │   └── router.py            # /wallet/*（agent B）
│   ├── charging/
│   │   ├── __init__.py
│   │   ├── ws.py                # WebSocket hub、Redis pub/sub（agent C）
│   │   ├── telemetry.py         # session_telemetry writer（agent C）
│   │   ├── qr.py                # QR payload 驗證（agent C）
│   │   └── router.py            # /charging/sessions/* REST（agent C）
│   ├── stations/
│   │   ├── __init__.py
│   │   ├── search.py            # 地理 + 篩選（agent C）
│   │   ├── rates.py             # TOU 電價查詢（agent C）
│   │   └── router.py            # /stations/*（agent C）
│   ├── payments/
│   │   ├── __init__.py
│   │   ├── stripe.py            # Stripe webhooks（agent B）
│   │   └── apple_google.py      # 原生 sheet 驗證器（agent B）
│   ├── errors.py                # IDPError hierarchy（agent A）
│   └── metrics.py               # Prometheus exporter（agent A）
├── alembic/                     # agent A 負責
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py
├── tests/
│   ├── conftest.py              # 共用 fixtures（agent A）
│   ├── test_auth.py
│   ├── test_wallet_ledger.py    # agent B — 極為關鍵
│   ├── test_stations.py
│   ├── test_charging_ws.py      # agent C
│   └── test_health.py
├── mobile/                      # agent D（Expo）
│   ├── package.json
│   ├── tsconfig.json
│   ├── app.config.ts
│   ├── app/
│   │   ├── _layout.tsx
│   │   ├── (tabs)/
│   │   │   ├── _layout.tsx
│   │   │   ├── index.tsx        # 地圖
│   │   │   ├── wallet.tsx
│   │   │   ├── activity.tsx
│   │   │   └── profile.tsx
│   │   ├── scan.tsx             # QR 掃描
│   │   └── session/[id].tsx     # 即時充電 session 監察
│   ├── lib/
│   │   ├── api.ts               # 共用 API client
│   │   ├── types.ts             # 共用 TypeScript 型別
│   │   └── auth.ts              # SecureStore token 管理
│   └── components/
│       └── ...
├── web/                         # agent E（Next.js）
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
│       ├── hkev-poll.json       # HK EV Power 輪詢
│       ├── clp-poll.json        # CLP ChargePoint 輪詢
│       └── rate-normalize.json  # 共通 normalizer
├── deploy/                      # 已交付（不可修改）
├── scripts/                     # 已交付（不可修改）
├── pyproject.toml               # agent A 負責
└── README.md                    # 已交付
```

## 模組名稱不可擅改

Sub-agent 必須使用以下完全一致的 Python module 名稱 — 例如 `evwallet.wallet.ledger`。`mobile` 和 `web` 的共用 `lib/api.ts` 必須分別從 `mobile/lib/types.ts` 與 `web/lib/types.ts` import，而兩份 types.ts 必須對應到完全相同的 TypeScript shape。

## 資料庫 models（canonical SQLAlchemy 2.0）

**Single source of truth：agent A。** 其他 agent 直接讀 `src/evwallet/db/models.py` 並從中 import。若 B/C 需要一個尚未存在的欄位，必須由 agent A 加入；B/C 不得自行新增欄位。

資料表（依相依順序排列；除非另有說明，皆有 `id UUID PK default gen_random_uuid()` 及合適的 `created_at`、`updated_at`）：

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
    available_credits: Mapped[Decimal] = 0   # HKD（港元）, Numeric(12,4)
    reserved_credits: Mapped[Decimal] = 0    # HKD, Numeric(12,4)
    currency: Mapped[str] = "HKD"
    version: Mapped[int] = 0                  # 樂觀鎖版本號
```

### `wallet_transactions`
```python
class WalletTransaction(Base):
    id: Mapped[uuid.UUID]
    wallet_id: Mapped[uuid.UUID]  # FK
    user_id: Mapped[uuid.UUID]    # FK（反正規化以加速 user 端查詢）
    kind: Mapped[str]             # 'topup' | 'charge' | 'refund' | 'fee'
    status: Mapped[str] = "posted"  # 'pending' | 'posted' | 'reversed'
    amount: Mapped[Decimal]        # HKD
    currency: Mapped[str] = "HKD"
    external_ref: Mapped[str | None]  # unique，idempotency key
    description: Mapped[str] = ""
    metadata_json: Mapped[dict] = {}  # column name `metadata`
    posted_at: Mapped[datetime]
```

### `ledger_entries`
**這是雙分錄 journal（double-entry journal，分類帳分錄）。完整性不變式（integrity invariants）由這張表把關。**
```python
class LedgerEntry(Base):
    id: Mapped[int]  # BIGINT autoincrement PK（高流量 journal）
    txn_id: Mapped[uuid.UUID]  # FK wallet_transactions.id
    wallet_id: Mapped[uuid.UUID]  # FK
    entry_type: Mapped[str]  # enum（見下）
    amount: Mapped[Decimal]   # signed, HKD；單一 txn 的 SUM(amount) 必須為 0
    bucket: Mapped[str]       # 'available' | 'reserved' | 'external'
    posted_at: Mapped[datetime]
```
`entry_type` enum：`'topup_debit', 'charge_credit', 'reserve', 'release', 'settle', 'refund', 'external_clearing'`。

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
    qr_code: Mapped[str]  # unique, indexed — 掃描得到的值
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

## API 介面（canonical HTTP/WS endpoints）

所有 endpoints 路徑都在 `/api/v1/` 之下。除另有註明外，所有回應都是 JSON。所有需要認證的 endpoints 都必須帶 `Authorization: Bearer ***。JWT 內容包含 `sub: uuid`、`exp: int`、`iat: int`。Token 有效期：720 小時（30 日）。

### Auth（agent A）
- `POST /api/v1/auth/login` body=`{email, password}` → 200 `{access_token, expires_at, user}`
- `POST /api/v1/auth/apple` body=`{identity_token, authorization_code?, full_name?, email?}` → 200 同上
- `POST /api/v1/auth/google` body=`{id_token}` → 200 同上
- `GET  /api/v1/auth/me` → 200 `{user, wallet: {available_hkd, reserved_hkd}}`

### Wallet（agent B）
- `GET  /api/v1/wallet` → `{available_hkd, reserved_hkd, currency, recent_transactions: [...]}`
- `POST /api/v1/wallet/topup` body=`{amount_hkd, source: 'stripe'|'apple_pay'|'google_pay', source_payload}` → 200 `{transaction_id, status: 'pending'|'posted'}`
- `GET  /api/v1/wallet/transactions?limit=20&cursor=...` → `{transactions: [...], next_cursor}`
- `GET  /api/v1/wallet/balance` → `{available_hkd, reserved_hkd}`

### Stations（agent C）
- `GET  /api/v1/stations?lat=22.3&lng=114.2&radius_km=5&connector=ccs2&min_kw=50` → `{stations: [...], total}`
- `GET  /api/v1/stations/{id}` → 完整 station + poles + 未來 24 小時 rates
- `GET  /api/v1/stations/{id}/rates?date=2026-09-13` → 24 小時 TOU 窗口

### Charging sessions（agent C）
- `POST /api/v1/charging/sessions` body=`{qr_code, target_soc_pct?, preauth_hkd?}` → 201 `{session_id, status, ws_url}`
- `GET  /api/v1/charging/sessions/{id}` → session 詳細資料
- `POST /api/v1/charging/sessions/{id}/end` → 200 `{final_cost_hkd, kwh_delivered, duration_seconds}`
- `WS   /api/v1/charging/sessions/{id}/stream` — 見下方「WS protocol」

### Payments（agent B）
- `POST /api/v1/payments/stripe/webhook`（不需認證；以 signature 驗證）→ 200

### Health（agent A）
- `GET /healthz` → 200 "ok"
- `GET /readyz` → 200 "ready"（若 DB + Redis 可達），否則 503
- `GET /version` → 200 plain text
- `GET /metrics` → 200 Prometheus text

### 錯誤回應信封（canonical）
所有錯誤回應皆使用以下格式：
```json
{"error": {"code": "WALLET_INSUFFICIENT_FUNDS", "message": "...", "details": {...}, "trace_id": "..."}}
```
錯誤代碼採用 SCREAMING_SNAKE_CASE。各 agent 在自己負責的 domain 內自行定義代碼（`WALLET_*`、`STATION_*`、`CHARGING_*`、`AUTH_*`）。

## WS 協定（canonical）

WS endpoint：`wss://api.evwallet.com.hk/api/v1/charging/sessions/{id}/stream`

連線：client 以 `Authorization: Bearer *** 作為 subprotocol，或以 query param `?token=<jwt>` 傳入。Server 在 upgrade 之前必須先驗 JWT 及 session 擁有權。

Server 主動推送的 frame（JSON；active 狀態下每 2 秒一次）：
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

其他 server frame：
```json
{"type": "status", "status": "active"|"completed"|"failed"}
{"type": "target_reached", "soc_pct": 80}
{"type": "error", "code": "...", "message": "..."}
{"type": "ping"}
```

Client 送往 server 的 frame（optional）：
```json
{"type": "ping"}
{"type": "set_target_soc", "soc_pct": 80}
{"type": "end_session"}
```

Server 把 telemetry 發佈到 Redis channel `charging:session:{id}`，WS hub 為每條連線訂閱該 channel。

## 設定（canonical 環境變數 — agent A 擁有 `config.py`）

全部使用 `EVW_` 前綴。以 Pydantic Settings 載入。啟動時若有任何必需變數缺失或無效，必須 fail-fast。

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVW_", env_file=".env", extra="ignore")

    env: str = "production"
    log_level: str = "INFO"
    log_format: str = "human"  # 或 "json"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    workers: int = 3

    # Auth
    jwt_secret: str            # 必填，最少 32 字元
    jwt_expiry_hours: int = 720

    # 信任的 proxy IP（X-Forwarded-For）—— Cloudflare edge IP
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

## 測試 fixtures（共用）

`tests/conftest.py` 提供：
- `db_session` — async SQLAlchemy session，連線至全新的測試 DB
- `client` — FastAPI `AsyncClient`，對應到該 app
- `user_factory` — 建立帶錢包的用戶，回傳 `(user, jwt)`
- `station_factory` — 建立帶 poles 及 rates 的 station
- `redis_client` — 每個 test 之間 flush DB

## Telemetry / metrics（agent A 擁有 `metrics.py`）

Counters：`auth_login_total{provider}`、`wallet_topup_total{source}`、`wallet_reservation_total{outcome}`、`charging_session_started_total`、`charging_session_ended_total{outcome}`、`ws_frames_sent_total{type}`

Histograms：`charging_session_duration_seconds`、`charging_ws_frame_latency_ms`、`wallet_ledger_post_latency_ms`

於 `GET /metrics` 以 Prometheus text format 暴露。

## Logging（canonical 格式）

每行 log 依 `EVW_LOG_FORMAT` 設定為結構化 JSON 或人可讀格式。必含欄位：`ts`、`level`、`logger`、`message`、`trace_id`（以 request 為單位）。Agent A 負責 middleware，在每個 request 注入 `trace_id`（ULID）。

## Mobile + web API client contract

`mobile/lib/api.ts` 及 `web/lib/api.ts` 皆實作同一個 TypeScript interface：

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

兩邊實作分別使用 `mobile/lib/types.ts` 及 `web/lib/types.ts` — 兩份型別檔**必須完全相同**，並對應到後端 Pydantic models。Agent D 擁有 `mobile/lib/types.ts`，agent E 擁有 `web/lib/types.ts`，兩邊必須保持一致（驗證時可執行 `diff mobile/lib/types.ts web/lib/types.ts`，應該完全無輸出）。

## Agent 不可做的事

- **任何 sub-agent 不得擅自新增 env var**，除非先更新本合約。
- **任何 sub-agent 不得擅自修改 Pydantic 欄位名稱或型別**，除非同時更新本合約、mobile/web 型別檔、以及 alembic migration。
- **任何 sub-agent 不得使用 `pydantic.BaseModel` 中名為 `schema` 的欄位** — 一律用 `schema_` 加 alias。
- **任何 sub-agent 不得使用 `print()` 來 log** — 一律使用 `get_logger(__name__)`。
- **任何 sub-agent 不得直接讀環境變數** — 一律透過 `Settings`。
- **任何 sub-agent 不得在工作中寫入 `/Users/hermes` 或 `/tmp`** — 只能在指定的 repo 路徑內操作。
- **任何 sub-agent 不得 push 到 git** — 只在本地 branch / working copy commit，由 parent 統一 merge。

## 驗證閘門（parent 在所有 agent 回報後執行）

1. `ruff check src/` — 無錯誤
2. `mypy src/ --ignore-missing-imports` — 無錯誤（strict optional、no）
3. `pytest -q` — 全部綠燈
4. `docker compose -f docker-compose.yml --env-file .env.test config` — 設定有效
5. `curl http://localhost:8000/healthz` — 200（服務組合啟動後）
6. `mobile` 和 `web` 的 `lib/types.ts` 位元級相同
7. `cd mobile && npx tsc --noEmit` — 通過
8. `cd web && npx tsc --noEmit` — 通過
9. `cd n8n/workflows && for f in *.json; do jq empty "$f" && echo "$f ok"; done` — 全部有效
10. `git diff --stat main` — 只包含本輪預期的檔案