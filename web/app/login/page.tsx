import { Suspense } from 'react';
import Link from 'next/link';
import { LoginForm } from './login-form';
import { APP_NAME } from '@/lib/config';

// Suspense wraps the client form so static export accepts its useSearchParams().
export default function LoginPage() {
  return (
    <main style={{ maxWidth: 420, margin: '4rem auto', padding: '0 1rem' }}>
      <div className="card" style={{ padding: '1.5rem' }}>
        <h1 style={{ marginBottom: '0.25rem' }}>Sign in</h1>
        <p className="muted" style={{ marginBottom: '1.25rem' }}>
          {APP_NAME} — admin &amp; desktop portal
        </p>
        <Suspense fallback={<div style={{ color: 'var(--muted)' }}>Loading…</div>}>
          <LoginForm />
        </Suspense>
        <p className="muted" style={{ fontSize: '0.85rem', marginTop: '1rem' }}>
          Don&apos;t have an account?{' '}
          <Link href="/signup" style={{ color: 'var(--accent)' }}>
            Create one
          </Link>
        </p>
      </div>
    </main>
  );
}