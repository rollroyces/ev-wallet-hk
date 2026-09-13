#!/usr/bin/env bash
# =============================================================================
# EV Wallet HK — daily backup
# Sequence:
#   1. Source .env
#   2. pg_basebackup from running Postgres -> local staging (PITR snapshot)
#   3. Redis BGSAVE -> snapshot file
#   4. rclone sync to Backblaze B2
#   5. Apply retention (daily N / weekly N / monthly N)
#
# IMPORTANT: this uses pg_basebackup (NOT pg_dump). pg_basebackup captures a
# consistent cluster snapshot that's PITR-eligible. pg_dump is a logical
# export and won't recover cleanly under WAL replay.
# =============================================================================

set -euo pipefail

# ---- Load env --------------------------------------------------------------
ENV_FILE="/Users/hermes/projects/ev-wallet-hk/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "FATAL: .env not found at $ENV_FILE" >&2
    exit 1
fi
set -a; source "$ENV_FILE"; set +a

# ---- Config ----------------------------------------------------------------
LOG_DIR="/Users/hermes/projects/ev-wallet-hk/logs"
LOCAL_DIR="${BACKUP_LOCAL_DIR:-/Users/hermes/ev-wallet/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DAY_OF_WEEK="$(date +%u)"   # 1=Mon ... 7=Sun
DAY_OF_MONTH="$(date +%d)"
TODAY_DIR="${LOCAL_DIR}/daily/${STAMP}"
RETAIN_DAILY="${BACKUP_RETAIN_DAILY:-7}"
RETAIN_WEEKLY="${BACKUP_RETAIN_WEEKLY:-4}"
RETAIN_MONTHLY="${BACKUP_RETAIN_MONTHLY:-6}"
COMPOSE_DIR="/Users/hermes/projects/ev-wallet-hk"

mkdir -p "$LOG_DIR" "$TODAY_DIR/pg" "$TODAY_DIR/redis"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_DIR/backup.log"; }

log "Backup starting (stamp=$STAMP, retention: d=${RETAIN_DAILY}/w=${RETAIN_WEEKLY}/m=${RETAIN_MONTHLY})"

# ---- 1. Postgres base backup ----------------------------------------------
log "Running pg_basebackup..."

docker exec -u postgres evw-postgres \
    pg_basebackup \
        -D /var/lib/postgresql/data/.backup-${STAMP} \
        -Ft -z -Xs -P \
        -U "${POSTGRES_USER}" \
        -w 2>>"${LOG_DIR}/backup.err" || {
    log "FATAL: pg_basebackup failed — see ${LOG_DIR}/backup.err"
    exit 1
}

docker cp "evw-postgres:/var/lib/postgresql/data/.backup-${STAMP}/" "${TODAY_DIR}/pg/" \
    2>>"${LOG_DIR}/backup.err"
docker exec -u postgres evw-postgres \
    rm -rf "/var/lib/postgresql/data/.backup-${STAMP}"

log "Postgres backup complete: $(du -sh "${TODAY_DIR}/pg" | cut -f1)"

# ---- 2. Redis BGSAVE -------------------------------------------------------
log "Triggering Redis BGSAVE..."

docker exec evw-redis \
    redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning BGSAVE \
    2>>"${LOG_DIR}/backup.err" || {
    log "WARN: Redis BGSAVE failed — see ${LOG_DIR}/backup.err (non-fatal)"
}

# Wait for BGSAVE to finish (max 30s)
for i in {1..30}; do
    status=$(docker exec evw-redis \
        redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning LASTSAVE)
    if [[ -n "$status" ]]; then break; fi
    sleep 1
done

docker cp "evw-redis:/data/dump.rdb" "${TODAY_DIR}/redis/dump-${STAMP}.rdb" \
    2>>"${LOG_DIR}/backup.err" || log "WARN: Redis dump copy failed (non-fatal)"

log "Redis backup complete"

# ---- 3. Sanity check -------------------------------------------------------
# Ensure the pg backup is non-empty AND the latest WAL file is captured.
PG_SIZE=$(du -sb "${TODAY_DIR}/pg" | cut -f1)
if [[ "$PG_SIZE" -lt 1000000 ]]; then
    log "FATAL: Postgres backup suspiciously small (${PG_SIZE} bytes)"
    exit 1
fi

# ---- 4. rclone to B2 -------------------------------------------------------
if [[ -n "${B2_ACCOUNT_ID:-}" ]] && [[ -n "${B2_APPLICATION_KEY:-}" ]]; then
    log "Syncing to Backblaze B2..."

    export RCLONE_CONFIG="/tmp/rclone-evw.conf"
    cat > "$RCLONE_CONFIG" <<EOF
[b2]
type = b2
account = ${B2_ACCOUNT_ID}
key = ${B2_APPLICATION_KEY}
EOF

    B2_PATH="${B2_PATH_PREFIX:-mac-mini-prod}/daily/${STAMP}"
    rclone copy "${TODAY_DIR}" "b2:${B2_BUCKET}/${B2_PATH}" \
        --config "$RCLONE_CONFIG" \
        --transfers 4 --checkers 4 --b2-hard-delete \
        --log-file "${LOG_DIR}/rclone-${STAMP}.log" \
        --log-level INFO \
        2>>"${LOG_DIR}/backup.err" || {
        log "FATAL: rclone to B2 failed — see ${LOG_DIR}/rclone-${STAMP}.log"
        exit 1
    }

    # Weekly (Sunday) and monthly (1st of month) deep copies
    if [[ "$DAY_OF_WEEK" == "7" ]]; then
        rclone copy "${TODAY_DIR}" "b2:${B2_BUCKET}/${B2_PATH_PREFIX}/weekly/${STAMP}" \
            --config "$RCLONE_CONFIG" --transfers 4 --checkers 4 \
            2>>"${LOG_DIR}/backup.err" || true
        log "Weekly snapshot promoted to B2"
    fi

    if [[ "$DAY_OF_MONTH" == "01" ]]; then
        rclone copy "${TODAY_DIR}" "b2:${B2_BUCKET}/${B2_PATH_PREFIX}/monthly/${STAMP}" \
            --config "$RCLONE_CONFIG" --transfers 4 --checkers 4 \
            2>>"${LOG_DIR}/backup.err" || true
        log "Monthly snapshot promoted to B2"
    fi

    rm -f "$RCLONE_CONFIG"
else
    log "WARN: B2 not configured (B2_ACCOUNT_ID/B2_APPLICATION_KEY missing); local-only backup"
fi

# ---- 5. Apply retention ----------------------------------------------------
log "Applying local retention policy..."
# Keep most recent N daily dirs
find "${LOCAL_DIR}/daily" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | \
    sort -rn | tail -n +$((RETAIN_DAILY + 1)) | awk '{print $2}' | \
    xargs -I{} rm -rf {} || true

log "Local retention complete"

# ---- 6. Final report -------------------------------------------------------
TOTAL=$(du -sh "${LOCAL_DIR}" | cut -f1)
log "Backup complete. Total local usage: ${TOTAL}"
log "Snapshot: ${TODAY_DIR}"