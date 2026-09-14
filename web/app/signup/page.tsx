import Link from 'next/link';
import { Nav } from '@/components/nav';
import { SignupForm } from './signup-form';

export const dynamic = 'force-dynamic';

export default function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  return (
    <>
      <Nav />
      <main style={{ maxWidth: 420, margin: '4rem auto', padding: '0 1rem' }}>
        <div className="card" style={{ padding: '1.5rem' }}>
          <h1 style={{ marginBottom: '0.25rem' }}>Create account</h1>
          <p className="muted" style={{ marginBottom: '1.25rem' }}>
            EV Wallet HK — admin &amp; desktop portal
          </p>
          <SignupForm searchParams={searchParams} />
          <p className="muted" style={{ fontSize: '0.85rem', marginTop: '1rem' }}>
            Already have an account?{' '}
            <Link href="/login" style={{ color: 'var(--accent)' }}>
              Sign in
            </Link>
          </p>
        </div>
      </main>
    </>
  );
}