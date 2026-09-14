# Local web testing — how to run

Both servers run on the same Mac (or whatever host has the repo checked
out). The backend has a DEV-ONLY login shortcut enabled by
`EVW_DEV_LOGIN=true`. **Never set that in production.**

## Quick start

```bash
# 1. Containers (already running if you ran v0.4.0 setup)
docker ps | grep -E 'evw-test-(pg|redis)'

# 2. Start the FastAPI backend (port 8001, DEV_LOGIN enabled)
cd /Users/hermes/projects/ev-wallet-hk
export EVW_DATABASE_URL="postgresql+asyncpg://evwallet:evwallet@localhost:5433/evwallet_test"
export EVW_REDIS_HOST=localhost EVW_REDIS_PORT=6380 EVW_REDIS_PASSWORD=evwallet
export EVW_JWT_SECRET="q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0OpQ9pXrLkMz7NcV"
export EVW_APPLE_BUNDLE_ID="com.evwallet.hk"
export EVW_GOOGLE_CLIENT_ID="test.apps.googleusercontent.com"
export EVW_INTERNAL_TOKEN="test-internal-token-1234567890abcdefghij"
export EVW_ENV=development
export EVW_DEV_LOGIN=true
.venv/bin/python -m uvicorn evwallet.main:app --host 127.0.0.1 --port 8001

# 3. In another terminal: start the Next.js dev server
cd web
export NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001
export EVW_JWT_SECRET="q9pXrLkMz7NcVt5WgBjHsAuYf3dE6i2oQ4r8y1uI0OpQ9pXrLkMz7NcV"
npx next dev --hostname 127.0.0.1 --port 3000
```

## Open in your browser

1. <http://localhost:3000/> → redirects to `/login`
2. Sign in with **any email** (e.g. `[email protected]`) + **any password ≥ 8 chars**
3. You'll be redirected to `/dashboard` showing the wallet (initially 0 HKD), with a "+ Top up wallet" button.

The dev login auto-creates the user + wallet on first sign-in. There's no password stored anywhere; it's a test-only shortcut.

## What works today

- Login + dashboard + station list + station detail + sessions + admin pages
- Navigate buttons (Google Maps deep-link)
- Top up dialog: shows "Stripe isn't configured on the server" warning (no `pk_test_…` set)
- EPD station ingestion: 917 stations visible at `/api/v1/internal/providers/epd/stations`
- HKEV / Shell / Tesla: 503 with `contact_email` field (graceful skip)

## What needs additional setup

- **Real Stripe topup roundtrip**: set `EVW_STRIPE_PUBLISHABLE_KEY` + `EVW_STRIPE_SECRET_KEY` + run `stripe listen --forward-to localhost:8001/api/v1/payments/stripe/webhook`. Test card `4242 4242 4242 4242`.
- **Apple Pay / Google Pay**: requires EAS Build (native modules don't compile in Expo Go). See `docs/operations/PAYMENTS.md`.
- **Push notifications (FCM/APNs)**: requires Firebase project + APNs key. Not on the critical path for local testing.

## Production safety

`EVW_DEV_LOGIN=true` MUST be unset (or set to `false`) in production.
The endpoint also raises a clear "not yet implemented" error when the
flag is off, so an accidental deployment without the flag set behaves
correctly (returns 422 instead of auto-creating users).