#!/usr/bin/env bash
# =============================================================================
# EV Wallet HK — restore a backup
# USE: ./restore.sh <STAMP-or-DATE> [--dry-run]
#   STAMP: e.g. 20260913-030000  (full timestamp)
#   DATE:  e.g. 20260913          (latest backup that day)
#   --dry-run: list what would happen; don't touch anything
#
# WARNING: this is destructive. It WILL stop running services and rebuild the
# Postgres data directory from the backup snapshot. Test in a throwaway VM first.
# =============================================================================

set -euo pipefail

STAMP_OR_DATE="${1:-}"
DRY_RUN="false"
if [[ "${2:-}" == "--dry-run" ]]; then DRY_RUN="true"; fi

if [[ -z "$STAMP_OR_DATE" ]]; then
    echo "Usage: $0 <stamp-or-date> [--dry-run]" >&2
    exit 1
fi

# ---- Load env --------------------------------------------------------------
ENV_FILE="/Users/hermes/projects/ev-wallet-hk/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "FATAL: .env not found at $ENV_FILE" >&2
    exit 1
fi
set -a; source "$ENV_FILE"; set +a

LOCAL_DIR="${BACKUP_LOCAL_DIR:-/Users/hermes/ev-wallet/backups}"
COMPOSE_DIR="/Users/hermes/projects/ev-wallet-hk"

# ---- Resolve backup directory ---------------------------------------------
BACKUP_PATH=""
if [[ -d "${LOCAL_DIR}/daily/${STAMP_OR_DATE}" ]]; then
    BACKUP_PATH="${LOCAL_DIR}/daily/${STAMP_OR_DATE}"
else
    # Find the latest daily dir matching date prefix
    MATCHES=$(find "${LOCAL_DIR}/daily" -maxdepth 1 -type d -name "${STAMP_OR_DATE}*" 2>/dev/null | sort | tail -1)
    if [[ -n "$MATCHES" ]] && [[ -d "$MATCHES" ]]; then
        BACKUP_PATH="$MATCHES"
    fi
fi

if [[ -z "$BACKUP_PATH" ]]; then
    echo "FATAL: no backup found matching '${STAMP_OR_DATE}'" >&2
    echo "Available:" >&2
    ls -1 "${LOCAL_DIR}/daily/" 2>&1 || echo "  (no daily backups)" >&2
    exit 1
fi

echo "[restore] Using backup: ${BACKUP_PATH}"
echo "[restore] Dry-run: ${DRY_RUN}"
echo "[restore] Contents:"
ls -lah "${BACKUP_PATH}" "${BACKUP_PATH}/pg" "${BACKUP_PATH}/redis" 2>&1

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[restore] --dry-run: exiting without action"
    exit 0
fi

echo
echo "!!! WARNING: this will:"
echo "    1. Stop all EV Wallet services"
echo "    2. WIPE the Postgres data volume (currently in 'evw-pgdata')"
echo "    3. WIPE Redis AOF"
echo "    4. Rebuild Postgres from ${BACKUP_PATH}/pg"
echo "    5. Restore Redis snapshot from ${BACKUP_PATH}/redis"
echo "    6. Bring services back up"
echo
echo -n "Type 'RESTORE' to proceed: "
read -r CONFIRM
if [[ "$CONFIRM" != "RESTORE" ]]; then
    echo "Aborted."
    exit 1
fi

cd "$COMPOSE_DIR"

echo "[restore] Stopping services..."
docker compose down

echo "[restore] Removing old data volumes..."
docker volume rm evw-pgdata evw-redisdata 2>&1 || true

echo "[restore] Creating fresh volumes..."
docker volume create evw-pgdata
docker volume create evw-redisdata

echo "[restore] Starting Postgres temporarily to restore..."
docker compose up -d postgres redis

echo "[restore] Waiting for Postgres to be ready..."
for i in {1..30}; do
    if docker exec evw-postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "[restore] Stopping Postgres for restore..."
docker compose stop postgres

echo "[restore] Wiping new data dir and replacing with backup..."
docker run --rm \
    -v evw-pgdata:/var/lib/postgresql/data \
    -v "${BACKUP_PATH}/pg:/backup:ro" \
    alpine:3.20 sh -c '
        rm -rf /var/lib/postgresql/data/*
        tar -xzf /backup/base.tar.gz -C /var/lib/postgresql/data
        if [ -f /backup/pg_wal.tar.gz ]; then
            mkdir -p /var/lib/postgresql/data/pg_wal
            tar -xzf /backup/pg_wal.tar.gz -C /var/lib/postgresql/data/pg_wal
        fi
        chown -R 999:999 /var/lib/postgresql/data
    '

echo "[restore] Restoring Redis snapshot..."
docker compose start redis
sleep 2
docker exec evw-redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning SHUTDOWN NOSAVE 2>&1 || true
docker run --rm \
    -v evw-redisdata:/data \
    -v "${BACKUP_PATH}/redis:/backup:ro" \
    alpine:3.20 sh -c '
        cp /backup/*.rdb /data/dump.rdb
        chown 999:999 /data/dump.rdb
    '

echo "[restore] Restarting Postgres..."
docker compose start postgres
sleep 3

echo "[restore] Bringing all services up..."
docker compose up -d

echo "[restore] DONE. Verify with:"
echo "    docker compose ps"
echo "    docker exec evw-postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c '\\dt'"
echo "    curl https://api.evwallet.com.hk/healthz"