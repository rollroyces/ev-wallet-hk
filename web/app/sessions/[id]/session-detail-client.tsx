'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { getCurrentUser } from '@/lib/auth';
import { Nav } from '@/components/nav';
import { TelemetryChart } from './telemetry-chart';
import type { ChargingSession } from '@/lib/types';

export function SessionDetailClient({ id }: { id: string }) {
  const router = useRouter();
  const [user, setUser] = useState<{ is_admin: boolean } | null>(null);
  const [session, setSession] = useState<ChargingSession | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const u = getCurrentUser();
      if (!u) {
        router.replace(`/login?next=/sessions/${encodeURIComponent(id)}`);
        return;
      }
      try {
        const api = getApiClient();
        const detail = await api.getSession(id);
        if (cancelled) return;
        setUser({ is_admin: u.is_admin });
        setSession(detail);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError) {
          if (e.status === 404) {
            setError('Session not found.');
            return;
          }
          if (e.status === 401 || e.status === 403) {
            router.replace(`/login?next=/sessions/${encodeURIComponent(id)}`);
            return;
          }
        }
        setError(e instanceof Error ? e.message : 'Failed to load session.');
      }
    })();
    return () => { cancelled = true; };
  }, [router, id]);

  if (error) {
    return (
      <>
        <Nav user={user ?? undefined} />
        <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
          <div className="tag-pill tag-danger" role="alert">{error}</div>
          <p style={{ marginTop: '1rem' }}>
            <Link href="/sessions">← Back to sessions</Link>
          </p>
        </main>
      </>
    );
  }

  if (!session || !user) {
    return (
      <main style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--muted)' }}>
        Loading…
      </main>
    );
  }

  return (
    <>
      <Nav user={user} />
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