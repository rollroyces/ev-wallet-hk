# Local web testing — how to run

Both servers run on the same Mac (or whatever host has the repo checked
out). The backend ships with **real Argon2id password hashing** (v0.4.2)
and **email verification** (v0.5.0) — `POST /api/v1/auth/register` creates
a user, sends a 6-digit verification code, and `POST /api/v1/auth/verify-email`
redeems it. Topup is gated on `email_verified_at IS NOT NULL`. The
dev-mode `EVW_DEV_LOGIN=true` shortcut still exists (for getting
something on screen fast without configuring SMTP) but it's no longer
required.

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
4. After signup, check the backend's stderr log for your 6-digit
   verification code (or check your inbox if SMTP is configured).
   POST it to `/api/v1/auth/verify-email` to unlock topup.
5. Once verified, the topup dialog works. With no Stripe credentials
   configured, the request will be accepted but no real money moves
   (the `payment_intent_id` is trusted client-side at this stage; see
   the "Production safety" section for what's missing).

The first signup creates the user + wallet + verification code row in
one transaction.

## What works today

- **Signup** via `/signup` (email + password) — Argon2id hashing,
  409 on duplicate email, auto-login on success. Sends a 6-digit
  verification code via the configured sender (or logs to stderr in
  console-sender mode).
- **Email verification** via `/auth/verify-email` — 6-digit code,
  Argon2id-hashed in the DB, 15-min TTL, row lockout after 5 wrong
  attempts. Rate-limited per IP (10/15min) and per row (5 attempts).
- **Topup gate** — `POST /api/v1/wallet/topup` returns 401
  `EMAIL_NOT_VERIFIED` until the user is verified. Prevents the basic
  "sign up + top up + drain" pattern.
- **Resend** via `/auth/resend-verification` — idempotent (returns
  `sent=false` if already verified), 3/hour per IP.
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
- **Rate limiting** on `/auth/login` (5/15min/IP), `/auth/register`
  (3/hour/IP), `/auth/verify-email` (10/15min/IP),
  `/auth/resend-verification` (3/hour/IP). All return HTTP 429 with
  `Retry-After` header. Redis-backed (ZSET sliding window) with an
  in-process fallback when Redis is down.

## What needs additional setup

- **Real SMTP for verification codes** — set `EVW_SMTP_HOST`,
  `EVW_SMTP_PORT`, `EVW_SMTP_USERNAME`, `EVW_SMTP_PASSWORD`,
  `EVW_SMTP_FROM`, `EVW_SMTP_TLS` (`starttls` | `ssl` | `none`).
  Without these, codes are logged to stderr.
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
- Email verification codes are stored as **Argon2id hashes** (not
  plaintext), so a DB leak doesn't immediately give an attacker the
  codes. The plaintext code is sent exactly once, via the SMTP
  channel, and is not retained.
- Rate limiting uses Redis ZSETs with atomic Lua scripts. If Redis is
  unavailable, the limiter falls back to an in-process map (with a
  warning log); this is per-worker and resets on restart, so
  horizontal scaling splits the limit across workers.

## Password flow details

- Signup: `POST /api/v1/auth/register { email, password }` →
  - Hashes the password with Argon2id (default OWASP params: m=64MiB, t=3, p=4)
  - Creates the `User` row with the hash
  - Creates the `Wallet` row in the same transaction
  - Issues an `EmailVerification` row (Argon2id hash of a 6-digit
    numeric code, 15-min TTL)
  - Sends the code via the configured SMTP sender (or console in dev)
  - Returns a session (access token) — same shape as `/auth/login`
  - Status: 201 Created
  - 409 CONFLICT if the email is already taken
- Login: `POST /api/v1/auth/login { email, password }` →
  - Looks up the user by email
  - Verifies the password with `argon2-cffi.PasswordHasher.verify`
  - Returns the session
  - 422 AUTH_INVALID_CREDENTIALS for either wrong password or unknown email
    (same message — prevents user-enumeration attacks)
- Verify email: `POST /api/v1/auth/verify-email { code }` →
  - Looks up the latest unconsumed `EmailVerification` row for the user
  - Bumps `attempts` on each failure; locks the row after 5
  - On success, sets `users.email_verified_at` and consumes the row
  - Status: 200 OK with `{ verified, email_verified_at, dev_code }`
    (`dev_code` is only populated when running against the
    console-sender; in production with real SMTP, it's always null)
  - 401 AUTH_ERROR if the code is invalid, expired, or the row is locked
- Resend: `POST /api/v1/auth/resend-verification` →
  - If already verified, returns `{ sent: false, expires_at: <verified_at> }`
  - Otherwise issues a fresh code (3/hour per IP)
  - Status: 200 OK