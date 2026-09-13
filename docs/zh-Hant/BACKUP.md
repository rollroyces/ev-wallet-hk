# 備份 — EV Wallet HK

本文件說明備份系統如何運作、如何驗證其健康狀態，以及當（不是「如果」）Mac mini 損壞時如何復原。

## TL;DR

- **每日 03:00 HKT：** `pg_basebackup` + Redis `BGSAVE` → 本機 `/Users/hermes/ev-wallet/backups/` → Backblaze B2
- **保留策略：** B2 上保留 7 個日備份 + 4 個週備份 + 6 個月備份
- **還原：** `./scripts/restore.sh 20260913`（互動式；約 10 分鐘 downtime）
- **每週驗證：** `./scripts/verify-backup.sh`（最近 3 日的備份）

## 為何用 `pg_basebackup`，不用 `pg_dump`

`pg_dump` 產生的是邏輯匯出。它在執行當下一刻是一致的，但**不含 WAL stream** — 意即在 dump 與 crash 之間任何已 commit 的變更都無法救回。對錢包的雙分錄分類帳來說，這是不可接受的。

`pg_basebackup` 會對整個 data cluster 及其 WAL 檔案做一致性的快照。若啟用 WAL archiving（TODO：需在 deploy 加入），即可達成 PITR（point-in-time recovery）：可以還原到任意時間點。我們用稍大的備份體積換來真正的可還原性。

## 備份內容

| 來源 | 工具 | 大小估算 | 頻率 |
|---|---|---|---|
| Postgres cluster（二進位 + WAL） | `pg_basebackup -Ft -z -Xs` | ~50–200 MB 壓縮 | 每日 |
| Redis RDB | `BGSAVE` + copy | 1–10 MB | 每日 |
| Cloudflare Tunnel token | 手抄至密碼管理工具 | <1 KB | 不適用（可重新產生） |
| Stripe / Apple Pay keys | Stripe dashboard、Apple Developer | 不適用 | 不適用 |

**此處未備份：** Docker images（可重新 pull）、FastAPI 原始碼（在此 git repo）、n8n workflows（從 n8n UI 匯出 → 亦存放於此 repo）。

## 檔案分佈

```
~/ev-wallet/backups/
├── daily/
│   ├── 20260913-030000/
│   │   ├── pg/
│   │   │   ├── base.tar.gz      # cluster 快照
│   │   │   └── pg_wal.tar.gz    # 備份當下的 WAL
│   │   └── redis/
│   │       └── dump-*.rdb
│   └── 20260914-030000/
└── ...
```

B2 結構：
```
ev-wallet-backups/
├── mac-mini-prod/
│   ├── daily/{stamp}/     # 每日
│   ├── weekly/{stamp}/    # 每週（週日）
│   └── monthly/{stamp}/   # 每月（1 號）
```

## 設定 B2

1. 前往 <https://www.backblaze.com/b2> 註冊（free tier：10 GB）
2. 建立 bucket：`ev-wallet-backups`（private、啟用 encryption）
3. 建立 Application Key，權限僅限此 bucket 的 read/write
4. 將 credentials 寫入 `.env`：
   ```
   B2_ACCOUNT_ID=...
   B2_APPLICATION_KEY=...
   B2_BUCKET=ev-wallet-backups
   ```

若未設定 B2，備份只會寫到本機 — 開發階段沒問題，但**在生產環境屬於魯莽行為**。Mac mini 若無異地備份而遇到真正的 outage，第一個訊號就是 ledger 永久遺失。

## 每日備份 — 手動執行

```bash
cd ~/projects/ev-wallet-hk
./scripts/backup.sh
```

預期輸出：`[I, T] Backup starting ... [I, T] Backup complete. Total local usage: ...`

常見失敗：
- **`FATAL: pg_basebackup failed`** — Postgres 沒在跑。`docker compose ps` 確認。
- **`FATAL: rclone to B2 failed`** — B2 credentials 錯誤或 bucket 不存在。
- **`WARN: Redis BGSAVE failed`** — 非致命；會繼續執行。

## 還原 — 你人生中最黑暗的一天

在這個腳本出現之前，你要一行一行 SQL 重建。有了它：

```bash
cd ~/projects/ev-wallet-hk
./scripts/restore.sh 20260913              # 還原當日備份
./scripts/restore.sh 20260913 --dry-run    # 預覽會發生什麼事
```

腳本為互動式 — 在執行任何破壞性動作前，會要求你輸入 `RESTORE`。

**重要：在你需要之前就測過還原。** 排程每季做一次還原演練：

```bash
# 在另一部 Mac 或 VM 上：
git clone https://github.com/rollroyces/ev-wallet-hk.git
cd ev-wallet-hk
./scripts/restore.sh $(date -d '7 days ago' +%Y%m%d) --dry-run
```

若 `--dry-run` 列出的檔案與 7 天前的預期一致，便代表沒問題。

## 每週驗證 — 最近 3 日

```bash
./scripts/verify-backup.sh
```

此腳本會：
1. 列出本機最近 3 個日備份
2. 列出 B2 上最近 3 個日備份
3. 確認每個備份非空
4. （可選）對最新備份執行 `pg_waldump` 確認 WAL 可解析

若驗證失敗：先停下來調查，在下一次 outage 之前**先把備份路徑修好**。

## 當機器損壞時

1. **不要慌。** 你最後一個正常的備份在 B2。
2. 取得一部新的 Mac mini。
3. 安裝 macOS，執行 `energy.sh`，安裝 Docker。
5. Clone 此 repo。
6. 從 B2 拉回最新備份：
   ```bash
   rclone copy b2:ev-wallet-backups/mac-mini-prod/daily/<stamp> /tmp/restore
   ```
7. 執行 `./scripts/restore.sh /tmp/restore/<stamp>`
8. 更新 DNS — Cloudflare Tunnel token 不變，所以不需要動 DNS。
9. Smoke test：`curl https://api.evwallet.com.hk/healthz`

總 downtime：若手邊有現成 Mac mini，約 2–4 小時；若需要採購，約 24 小時。

## 救不了你的情境

| 情境 | 可還原？ |
|---|---|
| 單顆磁碟故障（NVMe 損壞） | ✅ B2 |
| macOS 更新把開機毀了 | ✅ B2 + 重灌 |
| FileVault 密碼遺忘 | ❌ 從頭開始（自上次 B2 備份以來的資料全部遺失） |
| 辦公室火災 | ✅ B2（在異地） |
| 機器被偷、FileVault 已開 | ✅ B2 + 重灌 |
| 有人中午跑了一條 bad SQL 砍掉 table | ⚠ 需啟用 WAL archiving（TODO） |
| 勒索軟體同時加密現有資料 + 已掛載的 B2 | ⚠ 需 B2 啟用 immutable object lock（B2 付費功能） |

若「單顆磁碟故障」或「辦公室火災」讓你睡不著，答案是「B2 + 獨立帳號憑證存放於 1Password」 — 而不是更複雜的備份系統。

## Ledger 完整性紅旗

若 Postgres log 出現**任何一項**，**請立即停下來調查**：

- `WAL flush skipped`
- `WAL file removed`
- `could not write to file ... No space left`
- `database is not accepting commands`

以上都是 ledger 損毀的前兆。切勿忽視 — 從 B2 還原後再繼續。