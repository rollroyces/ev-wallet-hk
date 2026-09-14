# Local web testing — how to run

Both servers run on the same Mac (or whatever host has the repo checked
out). The backend ships with **real Argon2id password hashing** as of
v0.4.2 — `POST /api/v1/auth/register` creates a user and returns a
session, `POST /api/v1/auth/login` verifies the password. The dev-mode
`EVW_DEV_LOGIN=true` shortcut still exists (for getting something on
screen fast) but it's no longer required.

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

1. <http://localhost:3000/> → redirects to `/signup` (the new-visitor flow)
2. Fill in email + password (8+ chars) + confirm password → auto-logged-in,
   redirected to `/dashboard` showing your wallet (initially 0 HKD) with
   a "+ Top up wallet" button.
3. Already have an account? Click "Sign in" from the signup page (or
   navigate to `/login`).

The first signup creates the user + wallet in one transaction.

## What works today

- **Signup** via `/signup` (email + password) — Argon2id hashing,
  409 on duplicate email, auto-login on success.
- **Login** via `/login` (email + password) — verifies the stored
  hash; same generic error message for wrong-password vs. unknown-email
  to avoid leaking which is which.
- **Sign out** via the Nav bar.
- Dashboard + station list + station detail + sessions + admin pages.
- Navigate buttons (Google Maps deep-link).
- Top up dialog: shows "Stripe isn't configured on the server" warning
  unless `EVW_STRIPE_PUBLISHABLE_KEY` is set.
- EPD station ingestion: 917 stations visible at
  `/api/v1/internal/providers/epd/stations`.
- HKEV / Shell / Tesla: 503 with `contact_email` field (graceful skip).

## What needs additional setup

- **Real Stripe topup roundtrip**: set `EVW_STRIPE_PUBLISHABLE_KEY` +
  `EVW_STRIPE_SECRET_KEY` + run `stripe listen --forward-to
  localhost:8001/api/v1/payments/stripe/webhook`. Test card
  `4242 4242 4242 4242`.
- **Apple Pay / Google Pay**: requires EAS Build (native modules don't
  compile in Expo Go). See `docs/operations/PAYMENTS.md`.
- **Push notifications (FCM/APNs)**: requires Firebase project + APNs
  key. Not on the critical path for local testing.

## Production safety

- Passwords use Argon2id (OWASP 2024 recommended). Never log or print
  the hash; it's enough to compromise a user's password if the salt is
  weak (it isn't — Argon2id uses random salts).
- `EVW_DEV_LOGIN=true` SHOULD NOT be set in production. With it off,
  login verifies the hash; with it on, login auto-creates users with
  any password (a developer convenience that should never reach
  prod). The flag defaults to `false` everywhere except local dev.

## Password flow details

- Signup: `POST /api/v1/auth/register { email, password }` →
  - Hashes the password with Argon2id (default OWASP params: m=64MiB, t=3, p=4)
  - Creates the `User` row with the hash
  - Creates the `Wallet` row in the same transaction
  - Returns a session (access token) — same shape as `/auth/login`
  - Status: 201 Created
  - 409 CONFLICT if the email is already taken
- Login: `POST /api/v1/auth/login { email, password }` →
  - Looks up the user by email
  - Verifies the password with `argon2-cffi.PasswordHasher.verify`
  - Returns the session
  - 422 AUTH_INVALID_CREDENTIALS for either wrong password or unknown email
    (same message — prevents user-enumeration attacks)