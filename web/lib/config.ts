// Backend API base URL. The web portal talks directly to the FastAPI service.
// NEXT_PUBLIC_* is exposed to the browser, which is fine because the API is
// unauthenticated endpoints are public — authenticated routes rely on the
// HTTP-only auth cookie set by /api/v1/auth/login.
//
// In production the backend is reverse-proxied through Caddy; the web origin
// reads NEXT_PUBLIC_API_BASE_URL at build time.

export const API_BASE_URL: string =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  'http://localhost:8000';

export const APP_NAME: string =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_APP_NAME) ||
  'EV Wallet HK';

export const API_PREFIX = '/api/v1';

// Name of the HTTP-only cookie the backend sets on successful login.
// Mobile and web MUST agree on this name.
export const AUTH_COOKIE_NAME = 'evw_auth';