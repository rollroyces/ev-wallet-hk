import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import { Nav } from '@/components/nav';

export const dynamic = 'force-dynamic';

export default async function SessionsPage() {
  const api = getApiClient(await getCookieHeader());

  let user: { display_name: string; is_admin: boolean };
  let sessions: Awaited<ReturnType<typeof api.getSessions>> = [];

  try {
    const me = await api.me();
    user = me.user;
    sessions = await api.getSessions(50);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) redirect('/login');
    throw e;
  }

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Charging sessions</h1>
        <p className="muted">{sessions.length} session{sessions.length === 1 ? '' : 's'}.</p>

        <table className="card" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '0.5rem' }}>Started</th>
              <th style={{ padding: '0.5rem' }}>Ended</th>
              <th style={{ padding: '0.5rem' }}>Status</th>
              <th style={{ padding: '0.5rem', textAlign: 'right' }}>kWh</th>
              <th style={{ padding: '0.5rem', textAlign: 'right' }}>Cost (HKD)</th>
              <th style={{ padding: '0.5rem' }}></th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {new Date(s.started_at).toLocaleString()}
                </td>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {s.ended_at ? new Date(s.ended_at).toLocaleString() : '—'}
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <span
                    className={`tag-pill ${
                      s.status === 'completed' ? 'tag-ok' :
                      s.status === 'active' ? 'tag-ok' :
                      s.status === 'failed' || s.status === 'cancelled' ? 'tag-danger' : ''
                    }`}
                  >
                    {s.status}
                  </span>
                </td>
                <td style={{ padding: '0.5rem', textAlign: 'right' }}>{s.kwh_delivered}</td>
                <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                  {s.settled_hkd ?? s.running_cost_hkd}
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <Link href={`/sessions/${s.id}`}>open</Link>
                </td>
              </tr>
            ))}
            {sessions.length === 0 ? (
              <tr><td colSpan={6} style={{ padding: '0.75rem' }} className="muted">No past sessions.</td></tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}