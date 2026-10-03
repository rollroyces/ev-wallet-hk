import { StationDetailClient } from './station-detail-client';

// Static export requires generateStaticParams to return at least one entry
// (Next.js's "output: export" check refuses empty arrays for dynamic
// routes). The placeholder entry is enough to make Next emit one HTML
// file for /stations/[id]; actual station IDs are unknown at build time
// and are resolved by the client component from the URL.
export function generateStaticParams() {
  return [{ id: '_' }];
}

export default async function StationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <StationDetailClient id={id} />;
}