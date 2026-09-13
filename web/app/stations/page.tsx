import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import type { Station, StationProviderCode, ConnectorType } from '@/lib/types';
import { Nav } from '@/components/nav';
import { StationsSearch } from './stations-search';

export const dynamic = 'force-dynamic';

export default async function StationsPage({
  searchParams,
}: {
  searchParams: Promise<{
    q?: string;
    connector?: string;
    min_kw?: string;
  }>;
}) {
  const params = await searchParams;
  const api = getApiClient(await getCookieHeader());

  let user: { display_name: string; is_admin: boolean };
  let stations: Station[] = [];
  let total = 0;

  try {
    const me = await api.me();
    user = me.user;
    const result = await api.getStations({
      // lat/lng optional — when omitted, the backend returns all stations
      // (depends on impl). We pass 0 as a placeholder; backend may ignore.
      lat: 0,
      lng: 0,
      connector: (params.connector as ConnectorType | undefined),
      min_kw: params.min_kw ? Number(params.min_kw) : undefined,
      limit: 100,
    });
    stations = result.stations;
    total = result.total;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      redirect('/login');
    }
    throw e;
  }

  // Server-side text filter on the result set. The backend can grow server-
  // side search; for the desktop portal this is acceptable.
  const q = (params.q ?? '').toLowerCase();
  const filtered = q
    ? stations.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.address.toLowerCase().includes(q) ||
          s.district?.toLowerCase().includes(q),
      )
    : stations;

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Stations</h1>
        <p className="muted">{total} total · showing {filtered.length}</p>
        <StationsSearch initialQ={params.q ?? ''} initialConnector={params.connector ?? ''} initialMinKw={params.min_kw ?? ''} />
        <table className="card" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '0.5rem' }}>Name</th>
              <th style={{ padding: '0.5rem' }}>Provider</th>
              <th style={{ padding: '0.5rem' }}>District</th>
              <th style={{ padding: '0.5rem' }}>Address</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem' }}>
                  <Link href={`/stations/${s.id}`}>{s.name}</Link>
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <span className="tag-pill">{s.provider_code as StationProviderCode}</span>
                </td>
                <td style={{ padding: '0.5rem' }}>{s.district ?? '—'}</td>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {s.address}
                </td>
              </tr>
            ))}
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: '0.75rem' }} className="muted">
                  No stations match.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}