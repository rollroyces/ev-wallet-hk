import type { ProviderAvailability, ProviderStatus } from '@/lib/types';

const STATUS_STYLES: Record<ProviderStatus, { bg: string; color: string; label: string }> = {
  live: { bg: '#10b981', color: '#fff', label: 'Live' },
  needs_config: { bg: '#f59e0b', color: '#fff', label: 'Needs API key' },
  coming_soon: { bg: '#6b7280', color: '#fff', label: 'Coming soon' },
};

export function ProviderCoverage({ providers }: { providers: ProviderAvailability[] }) {
  if (providers.length === 0) return null;
  return (
    <details className="card" style={{ marginTop: '1rem', padding: '0.75rem 1rem' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 500, listStyle: 'none' }}>
        Network coverage ({providers.length} networks)
        <span className="muted" style={{ fontWeight: 400, marginLeft: '0.5rem' }}>
          — {providers.filter((p) => p.status === 'live').length} live
        </span>
      </summary>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '0.75rem' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
            <th style={{ padding: '0.4rem' }}>Network</th>
            <th style={{ padding: '0.4rem' }}>Status</th>
            <th style={{ padding: '0.4rem' }}>Notes</th>
            <th style={{ padding: '0.4rem' }}></th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => {
            const s = STATUS_STYLES[p.status];
            return (
              <tr key={p.code} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.4rem' }}>{p.name}</td>
                <td style={{ padding: '0.4rem' }}>
                  <span
                    style={{
                      background: s.bg,
                      color: s.color,
                      padding: '0.15rem 0.5rem',
                      borderRadius: 4,
                      fontSize: '0.8rem',
                    }}
                  >
                    {s.label}
                  </span>
                </td>
                <td style={{ padding: '0.4rem' }} className="muted">
                  {p.blurb}
                </td>
                <td style={{ padding: '0.4rem', textAlign: 'right', fontSize: '0.85rem' }}>
                  {p.status === 'needs_config' && p.setup_url ? (
                    <a href={p.setup_url} target="_blank" rel="noopener noreferrer">
                      Get API key →
                    </a>
                  ) : p.status === 'coming_soon' && p.contact_email ? (
                    <a href={`mailto:${p.contact_email}`}>{p.contact_email}</a>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </details>
  );
}
