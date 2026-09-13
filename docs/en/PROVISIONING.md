# Provisioning — EV Wallet HK

Operational runbook for provisioning the external services and on-prem hardware
required to run EV Wallet HK in production on a Mac mini in Hong Kong. Read this
end-to-end before you start; each step references the relevant piece of `.env` or
the existing runbook it builds on.

Tone: senior-engineer-to-senior-engineer. No filler. If a step is unclear, the
canonical source is linked; do not improvise.

## A. Domain & DNS

1. Register **`.com.hk`** at [HKIRC](https://www.hkirc.hk/) via an HKIS-accredited
   registrar (HK$80–400/yr depending on term). A `.com` via Cloudflare Registrar
   (US$10/yr, $0 markup) is an acceptable fallback if you don't need a `.hk`.
2. In the registrar dashboard, **point the nameservers to Cloudflare**. Free
   plan is sufficient. Cloudflare will give you two nameserver hostnames
   (`xxx.ns.cloudflare.com`); paste them into the registrar's "custom nameservers"
   field and remove the registrar's default NS records.
3. Wait for Cloudflare to detect the delegation (usually <5 min, can take up to
   24h). The Cloudflare dashboard will show the domain as **"Active"**.
4. In Cloudflare DNS, add the three production hostnames. Use the Cloudflare
   Tunnel route (Step B) — do NOT create CNAMEs pointing to a public IP, because
   we have none. The `route dns` command below creates the CNAMEs for you and
   binds them to the tunnel:
   - `api.evwallet.com.hk` → FastAPI via Caddy
   - `n8n.evwallet.com.hk` → n8n admin UI
   - `app.evwallet.com.hk` → Next.js web portal (future)

Estimated cost: HK$80–400/yr + HK$0 for Cloudflare.

## B. Cloudflare Tunnel (only public ingress)

The Tunnel is the **only** path from the public internet to the Mac mini. Caddy
listens on loopback; cloudflared makes outbound-only connections to Cloudflare's
edge. There is no port forwarding and no public IP.

Full dance: see [`deploy/cloudflared/SETUP.md`](../deploy/cloudflared/SETUP.md).
TL;DR for a fresh box:

```bash
brew install cloudflared              # one-time
cloudflared tunnel login              # opens browser; pick evwallet.com.hk
cloudflared tunnel create ev-wallet-prod
cloudflared tunnel route dns ev-wallet-prod api.evwallet.com.hk
cloudflared tunnel route dns ev-wallet-prod n8n.evwallet.com.hk
cloudflared tunnel route dns ev-wallet-prod app.evwallet.com.hk
cloudflared tunnel token ev-wallet-prod     # copy to .env
```

Paste the token into `.env` as `CLOUDFLARED_TUNNEL_TOKEN`.

**Rotate the token every 90 days** (see Step 8 of `SETUP.md`). The credential
JSON in `~/.cloudflared/` is also worth backing up to a password manager — losing
it means re-creating the tunnel and re-routing DNS.

## C. Backblaze B2 (offsite backups)

1. Sign up at <https://www.backblaze.com/b2>. Free tier: **10 GB** — enough for
   years of compressed `pg_basebackup` snapshots at our scale.
2. Create a bucket: **`ev-wallet-backups`** (private, encryption enabled).
   Lifecycle rules: **keep 7 daily + 4 weekly + 6 monthly**. Older objects expire
   automatically. (See `docs/BACKUP.md` for what gets backed up and why B2.)
3. Create an **Application Key** scoped to *only* this bucket, with `listFiles`,
   `listBuckets`, `readFiles`, `writeFiles`, `deleteFiles`. Do NOT use your
   master account key in `.env`.
4. Paste into `.env`:
   ```
   B2_ACCOUNT_ID=...
   B2_APPLICATION_KEY=...
   B2_BUCKET=ev-wallet-backups
   B2_PATH_PREFIX=mac-mini-prod
   ```

Without B2, `scripts/backup.sh` writes locally only — fine for development,
reckless for production.

## D. UPS hardware

**Why:** hard power loss mid-write corrupts Postgres WAL. For a wallet's
double-entry ledger (which we cannot rebuild from external sources), corruption
== unrecoverable balance drift. A UPS is the cheapest insurance we can buy.

Recommended: **APC Back-UPS BE600M2** (~HK$280, USB, 600 VA / 330 W — enough
for a Mac mini M4 under full load). Any APC USB UPS will work; non-USB UPSes
need different cabling (`UPSCABLE simple`, `UPSTYPE simple`).

1. Plug the Mac mini into the UPS; plug the UPS into the wall.
2. `brew install apcupsd`
3. Edit `/opt/homebrew/etc/apcupsd/apcupsd.conf`:
   ```
   UPSCABLE usb
   UPSTYPE usb
   DEVICE
   ```
   (Leave `DEVICE` blank — apcupsd auto-detects the USB UPS.)
4. `brew services start apcupsd`
5. Verify: `apcaccess status` → expect `STATUS   : ONLINE` and a non-zero
   `LINEV` / `BCHARGE` / `TIMELEFT`.
6. Install the auto-shutdown plist:
   ```bash
   cp deploy/launchd/com.apc.shutdown.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
   ```
   This watches UPS status via `scripts/ups-monitor.sh` and issues `shutdown -h
   now` when battery < 30% AND on battery. Test by pulling the wall plug briefly
   (Step J).

## E. Apple Developer setup (Sign-In + Apple Pay)

1. Enroll in the **Apple Developer Program** (US$99/yr). HK entity or HK
   individual accepted. Approval takes 24–48 h.
2. In App Store Connect / Certificates, Identifiers & Profiles:
   - **App ID**: `com.evwallet.hk` — enable **Sign-In with Apple** capability.
   - **Services ID**: `com.evwallet.hk.web` — used for web Sign-In verification
     (no merchant certificate needed for Sign-In; JWKS-based token verification
     only).
3. **Apple Pay** (merchant-side):
   - **Merchant ID**: `merchant.com.evwallet.hk`
   - **Processing Certificate** (`merchant_identity.cer`) — generated CSR →
     uploaded → `.cer` downloaded.
   - **Private key** (`.p8`) — the key you generated the CSR with. **Back this up
     to a password manager immediately.** Losing it = re-do merchant cert.
   - Save both files to a path outside the repo (e.g.
     `/Users/hermes/.evwallet/secrets/`). Reference in `.env`:
     ```
     EVW_APPLE_PAY_MERCHANT_ID=merchant.com.evwallet.hk
     EVW_APPLE_PAY_MERCHANT_CERT_PATH=/Users/hermes/.evwallet/secrets/merchant_identity.cer
     EVW_APPLE_PAY_MERCHANT_KEY_PATH=/Users/hermes/.evwallet/secrets/merchant_key.p8
     ```

If Apple Pay is deferred for the first release, leave the three vars blank —
`Settings` declares them `Optional` and Apple Pay UI is gated.

## F. Google Cloud setup (OAuth + Google Pay)

1. Create a project at <https://console.cloud.google.com/> (e.g.
   `ev-wallet-prod`).
2. Enable **Google+ API** (for Sign-In — Google Identity Services still requires
   this for OAuth profile access). Also enable **Google Pay API** if you want
   production Google Pay on the web.
3. **OAuth Client ID** (Web application):
   - Authorized JavaScript origin: `https://app.evwallet.com.hk`
   - Authorized redirect URI: `https://app.evwallet.com.hk/api/v1/auth/google/callback`
     (only if you use the redirect flow; our backend uses the implicit/id_token
     flow, so this can be omitted.)
   - Paste into `.env`:
     ```
     EVW_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
     EVW_GOOGLE_CLIENT_SECRET=...
     ```
4. **Service Account** (for Google Pay processing):
   - IAM & Admin → Service Accounts → Create. Role: `Google Pay API` /
     appropriate minimal scope.
   - Create a JSON key. Save it to `/Users/hermes/.evwallet/secrets/gpay-sa.json`.
   - Reference in `.env`:
     ```
     EVW_GOOGLE_PAY_MERCHANT_ID=...     # your Google Pay merchant ID
     EVW_GOOGLE_SA_KEY_PATH=/Users/hermes/.evwallet/secrets/gpay-sa.json
     ```

## G. Stripe setup (topups + webhooks)

1. Create an account at <https://dashboard.stripe.com/>. **HK entity required**
   for accepting HK-issued cards without cross-border decline rates. Stripe
   Atlas can form an HK entity for ~US$500 if you don't already have one.
2. **Payment Methods → Activate Apple Pay + Google Pay.** Stripe handles the
   wallet-side decryption; you provide the merchant identifiers from Steps E
   and F.
3. Get **API keys** (test mode first, then live). Paste:
   ```
   EVW_STRIPE_SECRET_KEY=sk_live_...     # production
   EVW_STRIPE_PUBLISHABLE_KEY=pk_live_...  # web portal only
   ```
4. Create a **Webhook endpoint**:
   - URL: `https://api.evwallet.com.hk/api/v1/payments/stripe/webhook`
   - Events: `payment_intent.succeeded`, `payment_intent.payment_failed`,
     `charge.refunded`, `payout.paid`
5. Copy the **signing secret** into `.env`:
   ```
   EVW_STRIPE_WEBHOOK_SECRET=whsec_...
   ```
6. Smoke-test the webhook with the Stripe CLI:
   ```bash
   stripe listen --forward-to https://api.evwallet.com.hk/api/v1/payments/stripe/webhook
   stripe trigger payment_intent.succeeded
   ```

## H. Mac mini first-boot checklist

Run these in order on a freshly imaged Mac mini. The Mac mini should be a
**headless** install (no monitor / keyboard after setup) — set VNC/Screen
Sharing during provisioning, then disable the screen sharing port on the
firewall and rely on Cloudflare Tunnel for all remote access.

1. **Power settings** — auto-restart on power loss:
   ```bash
   sudo ./deploy/macos/energy.sh
   ```
   Verifies with `pmset -g | grep -i restart` → `RestartPowerFailure: 1`.

2. **Disable "Find My Mac"** — Activation Lock will block a future remote wipe
   from succeeding, leaving the device bricked for a legitimate new owner.
   System Settings → Apple ID → Find My → toggle off. Enter Apple ID password
   when prompted.

3. **FileVault** — should be on by default. Verify:
   ```bash
   fdesetup status
   ```
   If `Off`, turn it on and **save the recovery key in your password manager.**

4. **Docker Desktop** — install the dmg from docker.com (free for personal /
   small-business use; Commercial requires paid plan if revenue > US$10M/yr).
   After install: Settings → "Start Docker Desktop when you sign in" → ON.
   Then `cp deploy/launchd/com.docker.desktop-auto-restart.plist
   ~/Library/LaunchAgents/ && launchctl load
   ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist`.

5. **rclone** — `brew install rclone`. Configure the B2 remote:
   ```bash
   rclone config   # interactive; choose "Backblaze B2", paste account_id + key
   ```
   Set `B2_BUCKET` in `.env`.

6. **apcupsd** — `brew install apcupsd`. Configure per Step D.

7. **Install all launchd plists**:
   ```bash
   cp deploy/launchd/com.docker.desktop-auto-restart.plist ~/Library/LaunchAgents/
   cp deploy/launchd/com.apc.shutdown.plist                ~/Library/LaunchAgents/
   cp deploy/launchd/com.ev.backup.plist                   ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist
   launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
   launchctl load ~/Library/LaunchAgents/com.ev.backup.plist
   ```

8. **Clone + configure**:
   ```bash
   git clone https://github.com/rollroyces/ev-wallet-hk.git ~/projects/ev-wallet-hk
   cd ~/projects/ev-wallet-hk
   cp .env.example .env
   # Edit .env — fill every secret. See README.md "Secret generation".
   ```

## I. Smoke test the stack

```bash
cd ~/projects/ev-wallet-hk
mkdir -p logs

# Bring everything up
docker compose up -d
docker compose ps            # all services "healthy"

# Health
curl -fsS https://api.evwallet.com.hk/healthz     # expect "ok"
curl -fsS https://api.evwallet.com.hk/readyz      # expect "ready"

# Backup pipeline
./scripts/backup.sh
# Expect: "[I, T] Backup complete. Total local usage: ..." + rclone copy line
# Verify B2:
rclone ls b2:ev-wallet-backups/mac-mini-prod/daily/ --config ~/.config/rclone/rclone.conf

# Confirm the daily cron is actually scheduled
launchctl list | grep -i ev.backup
# Expect: <pid> 0 com.ev.backup
tail -f logs/backup.log
```

If `readyz` returns 503, the stack is up but DB or Redis is unreachable from
FastAPI. Check `docker compose logs fastapi` — the most common cause is the
FastAPI container can't reach the postgres/redis container on the Docker network
(check `EVW_POSTGRES_HOST` / `EVW_REDIS_HOST` in `.env` — should be
`postgres` / `redis`, not `127.0.0.1`).

## J. Disaster recovery

The hard part of running a Mac mini in a small office isn't uptime — it's the
disaster scenarios. Verify them now, not during the outage.

| Scenario | Test | Expected |
|---|---|---|
| Single disk failure | None (live test would be destructive) | Restore from B2 in ~2h |
| Power loss + UPS drains | Pull wall plug | `apcaccess` shows `ONBATT`; `com.apc.shutdown` triggers `shutdown -h now` within ~60 s |
| Corrupt Postgres | `pg_resetwal` then start | Boot fails; restore from B2 |
| Mac mini stolen | N/A | B2 has all data; Tunnel token revoke + new box restores service |
| Bad SQL drops a table | Run on staging | Enable WAL archiving (TODO — currently only daily snapshots) |

**UPS auto-shutdown verification** (do this in person with the monitor
attached):

```bash
# Watch the logs
tail -f logs/apc-shutdown.log
# Pull the wall plug
# Within 30–60 s you should see "Battery critical, shutting down" and the Mac
# powers off cleanly. Plug it back in; pmset should auto-restart within 5 s
# of AC return.
```

**B2 download drill** (quarterly, on a laptop or VM):

```bash
rclone copy b2:ev-wallet-backups/mac-mini-prod/daily/$(date -v-7d +%Y%m%d)/ /tmp/restore \
  --config /tmp/rclone.conf
ls -R /tmp/restore   # confirm pg/ and redis/ subdirs exist and are non-empty
```

Restore procedure is fully documented in `docs/BACKUP.md` § "When the box
dies". Run `./scripts/restore.sh 20260913 --dry-run` quarterly on a
non-production machine to confirm the restore path still works.

---

## Cost rollup

| Item | Cost |
|---|---|
| Mac mini M4 (one-time) | ~HK$4,500 |
| APC BE600M2 UPS (one-time) | ~HK$280 |
| `.com.hk` domain | HK$80–400/yr |
| Backblaze B2 | ~HK$7/mo (10 GB tier) |
| Apple Developer Program | US$99/yr |
| Cloudflare (free plan) | HK$0 |
| Docker Desktop / rclone / apcupsd / Postgres / Redis / n8n / FastAPI | HK$0 |
| Stripe HK entity (via Atlas) | one-time ~US$500 |
| **Monthly recurring** | **~HK$130 + US$99/yr Apple** |

vs. AWS HK (ap-east-1 ECS + RDS + ElastiCache + ALB + NAT): ~HK$2,000+/mo at
idle.