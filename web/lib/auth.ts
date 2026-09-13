// HTTP-only cookie session management for the web portal.
//
// Strategy:
//   - On successful login the backend's /api/v1/auth/login sets an HTTP-only
//     cookie containing the JWT. The browser sends it on every same-origin
//     request automatically; we never read or write the token from JS.
//   - middleware.ts inspects the cookie to gate /admin/*.
//   - Server components read the cookie via next/headers to build API calls.
//   - On the client we never touch the token; logout is a server action that
//     asks the backend to clear the cookie.

import { cookies } from 'next/headers';
import { jwtVerify, type JWTPayload } from 'jose';
import { AUTH_COOKIE_NAME } from './config';

export interface SessionClaims extends JWTPayload {
  sub: string;
  exp: number;
  iat: number;
}

export interface CurrentUser {
  id: string;
  is_admin: boolean;
}

function secret(): string {
  // In production this MUST be set. We default to a non-secret fallback so
  // dev builds don't crash; the middleware will refuse to admit anyone if the
  // secret isn't set.
  return process.env.EVW_JWT_SECRET || 'dev-only-secret-do-not-use-in-prod-32+';
}

/**
 * Server-side helper: verify the JWT cookie and return the current user.
 * Returns null when the cookie is missing, invalid, or expired.
 */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const store = await cookies();
  const token = store.get(AUTH_COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, new TextEncoder().encode(secret()), {
      algorithms: ['HS256'],
    });
    const claims = payload as SessionClaims;
    return {
      id: claims.sub,
      is_admin: Boolean((payload as Record<string, unknown>).is_admin),
    };
  } catch {
    return null;
  }
}

export function isAdmin(user: CurrentUser | null): boolean {
  return Boolean(user?.is_admin);
}