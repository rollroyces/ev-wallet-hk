// Client-side auth state for the static-exported web portal.
//
// The previous architecture stored the JWT in an HTTP-only cookie set by
// a server action. That requires a Node runtime, which GitHub Pages
// doesn't provide. On Pages we have to keep the JWT in localStorage
// instead, and ship the decoded claims (is_admin, sub) to client guards.
//
// Security tradeoff:
//   - HTTP-only cookies can't be exfiltrated by XSS reading document.cookie.
//     localStorage CAN be read by any script running on the page.
//   - We accept this for the public Pages deployment because the admin
//     routes are excluded from the build entirely (see GitHub Actions
//     workflow). The remaining surface (wallet, stations, sessions) does
//     not depend on admin claims.
//
// All functions are safe to call from any client component.

import { decodeJwt, type JWTPayload } from 'jose';
import { AUTH_TOKEN_KEY } from './config';

export interface SessionClaims extends JWTPayload {
  sub: string;
  exp: number;
  iat: number;
  is_admin?: boolean | string | number;
}

export interface CurrentUser {
  id: string;
  is_admin: boolean;
  expires_at: number;
}

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

export function getToken(): string | null {
  if (!isBrowser()) return null;
  try {
    return window.localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    // Safari private mode, storage disabled, etc.
    return null;
  }
}

export function setToken(token: string): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  } catch {
    /* noop */
  }
}

export function clearToken(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    /* noop */
  }
}

function isAdminClaim(payload: SessionClaims): boolean {
  const v = payload.is_admin;
  return v === true || v === 'true' || v === 1 || v === '1';
}

/**
 * Decode the JWT in localStorage and return the current user claims.
 * Returns null when the token is missing, malformed, or expired.
 *
 * Does NOT verify the signature. The backend re-verifies on every request;
 * this is only used for client-side gating (hide / show UI, redirect).
 */
export function getCurrentUser(): CurrentUser | null {
  const token = getToken();
  if (!token) return null;
  try {
    const claims = decodeJwt<SessionClaims>(token);
    // jwt-decode does not check exp; do it ourselves so the UI can hide
    // stale sessions even before the next API call fails.
    if (claims.exp && claims.exp * 1000 <= Date.now()) {
      clearToken();
      return null;
    }
    return {
      id: claims.sub,
      is_admin: isAdminClaim(claims),
      expires_at: claims.exp,
    };
  } catch {
    clearToken();
    return null;
  }
}

export function isAdmin(user: CurrentUser | null): boolean {
  return Boolean(user?.is_admin);
}