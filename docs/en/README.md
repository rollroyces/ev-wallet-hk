# EV Wallet HK

Hong Kong EV charging aggregator + unified credit wallet. Aggregates real-time HK charging prices (kWh rates, hourly tariffs, parking fees, connector types), lets drivers locate stations on a map, start charging via QR, and pay from a unified wallet across Web, iOS, and Android.

**Status:** 🚧 pre-alpha — deploy scaffolding only. No application code yet.

## What's in this repo (this commit)

- `docker-compose.yml` — production stack (Postgres 16 · Redis 7 · FastAPI · n8n · Caddy · Cloudflare Tunnel)
- `Caddyfile` — reverse proxy, loopback only, WebSocket-aware
- `deploy/fastapi/Dockerfile` — multi-stage, non-root, signal-aware FastAPI image
- `deploy/postgres/postgresql.conf` — tuned for ledger workload
- `deploy/cloudflared/{config.yml,SETUP.md}` — Tunnel routing + one-time setup guide
- `deploy/launchd/` — three plists for auto-restart on boot, UPS auto-shutdown, daily backups
- `deploy/macos/energy.sh` — pmset tuning for server-class Mac mini
- `scripts/{backup,restore,verify-backup,ups-monitor}.sh` — backup & recovery
- `.env.example` — every required env var
- `docs/BACKUP.md` — restore procedure, B2 setup, what fails and how

## What's NOT here yet (next turns)

- FastAPI application code (`auth`, `wallet`, `stations`, `charging` modules + WebSocket hub)
- SQLAlchemy models (designed in chat, not yet committed)
- Alembic migrations + initial schema
- Expo (React Native) mobile app
- Next.js web portal
- n8n workflow JSON for HK provider ingestion

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────┐
│  Public Internet                                            │
│     ↓                                                        │
│  Cloudflare Edge (TLS termination, DDoS, caching)           │
│     ↓                                                        │
│  cloudflared (encrypted tunnel) → Caddy :443 (loopback)      │
│     ↓                                                        │
│  ┌──────────────────────────────────────────────┐            │
│  │  FastAPI  ── Postgres 16 (ledger)            │            │
│  │     └── Redis 7 (sessions, TOU cache)        │            │
│  │  n8n ──── (HK provider polling, TOU normalize)│            │
│  └──────────────────────────────────────────────┘            │
│     ↓                                                        │
│  Backblaze B2 (encrypted offsite backups, daily 03:00 HKT)   │
└──────────────────────────────────────────────────────────────┘
```

## First-time setup on a fresh Mac mini

```bash
# 1. macOS power settings
sudo ./deploy/macos/energy.sh

# 2. Install Docker Desktop
#    https://www.docker.com/products/docker-desktop/

# 3. Install rclone for B2 backups
brew install rclone

# 4. Install apcupsd for UPS monitoring (APC UPS only)
brew install apcupsd
# Configure for your UPS model: see /opt/homebrew/etc/apcupsd/

# 5. Clone and configure
git clone https://github.com/rollroyces/ev-wallet-hk.git ~/projects/ev-wallet-hk
cd ~/projects/ev-wallet-hk
cp .env.example .env
# Edit .env — fill in every secret. See "Secret generation" below.

# 6. Install launchd plists
cp deploy/launchd/com.docker.desktop-auto-restart.plist ~/Library/LaunchAgents/
cp deploy/launchd/com.apc.shutdown.plist               ~/Library/LaunchAgents/
cp deploy/launchd/com.ev.backup.plist                  ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist
launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
launchctl load ~/Library/LaunchAgents/com.ev.backup.plist

# 7. One-time Cloudflare Tunnel setup
# See deploy/cloudflared/SETUP.md for the full dance.
# Result: paste CLOUDFLARED_TUNNEL_TOKEN into .env.

# 8. Boot the stack
mkdir -p ~/projects/ev-wallet-hk/logs
docker compose up -d

# 9. Verify
docker compose ps                  # all services healthy
curl https://api.evwallet.com.hk/healthz   # 200 ok
```

## Secret generation

```bash
# Postgres + Redis passwords (run twice, different output each time)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# JWT secret
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# n8n encryption key
python3 -c "import secrets; print(secrets.token_hex(16))"
```

Paste each into `.env`. Don't reuse the same secret across services.

## Daily operations

| Task | How |
|---|---|
| Backup status | `tail -f ~/projects/ev-wallet-hk/logs/backup.log` |
| Verify last 3 backups | `./scripts/verify-backup.sh` |
| Restore (interactive) | `./scripts/restore.sh 20260913` |
| Restart a service | `docker compose restart fastapi` |
| Tail FastAPI logs | `docker compose logs -f fastapi` |
| Tail n8n logs | `docker compose logs -f n8n` |
| Postgres shell | `docker exec -it evw-postgres psql -U evwallet -d evwallet` |

## Local development (laptop, not the Mac mini)

`docker-compose.yml` is identical between dev and prod. The difference is `.env`:

```bash
cp .env.example .env.dev
# Override:
#   EVW_ENV=development
#   POSTGRES_PASSWORD=devpassword
#   EVW_JWT_SECRET=devsecret
docker compose --env-file .env.dev up -d
```

## Cost

| Item | Cost |
|---|---|
| Mac mini M4 (one-time) | ~HK$4,500 |
| APC UPS BE600M2 (one-time) | ~HK$280 |
| Domain (.com.hk) | ~HK$120/yr |
| Backblaze B2 | ~HK$7/mo (10 GB tier) |
| rclone, Docker, Cloudflare Tunnel, Caddy, Postgres, Redis, n8n, FastAPI | **$0** |
| **Monthly recurring** | **~HK$130/mo + electricity** |

vs. running on AWS HK (ap-east-1 ECS + RDS + ElastiCache): ~HK$2,000+/mo at idle.

## Security

- All secrets in `.env` (gitignored). Never commit.
- Postgres + Redis bound to `127.0.0.1` only. Not reachable from the network.
- Cloudflare Tunnel terminates TLS at Cloudflare's edge. Caddy listens on loopback.
- FastAPI trusts `X-Forwarded-For` **only** from Cloudflare's published IP ranges (`EVW_TRUSTED_PROXIES`).
- JWT secret rotation requires restarting FastAPI; old tokens remain valid until expiry.

See `SECURITY.md` for the threat model and disclosure process.

## License

**Commercial (Proprietary).** This source code is published for visibility — no permission to run, copy, modify, or distribute is granted without a written commercial agreement. See `LICENSE` for the summary and `LICENSE-COMMERCIAL.md` for the template agreement with indicative pricing tiers.