'use client';

import { useEffect, useState, useTransition } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { setToken, getCurrentUser } from '@/lib/auth';

export function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const next = params.get('next') || '/dashboard';
  const [error, setError] = useState<string | null>(
    params.get('error') === 'forbidden' ? 'You do not have access to that area.' : null,
  );
  const [isPending, startTransition] = useTransition();

  // If we're already logged in, bounce straight to dashboard.
  useEffect(() => {
    const u = getCurrentUser();
    if (u) router.replace(next);
  }, [router, next]);

  async function handleSubmit(formData: FormData) {
    setError(null);
    const email = String(formData.get('email') ?? '');
    const password = String(formData.get('password') ?? '');
    startTransition(async () => {
      try {
        const session = await getApiClient().login(email, password);
        setToken(session.access_token);
        router.push(next);
        router.refresh();
      } catch (e) {
        if (e instanceof ApiError) {
          if (e.status === 0) setError(e.message);
          else if (e.status === 401 || e.status === 403)
            setError('Invalid email or password.');
          else setError(e.message ?? `Sign in failed (HTTP ${e.status}).`);
        } else {
          setError('Sign in failed.');
        }
      }
    });
  }

  async function providerLogin(p: 'apple' | 'google') {
    setError(null);
    // Dev placeholder: the backend accepts dev-apple-token / dev-google-token
    // in non-prod. Production must wire up real OAuth flows.
    const token = p === 'apple' ? 'dev-apple-token' : 'dev-google-token';
    startTransition(async () => {
      try {
        const session = await getApiClient()[p === 'apple' ? 'loginApple' : 'loginGoogle'](token);
        setToken(session.access_token);
        router.push(next);
        router.refresh();
      } catch (e) {
        if (e instanceof ApiError) setError(e.message);
        else setError('Sign in failed.');
      }
    });
  }

  return (
    <form action={handleSubmit}>
      {error ? (
        <div
          className="tag-pill tag-danger"
          role="alert"
          style={{ display: 'block', marginBottom: '0.75rem', padding: '0.5rem' }}
        >
          {error}
        </div>
      ) : null}

      <label style={{ display: 'block', marginBottom: '0.75rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>Email</span>
        <input
          name="email"
          type="email"
          required
          autoComplete="email"
          className="input"
          disabled={isPending}
        />
      </label>

      <label style={{ display: 'block', marginBottom: '1rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>Password</span>
        <input
          name="password"
          type="password"
          required
          autoComplete="current-password"
          className="input"
          disabled={isPending}
        />
      </label>

      <button className="btn btn-primary" type="submit" disabled={isPending} style={{ width: '100%' }}>
        {isPending ? 'Signing in…' : 'Sign in'}
      </button>

      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
        <button
          className="btn"
          type="button"
          disabled={isPending}
          style={{ flex: 1 }}
          onClick={() => providerLogin('apple')}
        >
          Apple
        </button>
        <button
          className="btn"
          type="button"
          disabled={isPending}
          style={{ flex: 1 }}
          onClick={() => providerLogin('google')}
        >
          Google
        </button>
      </div>
      <p className="muted" style={{ fontSize: '0.75rem', marginTop: '0.5rem' }}>
        Apple/Google buttons send a dev placeholder token; wire up real OAuth in production.
      </p>
    </form>
  );
}