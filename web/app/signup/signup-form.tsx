'use client';

import { useEffect, useState, useTransition } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { registerAction } from '../login/actions';

export function SignupForm({
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
    searchParams.then((p) => setNext(p.next || '/dashboard'));
  }, [searchParams]);

  function handleSubmit(formData: FormData) {
    setError(null);
    startTransition(async () => {
      const result = await registerAction(formData);
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
          type="email"
          required
          autoComplete="email"
          className="input"
          name="email"
        />
      </label>
      <label style={{ display: 'block', marginBottom: '0.75rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>
          Password
        </span>
        <input
          type="password"
          required
          autoComplete="new-password"
          minLength={8}
          className="input"
          name="password"
        />
        <span
          className="muted"
          style={{ display: 'block', fontSize: '0.7rem', marginTop: 4 }}
        >
          At least 8 characters.
        </span>
      </label>
      <label style={{ display: 'block', marginBottom: '1rem' }}>
        <span style={{ display: 'block', fontSize: '0.85rem', marginBottom: 4 }}>
          Confirm password
        </span>
        <input
          type="password"
          required
          autoComplete="new-password"
          minLength={8}
          className="input"
          name="confirm"
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