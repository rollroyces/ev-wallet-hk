'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { getCurrentUser } from '@/lib/auth';

// Landing page: route based on whether a session token is in localStorage.
// SSR has no localStorage so this must run client-side. Renders nothing
// while we decide; useRouter.push is synchronous-ish so the flash is short.
export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    const user = getCurrentUser();
    if (user) router.replace('/dashboard');
    else router.replace('/login');
  }, [router]);

  return (
    <main style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--muted)' }}>
      Loading…
    </main>
  );
}