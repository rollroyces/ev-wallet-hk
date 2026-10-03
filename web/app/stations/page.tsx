import { Suspense } from 'react';
import { StationsPageClient } from './stations-page-client';

// Static export requires useSearchParams() to live behind a Suspense boundary.
// This server shell owns the boundary so the prerendered HTML can stream in.
export default function StationsPage() {
  return (
    <Suspense fallback={<div style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>}>
      <StationsPageClient />
    </Suspense>
  );
}