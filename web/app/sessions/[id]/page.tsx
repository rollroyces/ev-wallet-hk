import { redirect, notFound } from 'next/navigation';
import { getApiClient, getCookieHeader, ApiError } from '@/lib/api';
import { Nav } from '@/components/nav';
import { TelemetryChart } from './telemetry-chart';

export const dynamic = 'force-dynamic';

export default async function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const cookie = await getCookieHeader();
  const api = getApiClient(cookie);

  let user: { display_name: string; is_admin: boolean };
  let session: Awaited<ReturnType<typeof api.getSession>>;

  try {
    const me = await api.me();
    user = me.user;
    session = await api.getSession(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) redirect('/login');
    throw e;
  }

  return (
    <>
      <Nav user={{ display_name: user.display_name, is_admin: user.is_admin }} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Session {id.slice(0, 8)}…</h1>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '1rem',
            margin: '1rem 0',
          }}
        >
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Status</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{session.status}</div>
          </div>
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">kWh delivered</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{session.kwh_delivered}</div>
          </div>
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Running cost</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{session.running_cost_hkd} HKD</div>
          </div>
          <div className="card" style={{ padding: '1rem' }}>
            <div className="muted">Settled</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>
              {session.settled_hkd ?? '—'} HKD
            </div>
          </div>
        </div>

        <h2>Live telemetry</h2>
        <TelemetryChart sessionId={id} />

        <h2 style={{ marginTop: '1.5rem' }}>Metadata</h2>
        <pre className="card" style={{ padding: '1rem', overflow: 'auto' }}>
          {JSON.stringify(session.metadata ?? {}, null, 2)}
        </pre>
      </main>
    </>
  );
}