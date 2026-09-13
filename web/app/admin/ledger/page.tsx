import { redirect } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import { API_BASE_URL, API_PREFIX } from '@/lib/config';
import { Nav } from '@/components/nav';

export const dynamic = 'force-dynamic';

interface AdminTransactionRow {
  id: string;
  user_id: string;
  wallet_id: string;
  kind: 'topup' | 'charge' | 'refund' | 'fee';
  status: 'pending' | 'posted' | 'reversed';
  amount: string;
  currency: string;
  description: string;
  external_ref: string | null;
  posted_at: string;
}

async function fetchAdminLedger(cookie: string | undefined): Promise<AdminTransactionRow[]> {
  const url = `${API_BASE_URL}${API_PREFIX}/wallet/admin/all-transactions`;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (cookie) headers['Cookie'] = cookie;
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (!res.ok) {
    throw new ApiError(res.status, { code: 'HTTP_ERROR', message: `GET /wallet/admin/all-transactions failed (${res.status})` });
  }
  const data = (await res.json()) as { transactions: AdminTransactionRow[] };
  return data.transactions;
}

export default async function AdminLedgerPage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string }>;
}) {
  const params = await searchParams;
  const cookie = await getCookieHeader();
  const api = getApiClient(cookie);

  let user: { display_name: string; is_admin: boolean };
  let transactions: AdminTransactionRow[] = [];

  try {
    const me = await api.me();
    if (!me.user.is_admin) redirect('/dashboard');
    user = me.user;
    transactions = await fetchAdminLedger(cookie);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) redirect('/login');
    throw e;
  }

  const filtered = params.kind
    ? transactions.filter((t) => t.kind === params.kind)
    : transactions;

  const totalByKind: Record<string, string> = {};
  for (const t of filtered) {
    const k = t.kind;
    const v = Number(t.amount);
    totalByKind[k] = String((Number(totalByKind[k] ?? 0) + v).toFixed(4));
  }

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1300, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Admin · Wallet ledger</h1>
        <div className="muted" style={{ marginBottom: '1rem', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          {Object.entries(totalByKind).map(([kind, total]) => (
            <span key={kind}>
              {kind}: <strong>{total}</strong> HKD
            </span>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
          {(['', 'topup', 'charge', 'refund', 'fee'] as const).map((k) => (
            <a
              key={k || 'all'}
              href={k ? `/admin/ledger?kind=${k}` : '/admin/ledger'}
              className="btn"
              style={{ padding: '0.25rem 0.6rem' }}
            >
              {k || 'all'}
            </a>
          ))}
        </div>

        <table className="card" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '0.5rem' }}>When</th>
              <th style={{ padding: '0.5rem' }}>User</th>
              <th style={{ padding: '0.5rem' }}>Kind</th>
              <th style={{ padding: '0.5rem' }}>Description</th>
              <th style={{ padding: '0.5rem', textAlign: 'right' }}>Amount</th>
              <th style={{ padding: '0.5rem' }}>Status</th>
              <th style={{ padding: '0.5rem' }}>External ref</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {new Date(t.posted_at).toLocaleString()}
                </td>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {t.user_id.slice(0, 8)}…
                </td>
                <td style={{ padding: '0.5rem' }}>{t.kind}</td>
                <td style={{ padding: '0.5rem' }}>{t.description}</td>
                <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                  {t.amount} {t.currency}
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <span className={`tag-pill ${t.status === 'posted' ? 'tag-ok' : t.status === 'reversed' ? 'tag-danger' : ''}`}>
                    {t.status}
                  </span>
                </td>
                <td style={{ padding: '0.5rem' }} className="muted">{t.external_ref ?? '—'}</td>
              </tr>
            ))}
            {filtered.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: '0.75rem' }} className="muted">No transactions.</td></tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}