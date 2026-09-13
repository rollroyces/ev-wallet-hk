import { redirect } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import { API_BASE_URL, API_PREFIX } from '@/lib/config';
import { Nav } from '@/components/nav';

export const dynamic = 'force-dynamic';

interface AdminStationRow {
  id: string;
  name: string;
  provider_code: string;
  address: string;
  district: string | null;
  pole_count: number;
  last_synced_at: string;
}

async function fetchAdminStations(cookie: string | undefined): Promise<AdminStationRow[]> {
  const url = `${API_BASE_URL}${API_PREFIX}/internal/stations/list`;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (cookie) headers['Cookie'] = cookie;
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (!res.ok) {
    throw new ApiError(res.status, { code: 'HTTP_ERROR', message: `GET /internal/stations/list failed (${res.status})` });
  }
  const data = (await res.json()) as { stations: AdminStationRow[] };
  return data.stations;
}

export default async function AdminStationsPage() {
  const cookie = await getCookieHeader();
  const api = getApiClient(cookie);

  let user: { display_name: string; is_admin: boolean };
  let stations: AdminStationRow[] = [];

  try {
    const me = await api.me();
    if (!me.user.is_admin) redirect('/dashboard');
    user = me.user;
    stations = await fetchAdminStations(cookie);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) redirect('/login');
    throw e;
  }

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1300, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Admin · Stations</h1>
        <p className="muted">{stations.length} stations across all providers.</p>
        <table className="card" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '0.5rem' }}>Name</th>
              <th style={{ padding: '0.5rem' }}>Provider</th>
              <th style={{ padding: '0.5rem' }}>District</th>
              <th style={{ padding: '0.5rem' }}>Address</th>
              <th style={{ padding: '0.5rem', textAlign: 'right' }}>Poles</th>
              <th style={{ padding: '0.5rem' }}>Last synced</th>
            </tr>
          </thead>
          <tbody>
            {stations.map((s) => (
              <tr key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem' }}>{s.name}</td>
                <td style={{ padding: '0.5rem' }}>
                  <span className="tag-pill">{s.provider_code}</span>
                </td>
                <td style={{ padding: '0.5rem' }}>{s.district ?? '—'}</td>
                <td style={{ padding: '0.5rem' }} className="muted">{s.address}</td>
                <td style={{ padding: '0.5rem', textAlign: 'right' }}>{s.pole_count}</td>
                <td style={{ padding: '0.5rem' }} className="muted">{new Date(s.last_synced_at).toLocaleString()}</td>
              </tr>
            ))}
            {stations.length === 0 ? (
              <tr><td colSpan={6} style={{ padding: '0.75rem' }} className="muted">No stations.</td></tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}