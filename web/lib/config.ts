// Runtime config for the static-exported web portal.
//
// NEXT_PUBLIC_* values are inlined at build time. Since we deploy via
// `next build && next export`, you MUST rebuild after changing these.
//
// In production the public API is proxied through a Cloudflare Worker
// (see cloudflare-worker/) that adds CORS and forwards to the FastAPI
// backend behind the existing Cloudflare Tunnel. The browser talks to
// the Worker URL only — it never sees the backend directly.

export const API_BASE_URL: string =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  'http://localhost:8000';

export const APP_NAME: string =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_APP_NAME) ||
  'EV Wallet HK';

export const API_PREFIX = '/api/v1';

// Storage key for the JWT in localStorage. Single source of truth.
export const AUTH_TOKEN_KEY = 'evw_auth_token';