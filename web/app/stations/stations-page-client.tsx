'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { getApiClient, ApiError } from '@/lib/api';
import { getCurrentUser } from '@/lib/auth';
import { Nav } from '@/components/nav';
import { ProviderCoverage } from '@/components/provider-coverage';
import { StationsSearch } from './stations-search';
import type { Station, ProviderAvailability, ConnectorType } from '@/lib/types';
import { API_BASE_URL } from '@/lib/config';

interface Data {
  user: { is_admin: boolean };
  stations: Station[];
  total: number;
  providers: ProviderAvailability[];
}

export function StationsPageClient() {
  const router = useRouter();
  const params = useSearchParams();
  const q = (params.get('q') ?? '').toLowerCase();
  const connector = params.get('connector') ?? '';
  const minKw = params.get('min_kw') ?? '';

  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const u = getCurrentUser();
      if (!u) {
        router.replace('/login?next=/stations');
        return;
      }
      try {
        const api = getApiClient();
        const me = await api.me();
        const result = await api.getStations({
          lat: 22.302711,
          lng: 114.177216,
          radius_km: 100,
          connector: (connector as ConnectorType | undefined) || undefined,
          min_kw: minKw ? Number(minKw) : undefined,
          limit: 100,
        });
        // Coverage is a public endpoint — failure non-fatal.
        let providers: ProviderAvailability[] = [];
        try {
          const cov = await fetch(`${API_BASE_URL}/api/v1/providers/availability`, {
            cache: 'no-store',
          });
          if (cov.ok) providers = (await cov.json()) as ProviderAvailability[];
        } catch { /* ignore */ }
        if (cancelled) return;
        setData({
          user: { is_admin: me.user.is_admin },
          stations: result.stations,
          total: result.total,
          providers,
        });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          router.replace('/login?next=/stations');
          return;
        }
        setError(e instanceof Error ? e.message : 'Failed to load stations.');
      }
    })();
    return () => { cancelled = true; };
  }, [router, connector, minKw]);

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

  const filtered = q
    ? data.stations.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.address.toLowerCase().includes(q) ||
          s.district?.toLowerCase().includes(q),
      )
    : data.stations;

  return (
    <>
      <Nav user={data.user} />
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }}>
        <h1>Stations</h1>
        <p className="muted">{data.total} total · showing {filtered.length}</p>
        <ProviderCoverage providers={data.providers} />
        <StationsSearch
          initialQ={params.get('q') ?? ''}
          initialConnector={connector}
          initialMinKw={minKw}
        />
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
                  <Link href={`/stations/${encodeURIComponent(s.id)}`}>{s.name}</Link>
                </td>
                <td style={{ padding: '0.5rem' }}>
                  <span className="tag-pill">{s.provider_code}</span>
                </td>
                <td style={{ padding: '0.5rem' }}>{s.district ?? '—'}</td>
                <td style={{ padding: '0.5rem' }} className="muted">{s.address}</td>
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