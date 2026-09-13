# EV Wallet HK（香港電動車錢包）

香港電動車充電聚合平台 + 統一儲值錢包。系統會即時聚合香港各大充電營運商的實時收費（每度電價、小時停車費、充電位類型），讓駕駛者在地圖上搵到充電站，透過 QR code 啟動充電，並以同一個錢包在 Web、iOS、Android 三端結帳。

**狀態：** 🚧 pre-alpha（前置 alpha）— 目前只交付部署所需的基建。尚未有應用程式原始碼。

## 本倉庫目前已包含的內容（本 commit）

- `docker-compose.yml` — 生產環境服務組合（Postgres 16 · Redis 7 · FastAPI · n8n · Caddy · Cloudflare Tunnel）
- `Caddyfile` — 反向代理，只 listen loopback，支援 WebSocket
- `deploy/fastapi/Dockerfile` — multi-stage 建置、非 root 用戶（non-root）、可處理訊號的 FastAPI image
- `deploy/postgres/postgresql.conf` — 為 ledger（分類帳）工作量調校
- `deploy/cloudflared/{config.yml,SETUP.md}` — Tunnel 路由 + 一次性設定指南
- `deploy/launchd/` — 三份 launchd plist，分別處理開機自動重啟、UPS 自動關機、每日備份
- `deploy/macos/energy.sh` — 為 server-grade Mac mini 調校的 `pmset` 設定
- `scripts/{backup,restore,verify-backup,ups-monitor}.sh` — 備份與還原腳本
- `.env.example` — 列出所有必需的環境變數
- `docs/BACKUP.md` — 還原步驟、Backblaze B2 設定、常見失敗與處理方式

## 目前尚未包含的內容（下一階段）

- FastAPI 應用程式原始碼（`auth`、`wallet`、`stations`、`charging` 模組 + WebSocket hub）
- SQLAlchemy models（已在對話中設計，但尚未 commit）
- Alembic migrations + 初始 schema
- Expo（React Native）手機 App
- Next.js Web 入口網站
- n8n workflow JSON（用於抓取香港充電營運商資料）

## 架構概覽

```
┌──────────────────────────────────────────────────────────────┐
│  Public Internet                                            │
│     ↓                                                        │
│  Cloudflare Edge（TLS termination、 DDoS 防護、 caching）     │
│     ↓                                                        │
│  cloudflared（加密 tunnel） → Caddy :443（loopback）         │
│     ↓                                                        │
│  ┌──────────────────────────────────────────────┐            │
│  │  FastAPI  ── Postgres 16（ledger 分類帳）    │            │
│  │     └── Redis 7（sessions、TOU cache）       │            │
│  │  n8n ────（HK 充電營運商 polling、TOU 標準化） │            │
│  └──────────────────────────────────────────────┘            │
│     ↓                                                        │
│  Backblaze B2（加密異地備份，每日 03:00 HKT）               │
└──────────────────────────────────────────────────────────────┘
```

## 在全新 Mac mini 上的首次設定

```bash
# 1. macOS 電源設定
sudo ./deploy/macos/energy.sh

# 2. 安裝 Docker Desktop
#    https://www.docker.com/products/docker-desktop/

# 3. 安裝 rclone（供 B2 備份使用）
brew install rclone

# 4. 安裝 apcupsd（只適用於 APC UPS）
brew install apcupsd
# 依 UPS 型號設定：請參考 /opt/homebrew/etc/apcupsd/

# 5. Clone 及設定
git clone https://github.com/rollroyces/ev-wallet-hk.git ~/projects/ev-wallet-hk
cd ~/projects/ev-wallet-hk
cp .env.example .env
# 編輯 .env — 填好每個 secret。請參考下方「Secret generation」一節。

# 6. 安裝 launchd plist
cp deploy/launchd/com.docker.desktop-auto-restart.plist ~/Library/LaunchAgents/
cp deploy/launchd/com.apc.shutdown.plist               ~/Library/LaunchAgents/
cp deploy/launchd/com.ev.backup.plist                  ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist
launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
launchctl load ~/Library/LaunchAgents/com.ev.backup.plist

# 7. 一次性 Cloudflare Tunnel 設定
# 請參考 deploy/cloudflared/SETUP.md 完整步驟。
# 完成後將 CLOUDFLARED_TUNNEL_TOKEN 貼入 .env。

# 8. 啟動服務組合
mkdir -p ~/projects/ev-wallet-hk/logs
docker compose up -d

# 9. 驗證
docker compose ps                  # 所有服務應為 healthy
curl https://api.evwallet.com.hk/healthz   # 200 ok
```

## Secret 生成

```bash
# Postgres + Redis 密碼（執行兩次，每次輸出不同的結果）
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# JWT secret
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# n8n encryption key
python3 -c "import secrets; print(secrets.token_hex(16))"
```

每個值各自貼入 `.env`。請勿在不同服務之間重複使用同一個 secret。

## 日常運作

| 工作 | 做法 |
|---|---|
| 查看備份狀態 | `tail -f ~/projects/ev-wallet-hk/logs/backup.log` |
| 驗證最近 3 次備份 | `./scripts/verify-backup.sh` |
| 還原（互動式） | `./scripts/restore.sh 20260913` |
| 重啟單一服務 | `docker compose restart fastapi` |
| 檢視 FastAPI log | `docker compose logs -f fastapi` |
| 檢視 n8n log | `docker compose logs -f n8n` |
| 進入 Postgres shell | `docker exec -it evw-postgres psql -U evwallet -d evwallet` |

## 本機開發（用筆電，不是在 Mac mini 上）

開發與生產環境使用同一份 `docker-compose.yml`，差別只在 `.env`：

```bash
cp .env.example .env.dev
# 覆寫以下項目：
#   EVW_ENV=development
#   POSTGRES_PASSWORD=devpassword
#   EVW_JWT_SECRET=devsecret
docker compose --env-file .env.dev up -d
```

## 成本

| 項目 | 成本 |
|---|---|
| Mac mini M4（一次性） | ~HK$4,500 |
| APC UPS BE600M2（一次性） | ~HK$280 |
| Domain（`.com.hk`） | ~HK$120/年 |
| Backblaze B2 | ~HK$7/月（10 GB 級距） |
| rclone、Docker、Cloudflare Tunnel、Caddy、Postgres、Redis、n8n、FastAPI | **$0** |
| **每月經常性開支** | **~HK$130/月 + 電費** |

對比：在 AWS HK（ap-east-1 ECS + RDS + ElastiCache）閒置狀態每月約 HK$2,000+。

## 安全性

- 所有 secret 皆放在 `.env`（已被 `.gitignore` 排除）。永遠不要 commit。
- Postgres + Redis 只 bind 在 `127.0.0.1`，不對外開放。
- TLS 由 Cloudflare edge 終止；Caddy 只 listen loopback。
- FastAPI 只信任來自 Cloudflare 公開 IP 範圍的 `X-Forwarded-For`（由 `EVW_TRUSTED_PROXIES` 設定）。
- JWT secret rotation 後需要重啟 FastAPI；舊 token 在過期前仍然有效。

詳見 `SECURITY.md` 的威脅模型與漏洞通報流程。

## 授權

**Commercial (Proprietary)。** 此原始碼僅供公開參考 — 未經書面商業協議同意前，一律不得使用、複製、修改或散佈。請參考 `LICENSE` 的條款摘要，以及 `LICENSE-COMMERCIAL.md` 的商業協議範本及參考價目。