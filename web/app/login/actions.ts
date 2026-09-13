'use server';

import { cookies } from 'next/headers';
import { ApiError } from '@/lib/api';
import { AUTH_COOKIE_NAME, API_BASE_URL, API_PREFIX } from '@/lib/config';

export interface LoginActionResult {
  ok: boolean;
  message: string;
}

/**
 * Server action invoked from the login form. Calls the backend directly,
 * extracts the Set-Cookie header from the response, and stores it as an
 * HTTP-only cookie on the Next.js response.
 *
 * The browser then sends that cookie on subsequent same-origin requests
 * automatically. The web client never sees the JWT.
 */
export async function loginAction(
  formData: FormData,
  provider: 'email' | 'apple' | 'google' = 'email',
): Promise<LoginActionResult> {
  const email = String(formData.get('email') ?? '');
  const password = String(formData.get('password') ?? '');
  const idToken = String(formData.get('id_token') ?? '');

  try {
    const url = new URL(`${API_PREFIX}/auth/login`);
    let body: unknown;
    if (provider === 'email') {
      url.pathname = `${API_PREFIX}/auth/login`;
      body = { email, password };
    } else if (provider === 'apple') {
      url.pathname = `${API_PREFIX}/auth/apple`;
      body = { identity_token: idToken || 'dev-apple-token' };
    } else {
      url.pathname = `${API_PREFIX}/auth/google`;
      body = { id_token: idToken || 'dev-google-token' };
    }

    const res = await fetch(url.toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    });

    if (!res.ok) {
      // Try to surface a friendly error code.
      try {
        const errBody = (await res.json()) as { error?: { code?: string; message?: string } };
        return {
          ok: false,
          message: errBody.error?.message || 'Sign in failed',
        };
      } catch {
        return { ok: false, message: `Sign in failed (HTTP ${res.status})` };
      }
    }

    // Forward the Set-Cookie header from the backend so the browser
    // receives the HTTP-only auth cookie on this response.
    const setCookie = res.headers.get('set-cookie');
    if (setCookie) {
      const store = await cookies();
      // Parse the first cookie directive; backend sets evw_auth=...; HttpOnly; Path=/; SameSite=Lax
      const firstPair = setCookie.split(';')[0] ?? '';
      const eq = firstPair.indexOf('=');
      if (eq > 0) {
        const name = firstPair.slice(0, eq).trim();
        const value = firstPair.slice(eq + 1).trim();
        store.set({
          name,
          value,
          httpOnly: true,
          sameSite: 'lax',
          path: '/',
          secure: process.env.NODE_ENV === 'production',
        });
      }
    } else {
      // Backend may use the canonical cookie name only. Fall back: set the
      // access_token from the JSON body as a one-off HTTP-only cookie. The
      // backend MUST eventually set Set-Cookie itself; this is a dev escape.
      const data = (await res.json()) as { access_token?: string };
      if (data.access_token) {
        const store = await cookies();
        store.set({
          name: AUTH_COOKIE_NAME,
          value: data.access_token,
          httpOnly: true,
          sameSite: 'lax',
          path: '/',
          secure: process.env.NODE_ENV === 'production',
        });
      }
    }

    return { ok: true, message: 'ok' };
  } catch (e) {
    if (e instanceof ApiError) {
      return { ok: false, message: e.message };
    }
    return {
      ok: false,
      message: `Cannot reach API at ${API_BASE_URL}`,
    };
  }
}

/**
 * Sign out: clear the local auth cookie. The backend may also invalidate
 * the JWT; that's a separate call if needed (not implemented here).
 */
export async function logoutAction(): Promise<void> {
  const store = await cookies();
  store.delete(AUTH_COOKIE_NAME);
}