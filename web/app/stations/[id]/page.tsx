import { redirect, notFound } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import { Nav } from '@/components/nav';
import { Rates24hChart } from './rates-chart';

export const dynamic = 'force-dynamic';

export default async function StationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const cookie = await getCookieHeader();
  const api = getApiClient(cookie);

  let user: { display_name: string; is_admin: boolean };
  let station: Awaited<ReturnType<typeof api.getStation>>;

  try {
    const me = await api.me();
    user = me.user;
    station = await api.getStation(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) redirect('/login');
    throw e;
  }

  const rates = station.rates_next_24h ?? [];

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>{station.station.name}</h1>
        <p className="muted">
          {station.station.address}
          {station.station.district ? ` · ${station.station.district}` : ''}
        </p>
        <div className="muted" style={{ fontSize: '0.85rem', marginBottom: '1rem' }}>
          Provider: <span className="tag-pill">{station.station.provider_code}</span>{' '}
          Parking fee: {station.station.parking_fee_hkd} HKD
        </div>

        <h2>24-hour rates (HKD / kWh)</h2>
        <Rates24hChart rates={rates} />

        <h2 style={{ marginTop: '1.5rem' }}>Poles</h2>
        <table className="card" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '0.5rem' }}>Connector</th>
              <th style={{ padding: '0.5rem' }}>Speed tier</th>
              <th style={{ padding: '0.5rem' }}>Max kW</th>
              <th style={{ padding: '0.5rem' }}>Status</th>
              <th style={{ padding: '0.5rem' }}>Updated</th>
            </tr>
          </thead>
          <tbody>
            {station.poles.map((p) => (
              <tr key={p.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem' }}>{p.connector}</td>
                <td style={{ padding: '0.5rem' }}>{p.speed_tier}</td>
                <td style={{ padding: '0.5rem' }}>{p.max_kw}</td>
                <td style={{ padding: '0.5rem' }}>
                  <span
                    className={`tag-pill ${p.status === 'available' ? 'tag-ok' : p.status === 'fault' || p.status === 'offline' ? 'tag-danger' : ''}`}
                  >
                    {p.status}
                  </span>
                </td>
                <td style={{ padding: '0.5rem' }} className="muted">
                  {new Date(p.status_updated_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {station.poles.length === 0 ? (
              <tr><td colSpan={5} style={{ padding: '0.75rem' }} className="muted">No poles registered.</td></tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}