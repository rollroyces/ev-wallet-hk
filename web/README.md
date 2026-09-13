# EV Wallet HK — Web Portal (Agent E)

Next.js 15 admin + desktop portal for EV Wallet HK.

## Scope

- Login (email/password, Apple, Google)
- Dashboard (wallet summary, recent activity)
- Stations list + detail (with 24-hour rate chart)
- Sessions list + detail
- Admin: stations overview
- Admin: wallet ledger (all users)

The portal talks directly to the FastAPI backend at `NEXT_PUBLIC_API_BASE_URL`
(default `http://localhost:8000`). Auth is via an HTTP-only cookie set by
`POST /api/v1/auth/login` — the web client never sees the JWT.

## Tech stack

- Next.js 15 (app router, server components for data fetching, client components for interactivity)
- TypeScript strict (`noUncheckedIndexedAccess`, `noImplicitOverride`)
- Tailwind CSS for utilities + a small hand-rolled `globals.css` for components
- `@tanstack/react-query` for client-side caching
- `zod` available for input validation (server actions accept FormData today; migrate to zod-parsed payloads as the schema grows)
- `jose` for JWT verification in middleware

## Layout

```
web/
├── app/
│   ├── layout.tsx              # root layout + providers
│   ├── page.tsx                # / → /login or /dashboard
│   ├── login/                  # email + Apple + Google
│   ├── dashboard/              # wallet summary
│   ├── stations/               # list + [id] detail w/ rates chart
│   ├── sessions/               # list + [id] detail
│   └── admin/                  # gated by middleware.ts (is_admin)
│       ├── stations/           # GET /internal/stations/list (admin)
│       └── ledger/             # GET /wallet/admin/all-transactions (admin)
├── components/nav.tsx
├── lib/
│   ├── api.ts                  # ApiClient implementation (server-side w/ cookie forwarding)
│   ├── auth.ts                 # getCurrentUser() / isAdmin() (server)
│   ├── config.ts               # API_BASE_URL, APP_NAME, AUTH_COOKIE_NAME
│   └── types.ts                # canonical TS types — MUST be byte-equal to mobile/lib/types.ts
├── middleware.ts               # gates /admin/* + /dashboard, /stations, /sessions
├── next.config.js
├── tailwind.config.ts
├── postcss.config.js
├── tsconfig.json
└── package.json
```

## Develop

```bash
cd web
cp .env.example .env.local         # edit NEXT_PUBLIC_API_BASE_URL if needed
npm install --legacy-peer-deps
npm run dev                        # http://localhost:3000
```

## Verify

```bash
npx tsc --noEmit                   # must be clean
npm run build                      # must succeed
```

## Auth flow

1. User submits the login form.
2. `loginAction` (server action) POSTs to `API_BASE_URL/api/v1/auth/login`.
3. The backend returns `{ access_token, expires_at, user }` AND sets an
   HTTP-only `evw_auth` cookie.
4. The server action parses `Set-Cookie` from the response and writes the
   cookie to the Next.js response (so the browser receives it).
5. Subsequent navigations send `Cookie: evw_auth=...` automatically.
6. `middleware.ts` decodes the JWT to gate `/admin/*` (requires `is_admin: true`).
7. Server components read the cookie via `next/headers` and forward it to
   the backend on every API call (via `getCookieHeader()` + `getApiClient(cookie)`).

## API contract

The full contract is in `docs/ARCHITECTURE.md`. The web portal implements
the `ApiClient` interface from "Mobile + web API client contract" exactly.

Routes used:
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/apple`
- `POST /api/v1/auth/google`
- `GET  /api/v1/auth/me`
- `GET  /api/v1/wallet`
- `GET  /api/v1/wallet/transactions`
- `GET  /api/v1/stations`
- `GET  /api/v1/stations/{id}`
- `GET  /api/v1/stations/{id}/rates`
- `GET  /api/v1/charging/sessions/{id}`
- `POST /api/v1/charging/sessions`  (used by mobile)
- `POST /api/v1/charging/sessions/{id}/end`

Internal/admin routes the portal expects agent A to expose:
- `GET /api/v1/internal/stations/list`     (requires admin)
- `GET /api/v1/wallet/admin/all-transactions` (requires admin)

## TODOs

- **Live telemetry on session detail.** Browser → WS cannot forward an HTTP-only
  cookie; we render a snapshot today. Production fix is either a same-origin WS
  proxy rewrite or a one-shot signed token issued from a server action.
- **Sessions list endpoint.** Contract has `GET /api/v1/charging/sessions/{id}` but
  not a list. We currently derive sessions from wallet charge-transaction
  metadata; once agent C adds the list endpoint, switch the page to consume it.
- **Top-up form.** Backend supports `POST /api/v1/wallet/topup` with Stripe /
  Apple Pay / Google Pay `source_payload`. Build the form when payment
  integrations are ready.
- **Apple/Google buttons.** Currently stub POSTs a placeholder ID token in
  dev. Wire up real OAuth (Apple Sign-In JS, Google Identity Services) in
  production.