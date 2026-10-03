# Cloudflare Worker — public API proxy for the GitHub Pages web portal

The static-exported web portal at `https://rollroyces.github.io/ev-wallet-hk`
needs to call the FastAPI backend. The backend is intentionally locked to
`127.0.0.1` behind a Cloudflare Tunnel — it has no CORS and no public HTTPS
surface of its own.

This Worker sits in front:

```
   Browser  →  Cloudflare edge  →  Worker (CORS + proxy)  →  Existing Tunnel  →  Caddy  →  FastAPI
              evw-api-web.evwallet.com.hk                       (unchanged)
```

The Worker:

1. Returns CORS headers (the browser blocks cross-origin requests without them).
2. Forwards every method (GET / POST / PUT / DELETE / OPTIONS) and every header
   (including `Authorization: Bearer …`) to the backend.
3. Streams the response body back unchanged.

No JWT verification, no rate limiting, no caching here — keep this Worker dumb.
The backend already enforces auth on protected routes; the Worker just relabels
the request so the browser is willing to send it.

## Deploy (free tier)

The Worker falls in Cloudflare's free tier (100,000 requests/day, plenty for
a portal). Steps:

1. `cd cloudflare-worker`
2. Edit `wrangler.toml` — set `BACKEND_ORIGIN` to your tunnel's public hostname
   (e.g. `https://api.evwallet.com.hk`).
3. `npx wrangler deploy` (one-time `npx wrangler login` if you haven't already).

Then in the GitHub repo settings → Secrets and variables → Actions:

- Variable `NEXT_PUBLIC_API_BASE_URL` = `https://evw-api-web.evwallet.com.hk`
  (the Worker URL, no trailing slash).

## Files

- `src/index.js` — Worker source (the only file you need to read).
- `wrangler.toml` — deploy config.
- `package.json` — Wrangler dev dependency only.