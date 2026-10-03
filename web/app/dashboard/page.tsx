'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { getCurrentUser, type CurrentUser } from '@/lib/auth';
import { Nav } from '@/components/nav';
import { TopupButton } from '@/components/topup-button';
import type { Wallet, WalletSummary } from '@/lib/types';

interface DashboardData {
  user: CurrentUser;
  wallet: Wallet;
  recent: Wallet['recent_transactions'];
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const user = getCurrentUser();
      if (!user) {
        router.replace('/login?next=/dashboard');
        return;
      }
      try {
        const api = getApiClient();
        const me = await api.me();
        const wallet = await api.getWallet();
        if (cancelled) return;
        setData({
          user,
          wallet,
          recent: wallet.recent_transactions,
        });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          router.replace('/login?next=/dashboard');
          return;
        }
        setError(e instanceof Error ? e.message : 'Failed to load dashboard.');
      }
    })();
    return () => { cancelled = true; };
  }, [router]);

  if (error) {
    return (
      <>
        <Nav user={data ? { display_name: '', is_admin: false } : undefined} />
        <main style={{ maxWidth: 960, margin: '0 auto', padding: '1.5rem 1rem' }}>
          <div className="tag-pill tag-danger" role="alert">{error}</div>
        </main>
      </>
    );
  }

  if (!data) {
    return (
      <main style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--muted)' }}>
        Loading…
      </main>
    );
  }

  const { wallet, recent } = data;
  const currency = wallet.currency || 'HKD';

  return (
    <>
      <Nav user={{ display_name: '', is_admin: data.user.is_admin }} />
      <main style={{ maxWidth: 960, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <h1 style={{ margin: 0 }}>Wallet</h1>
          <TopupButton />
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: '1rem',
            margin: '1rem 0',
          }}
        >
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Available</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>
              {wallet.available_hkd} {currency}
            </div>
          </div>
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Reserved</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>
              {wallet.reserved_hkd} {currency}
            </div>
          </div>
        </div>

        <h2 style={{ marginTop: '1.5rem' }}>Recent activity</h2>
        {recent.length === 0 ? (
          <p className="muted">No transactions yet.</p>
        ) : (
          <table className="card" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '0.5rem' }}>When</th>
                <th style={{ padding: '0.5rem' }}>Kind</th>
                <th style={{ padding: '0.5rem' }}>Description</th>
                <th style={{ padding: '0.5rem', textAlign: 'right' }}>Amount</th>
                <th style={{ padding: '0.5rem' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.5rem' }} className="muted">
                    {new Date(t.posted_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '0.5rem' }}>{t.kind}</td>
                  <td style={{ padding: '0.5rem' }}>{t.description}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                    {t.amount} {currency}
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    <span
                      className={`tag-pill ${t.status === 'posted' ? 'tag-ok' : t.status === 'reversed' ? 'tag-danger' : ''}`}
                    >
                      {t.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </>
  );
}