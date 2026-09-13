'use client';

import { useEffect, useState, useTransition } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { loginAction } from './actions';

export function LoginForm({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = useSearchParams();
  const router = useRouter();
  const [next, setNext] = useState<string>('/dashboard');
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    searchParams.then((p) => {
      setNext(p.next || '/dashboard');
      if (p.error === 'forbidden') {
        setError('You do not have access to that area.');
      }
    });
  }, [searchParams]);

  function handleSubmit(formData: FormData) {
    setError(null);
    startTransition(async () => {
      const result = await loginAction(formData);
      if (result.ok) {
        router.push(next || '/dashboard');
        router.refresh();
      } else {
        setError(result.message);
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
          onClick={() => startTransition(async () => {
            const r = await loginAction(new FormData(), 'apple');
            if (r.ok) { router.push(next); router.refresh(); } else setError(r.message);
          })}
        >
          Apple
        </button>
        <button
          className="btn"
          type="button"
          disabled={isPending}
          style={{ flex: 1 }}
          onClick={() => startTransition(async () => {
            const r = await loginAction(new FormData(), 'google');
            if (r.ok) { router.push(next); router.refresh(); } else setError(r.message);
          })}
        >
          Google
        </button>
      </div>
      <p className="muted" style={{ fontSize: '0.75rem', marginTop: '0.5rem' }}>
        Apple/Google buttons accept a pasted ID token in dev; wire up real OAuth in production.
      </p>
    </form>
  );
}