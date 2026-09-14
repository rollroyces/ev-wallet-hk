import { redirect } from 'next/navigation';
import { getApiClient, getCookieHeader } from '@/lib/api';
import { ApiError } from '@/lib/api';
import { Nav } from '@/components/nav';
import { TopupButton } from '@/components/topup-button';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const api = getApiClient(await getCookieHeader());

  let user: { display_name: string; is_admin: boolean };
  let available = '0';
  let reserved = '0';
  let currency = 'HKD';
  let recent: Awaited<ReturnType<typeof api.getWallet>>['recent_transactions'] = [];

  try {
    const me = await api.me();
    user = me.user;
    const wallet = await api.getWallet();
    available = wallet.available_hkd;
    reserved = wallet.reserved_hkd;
    currency = wallet.currency;
    recent = wallet.recent_transactions;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      redirect('/login');
    }
    throw e;
  }

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
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
              {available} {currency}
            </div>
          </div>
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Reserved</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>
              {reserved} {currency}
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