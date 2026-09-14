#!/usr/bin/env bash
# =============================================================================
# EV Wallet HK — operator bootstrap readiness check
#
# Run this on a fresh Mac mini (or any host about to be deployed to) AFTER
# you have:
#   1. Installed Docker Desktop, rclone, apcupsd (see docs/PROVISIONING.md)
#   2. Copied the repo to ~/projects/ev-wallet-hk
#   3. Filled in .env from .env.example with your real secrets
#   4. Configured Cloudflare Tunnel token + DNS records
#
# This script is a READ-ONLY check — it does not modify anything. It exits
# non-zero on any blocking failure so you can run it from CI or a launchd
# pre-flight hook. The companion apply step is in docs/PROVISIONING.md
# Section I ("Smoke test the stack").
#
# Usage: ./scripts/provision_check.sh
# Exit:  0 on ready, 1 on any blocking issue
# =============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
WARN=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo -e "  ${GREEN}\xe2\x9c\x93${NC} $1"; }
warn() { WARN=$((WARN + 1)); echo -e "  ${YELLOW}!${NC} $1"; }
fail() { FAIL=$((FAIL + 1)); echo -e "  ${RED}\xe2\x9c\x97${NC} $1"; }
section() { echo; echo -e "${BLUE}== $1 ==${NC}"; }

check_command() {
    local cmd=$1
    local label=${2:-$cmd}
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "$label is installed ($(command -v $cmd))"
    else
        fail "$label not found (run: brew install $cmd)"
    fi
}

# Docker Compose v2 ships as a `docker compose` subcommand, not a separate
# binary; check both forms.
docker_compose_installed() {
    docker compose version >/dev/null 2>&1 || docker-compose --version >/dev/null 2>&1
}

check_env_var() {
    local var=$1
    local critical=${2:-true}
    if grep -qE "^${var}=[^[:space:]]" .env 2>/dev/null; then
        local value
        value=$(grep -E "^${var}=" .env | head -1 | cut -d= -f2-)
        if [[ "$value" == *change_me* || "$value" == *placeholder* || ${#value} -lt 8 ]]; then
            if [[ "$critical" == "true" ]]; then
                fail "$var is set but looks like a placeholder (got: $value)"
            else
                warn "$var is set but looks like a placeholder (got: $value)"
            fi
        else
            pass "$var is set (length: ${#value})"
        fi
    else
        if [[ "$critical" == "true" ]]; then
            fail "$var is missing from .env"
        else
            warn "$var is missing from .env (optional, but recommended)"
        fi
    fi
}

# ---------------------------------------------------------------- A. Tools
section "A. Required tools"
check_command docker
if docker_compose_installed; then
    pass "docker compose is available"
else
    fail "docker compose not found (Docker Desktop includes it; install from docker.com)"
fi
check_command git
check_command curl
check_command rclone
check_command apcaccess
check_command cloudflared

# ---------------------------------------------------------------- B. .env presence + secrets
section "B. .env secrets"

if [[ ! -f .env ]]; then
    fail ".env not found — copy from .env.example and fill in"
    echo
    echo "  Quick start:"
    echo "    cp .env.example .env"
    echo "    python3 -c \"import secrets; print(secrets.token_urlsafe(64))\"  # JWT"
    echo "    python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"  # Postgres"
    echo "    python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"  # Redis"
    echo "    python3 -c \"import secrets; print(secrets.token_hex(16))\"        # n8n"
    echo "    echo EVW_INTERNAL_TOKEN=\"\$(python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")\""
    echo
    echo "  See docs/PROVISIONING.md for the full signup list."
    exit 1
fi

pass ".env exists"

# Critical (must be set + non-placeholder)
check_env_var EVW_JWT_SECRET
check_env_var POSTGRES_PASSWORD
check_env_var REDIS_PASSWORD
check_env_var N8N_PASSWORD
check_env_var EVW_APPLE_BUNDLE_ID
check_env_var EVW_GOOGLE_CLIENT_ID
check_env_var CLOUDFLARED_TUNNEL_TOKEN
check_env_var B2_ACCOUNT_ID
check_env_var B2_APPLICATION_KEY

# Optional but recommended
check_env_var EVW_STRIPE_SECRET_KEY false
check_env_var EVW_STRIPE_WEBHOOK_SECRET false
check_env_var EVW_APPLE_PAY_MERCHANT_CERT_PATH false
check_env_var EVW_GOOGLE_SA_KEY_PATH false
check_env_var EVW_INTERNAL_TOKEN false

# ---------------------------------------------------------------- C. JWT secret strength
section "C. Secret strength"
JWT=$(grep -E "^EVW_JWT_SECRET=" .env | head -1 | cut -d= -f2-)
if [[ ${#JWT} -lt 32 ]]; then
    fail "EVW_JWT_SECRET is only ${#JWT} chars; need >=32"
elif [[ "$JWT" == *change_me* ]]; then
    fail "EVW_JWT_SECRET still has the default placeholder"
else
    pass "EVW_JWT_SECRET length is ${#JWT} (>=32)"
fi

# ---------------------------------------------------------------- D. Docker
section "D. Docker readiness"
if ! command -v docker >/dev/null 2>&1; then
    fail "docker not installed"
else
    if docker info >/dev/null 2>&1; then
        pass "docker daemon reachable"
    else
        fail "docker installed but daemon not responding — open Docker Desktop"
    fi
fi

# ---------------------------------------------------------------- E. Cloudflare tunnel token
section "E. Cloudflare tunnel"
if grep -qE "^CLOUDFLARED_TUNNEL_TOKEN=[^[:space:]]" .env 2>/dev/null; then
    TOKEN=$(grep -E "^CLOUDFLARED_TUNNEL_TOKEN=" .env | head -1 | cut -d= -f2-)
    if [[ ${#TOKEN} -ge 50 ]]; then
        pass "CLOUDFLARED_TUNNEL_TOKEN present (length ${#TOKEN})"
    else
        warn "CLOUDFLARED_TUNNEL_TOKEN seems short (${#TOKEN} chars); expected >=50"
    fi
else
    fail "CLOUDFLARED_TUNNEL_TOKEN missing — see docs/PROVISIONING.md Step B"
fi

# ---------------------------------------------------------------- F. Backblaze B2
section "F. Backblaze B2"
if grep -qE "^B2_ACCOUNT_ID=[^[:space:]]" .env 2>/dev/null && \
   grep -qE "^B2_APPLICATION_KEY=[^[:space:]]" .env 2>/dev/null; then
    pass "B2_ACCOUNT_ID and B2_APPLICATION_KEY both present"
    if command -v rclone >/dev/null 2>&1; then
        if rclone listremotes --config <(echo) 2>/dev/null | grep -q b2:; then
            pass "rclone has b2: remote configured"
        else
            warn "rclone b2: remote not configured — backup.sh will warn but continue"
        fi
    fi
else
    fail "B2_ACCOUNT_ID or B2_APPLICATION_KEY missing — see docs/PROVISIONING.md Step C"
fi

# ---------------------------------------------------------------- G. UPS
section "G. UPS (apcupsd)"
if command -v apcaccess >/dev/null 2>&1; then
    if apcaccess status 2>/dev/null | grep -q "STATUS.*:.*ONLINE"; then
        pass "UPS online (apcaccess reports STATUS: ONLINE)"
    elif apcaccess status 2>/dev/null | grep -q "STATUS"; then
        warn "apcupsd installed but not ONLINE — check USB cable"
    else
        warn "apcaccess not responding — apcupsd may not be running"
    fi
else
    fail "apcupsd not installed — see docs/PROVISIONING.md Step D"
fi

# ---------------------------------------------------------------- H. macOS power settings
section "H. macOS power settings"
if [[ "$(uname)" == "Darwin" ]]; then
    if pmset -g | grep -q "autorestart.*1"; then
        pass "autorestart on power loss is enabled"
    else
        warn "autorestart NOT enabled — run deploy/macos/energy.sh"
    fi
    if pmset -g | grep -q "powernap.*0"; then
        pass "powernap disabled (server-class config)"
    else
        warn "powernap still on — run deploy/macos/energy.sh"
    fi
else
    warn "not macOS — skipping pmset checks"
fi

# ---------------------------------------------------------------- I. launchd plists
section "I. launchd plists"
PLISTS=(
    "$HOME/Library/LaunchAgents/com.docker.desktop-auto-restart.plist"
    "$HOME/Library/LaunchAgents/com.apc.shutdown.plist"
    "$HOME/Library/LaunchAgents/com.ev.backup.plist"
)
for p in "${PLISTS[@]}"; do
    if [[ -f "$p" ]]; then
        if launchctl list 2>/dev/null | grep -q "$(basename $p .plist)"; then
            pass "$(basename $p) is loaded"
        else
            warn "$(basename $p) installed but not loaded (launchctl load $p)"
        fi
    else
        warn "$(basename $p) not installed yet"
    fi
done

# ---------------------------------------------------------------- Summary
echo
echo -e "${BLUE}== Summary ==${NC}"
echo -e "  ${GREEN}passed: $PASS${NC}"
echo -e "  ${YELLOW}warned: $WARN${NC}"
echo -e "  ${RED}failed: $FAIL${NC}"

if [[ $FAIL -gt 0 ]]; then
    echo
    echo -e "${RED}Not ready to deploy.${NC} Resolve the failures above first."
    echo
    echo "Next step: docs/PROVISIONING.md (10 sections, A-J)"
    exit 1
fi

if [[ $WARN -gt 0 ]]; then
    echo
    echo -e "${YELLOW}Ready with warnings.${NC} Optional items can be configured later."
fi

echo
echo -e "${GREEN}Ready to deploy.${NC}"
echo
echo "Next steps:"
echo "  1. docker compose up -d"
echo "  2. tail -f logs/*.log"
echo "  3. curl -s https://api.evwallet.com.hk/healthz   # expect 200 ok"
echo "  4. curl -s https://api.evwallet.com.hk/readyz    # expect 200 ready (DB+Redis OK)"
echo "  5. ./scripts/backup.sh                             # verify backup writes to B2"
echo
exit 0