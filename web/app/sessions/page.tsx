'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { getCurrentUser } from '@/lib/auth';
import { Nav } from '@/components/nav';
import type { ChargingSession } from '@/lib/types';

interface Data {
  user: { is_admin: boolean };
  sessions: ChargingSession[];
}

export default function SessionsPage() {
  const router = useRouter();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const u = getCurrentUser();
      if (!u) {
        router.replace('/login?next=/sessions');
        return;
      }
      try {
        const api = getApiClient();
        const me = await api.me();
        const sessions = await api.getSessions(50);
        if (cancelled) return;
        setData({ user: { is_admin: me.user.is_admin }, sessions });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          router.replace('/login?next=/sessions');
          return;
        }
        setError(e instanceof Error ? e.message : 'Failed to load sessions.');
      }
    })();
    return () => { cancelled = true; };
  }, [router]);

  if (error) {
    return (
      <>
        <Nav user={data ? data.user : undefined} />
        <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
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

  return (
    <>
      <Nav user={data.user} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Charging sessions</h1>
        <p className="muted">{data.sessions.length} session{data.sessions.length === 1 ? '' : 's'}.</p>

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
            {data.sessions.map((s) => (
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
                  <Link href={`/sessions/${encodeURIComponent(s.id)}`}>open</Link>
                </td>
              </tr>
            ))}
            {data.sessions.length === 0 ? (
              <tr><td colSpan={6} style={{ padding: '0.75rem' }} className="muted">No past sessions.</td></tr>
            ) : null}
          </tbody>
        </table>
      </main>
    </>
  );
}