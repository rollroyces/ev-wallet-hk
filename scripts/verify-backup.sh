#!/usr/bin/env bash
# Verify the last 3 daily backups are present on disk and B2, and non-empty.

set -euo pipefail

ENV_FILE="/Users/hermes/projects/ev-wallet-hk/.env"
[[ -f "$ENV_FILE" ]] || { echo "FATAL: .env not found"; exit 1; }
set -a; source "$ENV_FILE"; set +a

LOCAL_DIR="${BACKUP_LOCAL_DIR:-/Users/hermes/ev-wallet/backups}"
FAIL=0

echo "=== Local backups (last 3) ==="
mapfile -t LOCAL_BACKUPS < <(find "${LOCAL_DIR}/daily" -maxdepth 1 -mindepth 1 -type d | sort | tail -3)
for B in "${LOCAL_BACKUPS[@]}"; do
    SIZE=$(du -sh "$B" | cut -f1)
    PG_SIZE=$(du -sb "$B/pg" 2>/dev/null | cut -f1 || echo "0")
    if [[ "$PG_SIZE" -lt 1000000 ]]; then
        echo "  ✗ $B  size=$SIZE  pg=$PG_SIZE  SUSPICIOUSLY SMALL"
        FAIL=1
    else
        echo "  ✓ $B  size=$SIZE  pg=$PG_SIZE"
    fi
done

if [[ -z "${B2_ACCOUNT_ID:-}" ]]; then
    echo
    echo "=== B2 backups: SKIPPED (no credentials) ==="
else
    export RCLONE_CONFIG="/tmp/rclone-evw-verify.conf"
    cat > "$RCLONE_CONFIG" <<EOF
[b2]
type = b2
account = ${B2_ACCOUNT_ID}
key = ${B2_APPLICATION_KEY}
EOF

    echo
    echo "=== B2 backups (last 3) ==="
    mapfile -t B2_BACKUPS < <(rclone lsf "b2:${B2_BUCKET}/${B2_PATH_PREFIX:-mac-mini-prod}/daily/" \
        --config "$RCLONE_CONFIG" --dirs-only 2>/dev/null | sort | tail -3)
    for B in "${B2_BACKUPS[@]}"; do
        SIZE=$(rclone size "b2:${B2_BUCKET}/${B2_PATH_PREFIX:-mac-mini-prod}/daily/${B}" \
            --config "$RCLONE_CONFIG" --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bytes',0))" 2>/dev/null || echo "?")
        echo "  ✓ b2:${B2_BUCKET}/${B2_PATH_PREFIX:-mac-mini-prod}/daily/${B}  size=${SIZE}B"
    done
    rm -f "$RCLONE_CONFIG"
fi

if [[ $FAIL -ne 0 ]]; then
    echo
    echo "FAIL: at least one local backup looks bad. Investigate."
    exit 1
fi

echo
echo "OK"