'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { getCurrentUser } from '@/lib/auth';
import { Nav } from '@/components/nav';
import { NavigateButton } from '@/components/navigate-button';
import { Rates24hChart } from './rates-chart';
import type { StationDetail } from '@/lib/types';

export function StationDetailClient({ id }: { id: string }) {
  const router = useRouter();
  const [user, setUser] = useState<{ is_admin: boolean } | null>(null);
  const [station, setStation] = useState<StationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const u = getCurrentUser();
      if (!u) {
        router.replace(`/login?next=/stations/${encodeURIComponent(id)}`);
        return;
      }
      try {
        const api = getApiClient();
        const detail = await api.getStation(id);
        if (cancelled) return;
        setUser({ is_admin: u.is_admin });
        setStation(detail);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError) {
          if (e.status === 404) {
            setError('Station not found.');
            return;
          }
          if (e.status === 401 || e.status === 403) {
            router.replace(`/login?next=/stations/${encodeURIComponent(id)}`);
            return;
          }
        }
        setError(e instanceof Error ? e.message : 'Failed to load station.');
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
            <Link href="/stations">← Back to stations</Link>
          </p>
        </main>
      </>
    );
  }

  if (!station || !user) {
    return (
      <main style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--muted)' }}>
        Loading…
      </main>
    );
  }

  const rates = station.rates_next_24h ?? [];

  return (
    <>
      <Nav user={user} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1 style={{ marginBottom: '0.25rem' }}>{station.station.name}</h1>
            <p className="muted" style={{ marginTop: 0 }}>
              {station.station.address}
              {station.station.district ? ` · ${station.station.district}` : ''}
            </p>
            <div className="muted" style={{ fontSize: '0.85rem' }}>
              Provider: <span className="tag-pill">{station.station.provider_code}</span>{' '}
              Parking fee: {station.station.parking_fee_hkd} HKD
            </div>
          </div>
          <NavigateButton
            latitude={Number(station.station.latitude)}
            longitude={Number(station.station.longitude)}
            name={station.station.name}
          />
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