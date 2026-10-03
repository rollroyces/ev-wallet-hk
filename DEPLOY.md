# Deploying the web portal to GitHub Pages

The `web/` app has been refactored to a static-exported Next.js 15 bundle
that GitHub Pages can host. This document walks through everything that has
to happen outside the repo to make it live.

There are two deployable pieces:

1. The static site — uploaded automatically by `.github/workflows/pages.yml`
   on every push to `main`. Lives at
   `https://rollroyces.github.io/ev-wallet-hk/`.
2. A Cloudflare Worker — sits in front of the existing Cloudflare Tunnel
   → Caddy → FastAPI stack and adds CORS so the browser is willing to
   call it. Free tier. One-time deploy.

## 1. Push the branch

```bash
git push -u origin feat/gh-pages-static-export
```

Open the PR, review, merge into `main`. The Pages workflow runs on
every push to `main` that touches `web/**` or
`.github/workflows/pages.yml`.

## 2. Activate GitHub Pages

In the GitHub repo:

- **Settings → Pages**
- **Build and deployment → Source: GitHub Actions**
- Save.

The first run of `.github/workflows/pages.yml` builds and publishes.
The deployment job uses the official `actions/deploy-pages` action, so
the `github-pages` environment is created automatically on first run.

## 3. Set the Worker's URL as a repo variable

In the GitHub repo:

- **Settings → Secrets and variables → Actions → Variables**
- New variable: `NEXT_PUBLIC_API_BASE_URL` =
  `https://evw-api-web.evwallet.com.hk` (no trailing slash)

This is the URL the Worker will be reachable at (step 4). The build
embeds it into the static bundle so the browser knows where to send API
calls.

## 4. Deploy the Cloudflare Worker

You need a Cloudflare account. The Worker falls in the free tier
(100,000 requests/day, plenty for a public portal).

```bash
cd cloudflare-worker
npm install            # installs wrangler only
npx wrangler login     # one-time: opens browser, OAuth
npx wrangler secret put BACKEND_ORIGIN
# paste: https://api.evwallet.com.hk   (your existing Tunnel → Caddy hostname)
npx wrangler deploy
```

Wrangler prints the deployed URL. It should match the variable you set
in step 3. If you'd rather use a custom hostname (e.g.
`evw-api-web.evwallet.com.hk`), add a route in the Cloudflare dashboard
or in `wrangler.toml`.

## 5. Verify

Open `https://rollroyces.github.io/ev-wallet-hk/login/` — you should see
the sign-in page. Open DevTools → Network and try logging in:

- Request URL should be `https://evw-api-web.evwallet.com.hk/api/v1/auth/login`
- Response should be 200 with `{ access_token, ... }`
- After login, `localStorage.evw_auth_token` should contain the JWT
- Subsequent navigation to `/dashboard`, `/stations`, `/sessions` should
  hit the Worker with `Authorization: Bearer …` headers

If login fails with a network error, the browser is rejecting the
Worker's CORS headers. Check:

- Worker is deployed and reachable
- `BACKEND_ORIGIN` secret is set on the Worker
- Browser DevTools → Network → the failing request → Response Headers
  should include `access-control-allow-origin: https://rollroyces.github.io`

## Cost

- GitHub Pages: $0
- Cloudflare Worker: $0 (free tier; 100k requests/day)
- Backend: unchanged — still on the Mac mini behind your existing Tunnel

## Security caveats

- The JWT lives in `localStorage`. Any XSS on the public origin can read
  it. The threat model assumes no third-party scripts on this site; if
  that changes, swap `localStorage` for an in-memory token + silent
  refresh from a short-lived cookie set by the Worker.
- `/admin` is **not** in the public build. The repo no longer contains
  those source files. If you ever want an admin UI on Pages, route-gate
  it with `is_admin` claim on the JWT AND have the backend re-check.
- The Worker allows CORS only from `https://rollroyces.github.io` (and
  any custom domain you add to `ALLOWED_ORIGINS` in
  `cloudflare-worker/src/index.js`).

## Local development

The static build needs no real backend to render the shell. To iterate:

```bash
cd web
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
# open http://localhost:3000 — login form will fail unless FastAPI is up
```

To preview the static export itself:

```bash
cd web
npm run build
npx serve out -l 8080
# open http://localhost:8080/ev-wallet-hk/login/
```
(Or any static server that respects trailing slashes.)

## Updating the deployed site

Push to `main`. The workflow runs in ~2 minutes. Done.

## Rolling back

GitHub Pages keeps previous deployments under
**Settings → Pages → "View deployments"**. Click a previous successful
deploy → "Redeploy". Or revert the merge commit and push.