#!/usr/bin/env bash
# =============================================================================
# Apply recommended macOS power settings for a server-class Mac mini.
# Run once after fresh macOS install. Idempotent.
#
# Settings applied:
#   - Wake-on-LAN enabled (Cloudflare Tunnel reconnect after sleep)
#   - Auto-restart after power loss (no manual intervention on blackouts)
#   - Power Nap disabled (no surprise writes during sleep)
#   - Wake-for-network-access enabled
#   - Display sleep disabled (no monitor expected)
#   - Disk sleep disabled
# =============================================================================

set -euo pipefail

echo "[energy] Applying macOS power settings..."

# Disable Power Nap (no background activity during sleep)
sudo pmset -a powernap 0

# Enable wake-on-network (Cloudflare Tunnel re-establishes after sleep)
sudo pmset -a womp 1

# Auto-restart after power failure (UPS-protected scenario mostly, but belt-and-suspenders)
sudo pmset -a autorestart 1

# Disable display sleep (no monitor connected; saves nothing, can break things)
sudo pmset -a displaysleep 0
sudo pmset -a halfdim 0

# Disable disk sleep (NVMe spinning down causes latency spikes for Postgres)
sudo pmset -a disksleep 0

# Keep system awake — set sleep to "never"
sudo pmset -a sleep 0

# Set timezone (Postgres needs Asia/Hong_Kong for hourly rates)
sudo systemsetup -settimezone "Asia/Hong_Kong" >/dev/null 2>&1 || true

echo "[energy] Current settings:"
pmset -g

echo
echo "[energy] DONE. Reboot for all changes to take full effect."