#!/usr/bin/env bash
# =============================================================================
# UPS monitor — invoked every 30s by launchd.
# Reads apcupsd status; if on battery AND time-left < threshold,
# gracefully shut down the wallet stack.
#
# This is the LAST line of defense against power-loss ledger corruption.
# macOS will continue to run on battery for a few minutes; we want to
# reach a clean shutdown well before that.
# =============================================================================

set -u

THRESHOLD_SECONDS="${UPS_BATTERY_THRESHOLD_SECONDS:-60}"
LOG="/Users/hermes/projects/ev-wallet-hk/logs/ups-monitor.log"
COMPOSE_DIR="/Users/hermes/projects/ev-wallet-hk"

mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

# Pull status from apcupsd. If apcupsd not running, log and exit silently.
STATUS="$(apcaccess status 2>/dev/null || true)"
if [[ -z "$STATUS" ]]; then
    # apcaccess unavailable — likely apcupsd not installed/running.
    # Don't spam logs; only log first occurrence per session.
    exit 0
fi

# Parse key fields
LINE=$(echo "$STATUS" | grep -i "^STATUS"        | awk -F': ' '{print $2}' | tr -d ' ')
TIMELEFT=$(echo "$STATUS" | grep -i "^TIMELEFT"  | awk -F': ' '{print $2}' | tr -d ' ')

if [[ "$LINE" != "ONLINE" ]] && [[ -n "$TIMELEFT" ]]; then
    # Parse "X.X Minutes" or "X Seconds"
    if [[ "$TIMELEFT" == *"Minutes"* ]]; then
        MINS=$(echo "$TIMELEFT" | awk '{print int($1)}')
        SECS=$((MINS * 60))
    else
        SECS=$(echo "$TIMELEFT" | awk '{print int($1)}')
    fi

    log "UPS on battery, time left: ${SECS}s (threshold: ${THRESHOLD_SECONDS}s)"

    if [[ "$SECS" -lt "$THRESHOLD_SECONDS" ]]; then
        log "CRITICAL: time left ${SECS}s < threshold ${THRESHOLD_SECONDS}s"
        log "Initiating graceful shutdown of EV Wallet stack..."
        cd "$COMPOSE_DIR"
        docker compose down 2>&1 >> "$LOG" || true
        log "Stack stopped. Triggering macOS shutdown..."
        sudo /sbin/shutdown -h now "UPS critical: EV Wallet stack stopped" 2>&1 >> "$LOG" || true
    fi
fi