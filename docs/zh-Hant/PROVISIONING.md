# 部署建置（Provisioning）— EV Wallet HK

本文件是在香港以 Mac mini 跑 EV Wallet HK 生產環境時，建置（provision）所有外部服務與現場硬件所需的 operational runbook。請先從頭到尾讀一次再開始；每個步驟都會指向相關的 `.env` 設定，或背後已有的 runbook。

Tone：senior-engineer-to-senior-engineer。不囉嗦。若步驟不清楚，請打開連結的原始文件，**不要即興發揮**。

## A. Domain 與 DNS

1. 在 [HKIRC](https://www.hkirc.hk/) 透過 HKIS 認可的註冊商註冊 **`.com.hk`**（HK$80–400/年，依註冊年期而定）。若不需要 `.hk`，可改用 Cloudflare Registrar 的 `.com`（US$10/年，零加價）作為替代方案。
2. 在註冊商後台，將 **nameservers 指向 Cloudflare**。免費方案已足夠。Cloudflare 會給你兩個 nameserver hostname（`xxx.ns.cloudflare.com`）；把它們貼到註冊商的「custom nameservers」欄位，並移除註冊商預設的 NS 紀錄。
3. 等待 Cloudflare 偵測到委任（通常 <5 分鐘，最長 24 小時）。Cloudflare dashboard 會顯示該 domain 為 **"Active"**。
4. 在 Cloudflare DNS 新增三個生產 hostname。使用下方 Step B 的 Cloudflare Tunnel 路由即可 — **不要建立指向任何 public IP 的 CNAME**，因為我們根本沒有 public IP。`route dns` 指令會為你建立 CNAME 並把它綁定到 tunnel：
   - `api.evwallet.com.hk` → FastAPI（透過 Caddy）
   - `n8n.evwallet.com.hk` → n8n 管理介面
   - `app.evwallet.com.hk` → Next.js Web 入口網站（未來）

預估成本：HK$80–400/年 + Cloudflare HK$0。

## B. Cloudflare Tunnel（唯一對外入口）

Tunnel 是從 public internet 到 Mac mini 的**唯一**路徑。Caddy 只 listen loopback；cloudflared 對 Cloudflare edge 建立純外連（outbound-only）的連線。**沒有 port forwarding、沒有 public IP。**

完整步驟：見 [`deploy/cloudflared/SETUP.md`](../deploy/cloudflared/SETUP.md)。全新機器的 TL;DR：

```bash
brew install cloudflared              # 一次性
cloudflared tunnel login              # 開啟瀏覽器；選 evwallet.com.hk
cloudflared tunnel create ev-wallet-prod
cloudflared tunnel route dns ev-wallet-prod api.evwallet.com.hk
cloudflared tunnel route dns ev-wallet-prod n8n.evwallet.com.hk
cloudflared tunnel route dns ev-wallet-prod app.evwallet.com.hk
cloudflared tunnel token ev-wallet-prod     # 把結果貼到 .env
```

把 token 貼到 `.env` 的 `CLOUDFLARED_TUNNEL_TOKEN`。

**每 90 天輪換 token**（見 `SETUP.md` 第 8 步）。`~/.cloudflared/` 裡的 credential JSON 也建議備份到密碼管理工具 — 一旦遺失就必須重新建立 tunnel 與 DNS 路由。

## C. Backblaze B2（異地備份）

1. 前往 <https://www.backblaze.com/b2> 註冊。Free tier：**10 GB** — 以我們規模，存放多年壓縮後的 `pg_basebackup` 快照仍然綽綽有餘。
2. 建立 bucket：**`ev-wallet-backups`**（private、啟用 encryption）。Lifecycle 規則：**保留 7 個日備份 + 4 個週備份 + 6 個月備份**。較舊的 object 會自動 expire。（備份內容與選用 B2 的原因見 `docs/BACKUP.md`。）
3. 建立 **Application Key**，scope 限於**此 bucket**，權限為 `listFiles`、`listBuckets`、`readFiles`、`writeFiles`、`deleteFiles`。**不要**在 `.env` 裡直接放 master account key。
4. 寫入 `.env`：
   ```
   B2_ACCOUNT_ID=...
   B2_APPLICATION_KEY=...
   B2_BUCKET=ev-wallet-backups
   B2_PATH_PREFIX=mac-mini-prod
   ```

若沒設定 B2，`scripts/backup.sh` 只會備份到本機 — 開發階段可接受，**生產環境屬於魯莽行為**。

## D. UPS 硬件

**原因：** 充電中途突然斷電會把 Postgres WAL 寫到一半的資料毀掉。對錢包的雙分錄分類帳來說（我們無法從外部來源重建），corruption == 餘額永遠對不上，而且無法救回。UPS 是我們能買到的最便宜保險。

建議：**APC Back-UPS BE600M2**（~HK$280，USB 介面，600 VA / 330 W — Mac mini M4 全載仍然夠用）。任何 APC USB UPS 都可以；非 USB UPS 需改用不同線材設定（`UPSCABLE simple`、`UPSTYPE simple`）。

1. 將 Mac mini 插到 UPS，再把 UPS 插到牆壁插座。
2. `brew install apcupsd`
3. 編輯 `/opt/homebrew/etc/apcupsd/apcupsd.conf`：
   ```
   UPSCABLE usb
   UPSTYPE usb
   DEVICE
   ```
   （`DEVICE` 留空 — apcupsd 會自動偵測 USB UPS。）
4. `brew services start apcupsd`
5. 驗證：`apcaccess status` → 應見 `STATUS   : ONLINE`，以及非零的 `LINEV` / `BCHARGE` / `TIMELEFT`。
6. 安裝自動關機 plist：
   ```bash
   cp deploy/launchd/com.apc.shutdown.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
   ```
   此 plist 透過 `scripts/ups-monitor.sh` 監察 UPS 狀態，當電量 < 30% 且在電池模式時執行 `shutdown -h now`。請依 Step J 拔插頭測試。

## E. Apple Developer 設定（Sign-In + Apple Pay）

1. 申請加入 **Apple Developer Program**（US$99/年）。接受 HK 公司或 HK 個人申請。審批約需 24–48 小時。
2. 在 App Store Connect / Certificates, Identifiers & Profiles：
   - **App ID**：`com.evwallet.hk` — 啟用 **Sign-In with Apple** capability。
   - **Services ID**：`com.evwallet.hk.web` — 用於 Web Sign-In 驗證（Sign-In 不需要 merchant certificate，純 JWKS-based token 驗證）。
3. **Apple Pay**（merchant 端）：
   - **Merchant ID**：`merchant.com.evwallet.hk`
   - **Processing Certificate**（`merchant_identity.cer`）— 產生 CSR → 上傳 → 下載 `.cer`。
   - **Private key**（`.p8`）— 你當初用來產生 CSR 的那把 key。**請立即備份到密碼管理工具。** 一旦遺失就必須重做 merchant cert。
   - 把這兩個檔案儲存到 repo 以外的路徑（例如 `/Users/hermes/.evwallet/secrets/`）。在 `.env` 中引用：
     ```
     EVW_APPLE_PAY_MERCHANT_ID=merchant.com.evwallet.hk
     EVW_APPLE_PAY_MERCHANT_CERT_PATH=/Users/hermes/.evwallet/secrets/merchant_identity.cer
     EVW_APPLE_PAY_MERCHANT_KEY_PATH=/Users/hermes/.evwallet/secrets/merchant_key.p8
     ```

若 Apple Pay 在首版延後上線，把以上三個變數留空即可 — `Settings` 已將之宣告為 `Optional`，Apple Pay UI 會自動隱藏。

## F. Google Cloud 設定（OAuth + Google Pay）

1. 在 <https://console.cloud.google.com/> 建立 project（例如 `ev-wallet-prod`）。
2. 啟用 **Google+ API**（Sign-In 必備 — Google Identity Services 仍需此 API 才能取用 OAuth profile）。若想在 Web 上提供 Google Pay，亦需啟用 **Google Pay API**。
3. **OAuth Client ID**（Web application）：
   - Authorized JavaScript origin：`https://app.evwallet.com.hk`
   - Authorized redirect URI：`https://app.evwallet.com.hk/api/v1/auth/google/callback`（若使用 redirect flow；我們的後端用 implicit/id_token flow，所以可以省略。）
   - 寫入 `.env`：
     ```
     EVW_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
     EVW_GOOGLE_CLIENT_SECRET=...
     ```
4. **Service Account**（用於 Google Pay processing）：
   - IAM & Admin → Service Accounts → Create。Role：`Google Pay API` 或適當的最小 scope。
   - 建立 JSON key。儲存至 `/Users/hermes/.evwallet/secrets/gpay-sa.json`。
   - 在 `.env` 中引用：
     ```
     EVW_GOOGLE_PAY_MERCHANT_ID=...     # 你的 Google Pay merchant ID
     EVW_GOOGLE_SA_KEY_PATH=/Users/hermes/.evwallet/secrets/gpay-sa.json
     ```

## G. Stripe 設定（topup + webhook）

1. 在 <https://dashboard.stripe.com/> 建立帳號。**必須為 HK 公司實體**，才能順利接收 HK 發行的信用卡、避免跨境拒付率過高。若尚未有 HK 實體，可透過 Stripe Atlas 以 ~US$500 一次性成立。
2. **Payment Methods → 啟用 Apple Pay + Google Pay。** Wallet 端的解密由 Stripe 處理；你只需提供 Step E、Step F 的 merchant identifier。
3. 取得 **API keys**（先 test，後 live）。寫入：
   ```
   EVW_STRIPE_SECRET_KEY=sk_live_...     # 正式
   EVW_STRIPE_PUBLISHABLE_KEY=pk_live_...  # 僅 Web 入口使用
   ```
4. 建立 **Webhook endpoint**：
   - URL：`https://api.evwallet.com.hk/api/v1/payments/stripe/webhook`
   - 事件：`payment_intent.succeeded`、`payment_intent.payment_failed`、`charge.refunded`、`payout.paid`
5. 把 **signing secret** 寫入 `.env`：
   ```
   EVW_STRIPE_WEBHOOK_SECRET=whsec_...
   ```
6. 用 Stripe CLI smoke-test webhook：
   ```bash
   stripe listen --forward-to https://api.evwallet.com.hk/api/v1/payments/stripe/webhook
   stripe trigger payment_intent.succeeded
   ```

## H. Mac mini 首次開機 checklist

依下列順序在全新鏡像的 Mac mini 上執行。Mac mini 應為 **headless 安裝**（完成設定後不接螢幕 / 鍵盤） — 在建置階段先開 VNC / Screen Sharing，之後在 firewall 關掉 screen sharing port，所有遠端存取一律透過 Cloudflare Tunnel。

1. **電源設定** — 停電後自動重啟：
   ```bash
   sudo ./deploy/macos/energy.sh
   ```
   驗證：`pmset -g | grep -i restart` → `RestartPowerFailure: 1`。

2. **關閉「Find My Mac」** — Activation Lock 會讓日後合法的遠端 wipe 無法完成，導致裝置被新主人視為磚頭。
   System Settings → Apple ID → Find My → 關閉。系統會要求輸入 Apple ID 密碼。

3. **FileVault** — 預設應為開啟。驗證：
   ```bash
   fdesetup status
   ```
   若顯示 `Off`，請打開，並**把 recovery key 存到密碼管理工具**。

4. **Docker Desktop** — 從 docker.com 安裝 dmg（個人 / 小型商業免費；如年收入超過 US$10M 需付費 Commercial 方案）。安裝後到 Settings → 開啟「Start Docker Desktop when you sign in」。然後：
   ```bash
   cp deploy/launchd/com.docker.desktop-auto-restart.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist
   ```

5. **rclone** — `brew install rclone`。設定 B2 remote：
   ```bash
   rclone config   # 互動式；選 "Backblaze B2"，貼上 account_id + key
   ```
   並在 `.env` 中設定 `B2_BUCKET`。

6. **apcupsd** — `brew install apcupsd`。依 Step D 設定。

7. **安裝所有 launchd plist**：
   ```bash
   cp deploy/launchd/com.docker.desktop-auto-restart.plist ~/Library/LaunchAgents/
   cp deploy/launchd/com.apc.shutdown.plist                ~/Library/LaunchAgents/
   cp deploy/launchd/com.ev.backup.plist                   ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.docker.desktop-auto-restart.plist
   launchctl load ~/Library/LaunchAgents/com.apc.shutdown.plist
   launchctl load ~/Library/LaunchAgents/com.ev.backup.plist
   ```

8. **Clone + 設定**：
   ```bash
   git clone https://github.com/rollroyces/ev-wallet-hk.git ~/projects/ev-wallet-hk
   cd ~/projects/ev-wallet-hk
   cp .env.example .env
   # 編輯 .env — 填入所有 secret。請參考 README.md 的「Secret generation」一節。
   ```

## I. Smoke test 服務組合

### 0. 部署前檢查（在執行下方 smoke test 之前先做）

完成 Step A–G 之後，repository 內已附帶一份準備就緒檢查腳本，可以一次抓出最常見的部署問題（缺少工具、佔位符秘密、未安裝的 launchd plist 等）：

```bash
cd ~/projects/ev-wallet-hk
./scripts/provision_check.sh
```

輸出會以顏色區分：
- ✓ 綠色 = 通過
- ! 黃色 = 警告（例如缺少非必要 secret）
- ✗ 紅色 = 阻擋性失敗（部署前必須先解決）

腳本在準備就緒時 exit 0，遇到任何阻擋性問題時 exit 1。涵蓋的檢查段落：

- **A. 必要工具** — docker, docker compose, rclone, apcupsd, cloudflared
- **B. .env 秘密** — 所有 `EVW_*` 必填與選填變數都存在且非佔位符
- **C. 秘密強度** — `EVW_JWT_SECRET` 長度 ≥ 32
- **D. Docker 就緒** — daemon 有回應
- **E. Cloudflare tunnel** — token 存在且格式正確
- **F. Backblaze B2** — 兩把 key 都存在，rclone remote 已設定
- **G. UPS（apcupsd）** — `STATUS: ONLINE`
- **H. macOS 電源設定** — `autorestart` + `powernap` 已設定
- **I. launchd plist** — Docker auto-restart、UPS shutdown、daily backup 都已載入

如果腳本回報失敗，請先修復後重跑直到全綠。下方 smoke test 假設 pre-flight 已通過。

### 1. 啟動所有服務

```bash
cd ~/projects/ev-wallet-hk
mkdir -p logs

# 啟動所有服務
docker compose up -d
docker compose ps            # 所有服務應為 "healthy"

# 健康檢查
curl -fsS https://api.evwallet.com.hk/healthz     # 預期 "ok"
curl -fsS https://api.evwallet.com.hk/readyz      # 預期 "ready"

# 備份管線
./scripts/backup.sh
# 預期輸出："[I, T] Backup complete. Total local usage: ..." + rclone copy 一行
# 驗證 B2：
rclone ls b2:ev-wallet-backups/mac-mini-prod/daily/ --config ~/.config/rclone/rclone.conf

# 確認每日 cron 確實已排定
launchctl list | grep -i ev.backup
# 預期：<pid> 0 com.ev.backup
tail -f logs/backup.log
```

若 `readyz` 回 503，表示服務組合已起來但 FastAPI 無法連到 DB 或 Redis。請 `docker compose logs fastapi` — 最常見原因是 FastAPI container 無法透過 Docker network 連到 postgres/redis container（請檢查 `.env` 的 `EVW_POSTGRES_HOST` / `EVW_REDIS_HOST`，應為 `postgres` / `redis`，而非 `127.0.0.1`）。

## J. 災難復原

在小型辦公室跑 Mac mini 真正難的不是 uptime，而是災難情境。**現在就驗證**，不要等出事了才測。

| 情境 | 測試方式 | 預期結果 |
|---|---|---|
| 單顆磁碟故障 | 無法做 live test（會破壞資料） | 從 B2 還原，約 2 小時 |
| 停電 + UPS 耗電 | 拔掉牆壁插頭 | `apcaccess` 顯示 `ONBATT`；`com.apc.shutdown` 在約 60 秒內觸發 `shutdown -h now` |
| Postgres 損毀 | `pg_resetwal` 後啟動 | 開機失敗；從 B2 還原 |
| Mac mini 被偷 | N/A | 所有資料在 B2；revoke tunnel token + 新機器還原服務 |
| 有人跑 bad SQL 砍 table | 在 staging 執行 | 需啟用 WAL archiving（TODO，目前只有每日快照） |

**UPS 自動關機驗證**（現場親自做，需接螢幕）：

```bash
# 觀察 log
tail -f logs/apc-shutdown.log
# 拔掉牆壁插頭
# 30–60 秒內應看到「Battery critical, shutting down」，Mac 會整齊關機。
# 重新插回；pmset 應在恢復 AC 後 5 秒內自動重啟。
```

**B2 下載演練**（每季一次，在筆電或 VM 執行）：

```bash
rclone copy b2:ev-wallet-backups/mac-mini-prod/daily/$(date -v-7d +%Y%m%d)/ /tmp/restore \
  --config /tmp/rclone.conf
ls -R /tmp/restore   # 確認 pg/ 與 redis/ 子目錄存在且非空
```

完整的還原步驟見 `docs/BACKUP.md` 的「當機器損壞時」一節。每季於非生產機器執行一次 `./scripts/restore.sh 20260913 --dry-run`，確認還原路徑仍有效。

---

## 成本總覽

| 項目 | 成本 |
|---|---|
| Mac mini M4（一次性） | ~HK$4,500 |
| APC BE600M2 UPS（一次性） | ~HK$280 |
| `.com.hk` domain | HK$80–400/年 |
| Backblaze B2 | ~HK$7/月（10 GB 級距） |
| Apple Developer Program | US$99/年 |
| Cloudflare（free plan） | HK$0 |
| Docker Desktop / rclone / apcupsd / Postgres / Redis / n8n / FastAPI | HK$0 |
| Stripe HK 實體（透過 Atlas） | 一次性 ~US$500 |
| **每月經常性開支** | **~HK$130 + US$99/年 Apple** |

對比 AWS HK（ap-east-1 ECS + RDS + ElastiCache + ALB + NAT）：閒置狀態每月約 HK$2,000+。