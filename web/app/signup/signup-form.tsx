'use client';

import { useState, useTransition } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { setToken } from '@/lib/auth';
import { getApiClient, ApiError } from '@/lib/api';

export function SignupForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get('next') || '/dashboard';
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function handleSubmit(formData: FormData) {
    setError(null);
    const email = String(formData.get('email') ?? '');
    const password = String(formData.get('password') ?? '');
    const confirm = String(formData.get('confirm') ?? '');

    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }

    startTransition(async () => {
      try {
        const api = getApiClient();
        const session = await api.register(email, password);
        setToken(session.access_token);
        router.push(next);
        router.refresh();
      } catch (e) {
        if (e instanceof ApiError) {
          if (e.status === 401 || e.status === 409) {
            setError('An account with that email already exists. Try signing in.');
          } else {
            setError(e.message);
          }
        } else {
          setError('Sign up failed.');
        }
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

      <label style={{ display: 'block', marginBottom: '0.75rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>
          Password
        </span>
        <input
          name="password"
          type="password"
          required
          autoComplete="new-password"
          minLength={8}
          className="input"
          disabled={isPending}
        />
        <span className="muted" style={{ display: 'block', fontSize: '0.7rem', marginTop: 4 }}>
          At least 8 characters.
        </span>
      </label>

      <label style={{ display: 'block', marginBottom: '1rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>
          Confirm password
        </span>
        <input
          name="confirm"
          type="password"
          required
          autoComplete="new-password"
          minLength={8}
          className="input"
          disabled={isPending}
        />
      </label>

      <button
        className="btn btn-primary"
        type="submit"
        disabled={isPending}
        style={{ width: '100%' }}
      >
        {isPending ? 'Creating account…' : 'Create account'}
      </button>
    </form>
  );
}