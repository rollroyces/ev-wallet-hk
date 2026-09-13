# Backups — EV Wallet HK

This document explains how the backup system works, how to verify it's healthy, and how to recover when (not if) the Mac mini dies.

## TL;DR

- **Daily 03:00 HKT:** `pg_basebackup` + Redis BGSAVE → local `/Users/hermes/ev-wallet/backups/` → Backblaze B2
- **Retention:** 7 daily + 4 weekly + 6 monthly on B2
- **Restore:** `./scripts/restore.sh 20260913` (interactive; ~10 min downtime)
- **Verify weekly:** `./scripts/verify-backup.sh` (last 3 days)

## Why `pg_basebackup`, not `pg_dump`

`pg_dump` produces a logical export. It is consistent at the moment it runs but does not contain the WAL stream — meaning any change committed between the dump and a crash is unrecoverable. For a wallet's double-entry ledger, that's a deal-breaker.

`pg_basebackup` takes a consistent snapshot of the entire data cluster plus its WAL files. With WAL archiving enabled (TODO: add to deploy), this is PITR-eligible: you can recover to any point in time. We trade a slightly larger backup size for actual recoverability.

## What gets backed up

| Source | Tool | Size estimate | Frequency |
|---|---|---|---|
| Postgres cluster (binary + WAL) | `pg_basebackup -Ft -z -Xs` | ~50-200 MB compressed | Daily |
| Redis RDB | `BGSAVE` + copy | 1-10 MB | Daily |
| Cloudflare Tunnel token | Manual note in password manager | <1 KB | N/A (regenerable) |
| Stripe / Apple Pay keys | Stripe dashboard, Apple Developer | N/A | N/A |

**What is NOT backed up here:** Docker images (re-pullable), FastAPI source code (in this git repo), n8n workflows (export from n8n UI → also store in this repo).

## What lives where

```
~/ev-wallet/backups/
├── daily/
│   ├── 20260913-030000/
│   │   ├── pg/
│   │   │   ├── base.tar.gz      # cluster snapshot
│   │   │   └── pg_wal.tar.gz    # WAL captured at backup time
│   │   └── redis/
│   │       └── dump-*.rdb
│   └── 20260914-030000/
└── ...
```

B2 layout:
```
ev-wallet-backups/
├── mac-mini-prod/
│   ├── daily/{stamp}/
│   ├── weekly/{stamp}/    # Sundays
│   └── monthly/{stamp}/   # 1st of month
```

## Setting up B2

1. Sign up at https://www.backblaze.com/b2 (free tier: 10 GB)
2. Create a bucket: `ev-wallet-backups` (private, encryption enabled)
3. Create an application key with read/write on this bucket only
4. Put the credentials in `.env`:
   ```
   B2_ACCOUNT_ID=...
   B2_APPLICATION_KEY=...
   B2_BUCKET=ev-wallet-backups
   ```

Without B2 set up, backups run *locally only*. This is OK for development; it's reckless for production. The first sign of a real outage on a Mac mini without offsite backup is irrecoverable ledger loss.

## Daily backup — manual run

```bash
cd ~/projects/ev-wallet-hk
./scripts/backup.sh
```

Expected output: `[I, T] Backup starting ... [I, T] Backup complete. Total local usage: ...`

Common failures:
- **"FATAL: pg_basebackup failed"** — Postgres not running. `docker compose ps` to check.
- **"FATAL: rclone to B2 failed"** — B2 credentials wrong or bucket missing.
- **"WARN: Redis BGSAVE failed"** — non-fatal; we proceed.

## Restore — the worst day of your life

Before this script existed, you'd be re-typing hundreds of lines of SQL by hand. With it:

```bash
cd ~/projects/ev-wallet-hk
./scripts/restore.sh 20260913              # restore that day's backup
./scripts/restore.sh 20260913 --dry-run    # see what would happen
```

The script is interactive — it asks you to type `RESTORE` before doing anything destructive.

**Important: test the restore before you need it.** Schedule a quarterly restore drill:

```bash
# On a SECOND Mac or in a VM:
git clone https://github.com/rollroyces/ev-wallet-hk.git
cd ev-wallet-hk
./scripts/restore.sh $(date -d '7 days ago' +%Y%m%d) --dry-run
```

If `--dry-run` lists files that match what you'd expect from 7 days ago, you're OK.

## Verify weekly — last 3 days

```bash
./scripts/verify-backup.sh
```

This script:
1. Lists the last 3 daily backups on disk
2. Lists the last 3 daily backups on B2
3. Verifies each is non-empty
4. Optionally: `pg_waldump` on the latest to confirm WAL is parseable

If verify fails: stop, investigate, fix the backup path *before* the next outage.

## When the box dies

1. **Don't panic.** Your last good backup is in B2.
2. Get a replacement Mac mini.
3. Install macOS, run `energy.sh`, install Docker.
5. Clone this repo.
6. Pull the latest backup from B2:
   ```bash
   rclone copy b2:ev-wallet-backups/mac-mini-prod/daily/<stamp> /tmp/restore
   ```
7. Run `./scripts/restore.sh /tmp/restore/<stamp>`
8. Update DNS — Cloudflare Tunnel token is unchanged, so no DNS update needed.
9. Smoke test: `curl https://api.evwallet.com.hk/healthz`

Total downtime: ~2-4 hours if you have a spare Mac mini ready, ~24 hours if you need to source one.

## What this won't save you from

| Scenario | Recoverable? |
|---|---|
| Single disk failure (NVMe dies) | ✅ B2 |
| macOS update bricks the boot | ✅ B2 + reinstall |
| FileVault password forgotten | ❌ start over (loses everything since last B2 backup) |
| Office fire | ✅ B2 (in different region) |
| Stolen box, FVault on | ✅ B2 + reinstall |
| Bad SQL drops a table mid-day | ⚠ only if you enable WAL archiving (TODO) |
| Ransomware encrypts live data + mounted B2 | ⚠ only if B2 has immutable object lock (paid B2 feature) |

If "single disk failure" or "office fire" keeps you up at night, the answer is B2 with a separate account credential stored in 1Password — not a more elaborate backup system.

## Ledger integrity red flags

If Postgres logs contain ANY of the following, **stop and investigate**:

- `WAL flush skipped`
- `WAL file removed`
- `could not write to file ... No space left`
- `database is not accepting commands`

These are precursors to ledger corruption. Do not ignore them. Restore from B2 before continuing.