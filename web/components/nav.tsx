'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useTransition } from 'react';
import { logoutAction } from '@/app/login/actions';

const baseLinks: { href: string; label: string }[] = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/stations', label: 'Stations' },
  { href: '/sessions', label: 'Sessions' },
];

const adminLinks: { href: string; label: string }[] = [
  { href: '/admin/stations', label: 'Admin: Stations' },
  { href: '/admin/ledger', label: 'Admin: Ledger' },
];

export function Nav({
  user,
}: {
  /** Undefined on public pages (login/signup). The Nav hides the
   * profile chip and admin links when undefined. */
  user?: { display_name: string; is_admin: boolean };
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  function onLogout() {
    startTransition(async () => {
      await logoutAction();
      router.push('/login');
      router.refresh();
    });
  }

  const isAdmin = user?.is_admin ?? false;
  const displayName = user?.display_name ?? "";

  // When not logged in (login/signup pages), only show the brand mark
  // and a "Sign in" / "Sign up" link — no profile chip, no log-out button.
  if (!user) {
    return (
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '1rem',
          padding: '0.75rem 1rem',
          borderBottom: '1px solid var(--border)',
          background: 'var(--card)',
        }}
      >
        <Link href="/" style={{ fontWeight: 600, marginRight: '1rem' }}>
          EV Wallet HK
        </Link>
        <div style={{ flex: 1 }} />
        <Link href="/login" style={{ fontSize: '0.85rem' }}>
          Sign in
        </Link>
      </nav>
    );
  }

  const links = isAdmin ? [...baseLinks, ...adminLinks] : baseLinks;

  return (
    <nav
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '1rem',
        padding: '0.75rem 1rem',
        borderBottom: '1px solid var(--border)',
        background: 'var(--card)',
      }}
    >
      <Link href="/dashboard" style={{ fontWeight: 600, marginRight: '1rem' }}>
        EV Wallet HK
      </Link>
      <div style={{ display: 'flex', gap: '0.5rem', flex: 1 }}>
        {links.map((l) => {
          const active = pathname === l.href || pathname.startsWith(`${l.href}/`);
          return (
            <Link
              key={l.href}
              href={l.href}
              style={{
                padding: '0.35rem 0.7rem',
                borderRadius: 6,
                background: active ? 'var(--accent)' : 'transparent',
                color: active ? '#fff' : 'var(--fg)',
              }}
            >
              {l.label}
            </Link>
          );
        })}
      </div>
      <span className="muted" style={{ fontSize: '0.85rem' }}>
        {displayName}
      </span>
      <button className="btn" type="button" onClick={onLogout} disabled={isPending}>
        {isPending ? '…' : 'Sign out'}
      </button>
    </nav>
  );
}