'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useState, useTransition } from 'react';

export function StationsSearch({
  initialQ,
  initialConnector,
  initialMinKw,
}: {
  initialQ: string;
  initialConnector: string;
  initialMinKw: string;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(initialQ);
  const [connector, setConnector] = useState(initialConnector);
  const [minKw, setMinKw] = useState(initialMinKw);
  const [isPending, startTransition] = useTransition();

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const sp = new URLSearchParams(params);
    if (q) sp.set('q', q); else sp.delete('q');
    if (connector) sp.set('connector', connector); else sp.delete('connector');
    if (minKw) sp.set('min_kw', minKw); else sp.delete('min_kw');
    startTransition(() => {
      router.push(`/stations?${sp.toString()}`);
    });
  }

  return (
    <form onSubmit={apply} style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', flexWrap: 'wrap' }}>
      <input
        className="input"
        placeholder="Search name or address"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ flex: 2, minWidth: 220 }}
      />
      <select className="input" value={connector} onChange={(e) => setConnector(e.target.value)} style={{ flex: 1, minWidth: 140 }}>
        <option value="">Any connector</option>
        <option value="ccs2">CCS2</option>
        <option value="type2">Type 2</option>
        <option value="chademo">CHAdeMO</option>
        <option value="tesla">Tesla</option>
      </select>
      <input
        className="input"
        type="number"
        placeholder="Min kW"
        value={minKw}
        onChange={(e) => setMinKw(e.target.value)}
        style={{ flex: 1, minWidth: 120 }}
      />
      <button className="btn btn-primary" type="submit" disabled={isPending}>
        {isPending ? 'Searching…' : 'Search'}
      </button>
    </form>
  );
}