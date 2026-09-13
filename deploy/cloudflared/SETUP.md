# =============================================================================
# IMPORTANT: this is documentation for the Cloudflare Tunnel ONE-TIME SETUP.
# Run these commands on your LOCAL Mac (NOT inside the container) to:
#   1. Authenticate cloudflared with your Cloudflare account
#   2. Create the tunnel
#   3. Get the tunnel token
#   4. Route DNS
#
# After this, the container just needs TUNNEL_TOKEN env var.
# =============================================================================

# ---- 1. Install cloudflared locally (one-time) ----------------------------
brew install cloudflared
# OR: brew install --cask cloudflared

# ---- 2. Login (opens browser) ---------------------------------------------
cloudflared tunnel login
# Select the domain you registered (evwallet.com.hk) in the browser.

# ---- 3. Create tunnel -----------------------------------------------------
cloudflared tunnel create ev-wallet-prod
# Output: Created tunnel ev-wallet-prod with id a1b2c3d4-e5f6-...
# Credential file: ~/.cloudflared/<TUNNEL_ID>.json

# ---- 4. Route DNS --------------------------------------------------------
cloudflared tunnel route dns ev-wallet-prod api.evwallet.com.hk
cloudflared tunnel route dns ev-wallet-prod n8n.evwallet.com.hk

# ---- 5. Get the token -----------------------------------------------------
cloudflared tunnel token ev-wallet-prod
# Output: a long string. Copy this to CLOUDFLARED_TUNNEL_TOKEN in .env.

# ---- 6. (Optional) Validate routing config -------------------------------
cloudflared tunnel ingress validate ./deploy/cloudflared/config.yml

# ---- 7. Verify tunnel works ----------------------------------------------
# After docker compose up, from your laptop:
cloudflared tunnel info ev-wallet-prod
# Should show: "0 connections, 1 active route, 0 active connectors"
# When compose is up, it becomes "1 connector"

# ---- 8. Test --------------------------------------------------------------
curl -I https://api.evwallet.com.hk/healthz
# Expect: HTTP/2 200 (Caddy serves from FastAPI which returns ok)

# ---- Rotation (every 90 days or on incident) -------------------------------
# 1. cloudflared tunnel token ev-wallet-prod > new_token.txt
# 2. Update CLOUDFLARED_TUNNEL_TOKEN in .env
# 3. docker compose restart cloudflared