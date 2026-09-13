'use client';

import type { HourlyRate } from '@/lib/types';
import { useMemo } from 'react';

/**
 * Minimal SVG line chart of price_per_kwh_hkd over a 24h window.
 * Server-renders an SVG so we avoid pulling in a chart library.
 */
export function Rates24hChart({ rates }: { rates: HourlyRate[] }) {
  const series = useMemo(() => {
    // Aggregate by hour (00..23). Multiple poles contribute; we average per
    // hour across poles.
    const buckets = new Map<number, { sum: number; n: number }>();
    for (const r of rates) {
      const hh = Number(r.hour_start_local.slice(0, 2));
      if (Number.isNaN(hh)) continue;
      const cur = buckets.get(hh) ?? { sum: 0, n: 0 };
      cur.sum += Number(r.price_per_kwh_hkd);
      cur.n += 1;
      buckets.set(hh, cur);
    }
    const out: { hour: number; price: number }[] = [];
    for (let h = 0; h < 24; h += 1) {
      const b = buckets.get(h);
      out.push({ hour: h, price: b && b.n > 0 ? b.sum / b.n : 0 });
    }
    return out;
  }, [rates]);

  const W = 720;
  const H = 220;
  const PAD = 32;
  const innerW = W - PAD * 2;
  const innerH = H - PAD * 2;
  const maxP = Math.max(1, ...series.map((p) => p.price));
  const stepX = innerW / 23;

  const points = series.map((p, i) => {
    const x = PAD + i * stepX;
    const y = PAD + innerH - (p.price / maxP) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 720, height: 220 }} className="card">
      {/* axes */}
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--border)" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--border)" />
      {/* y label */}
      <text x={4} y={PAD} fontSize={10} fill="var(--muted)">{maxP.toFixed(2)}</text>
      <text x={4} y={H - PAD} fontSize={10} fill="var(--muted)">0</text>
      {/* x labels every 6h */}
      {[0, 6, 12, 18, 23].map((h) => {
        const x = PAD + h * stepX;
        return (
          <text key={h} x={x} y={H - 8} fontSize={10} fill="var(--muted)" textAnchor="middle">
            {h.toString().padStart(2, '0')}:00
          </text>
        );
      })}
      {/* line */}
      <polyline fill="none" stroke="var(--accent)" strokeWidth={2} points={points.join(' ')} />
      {/* dots */}
      {series.map((p, i) => {
        const x = PAD + i * stepX;
        const y = PAD + innerH - (p.price / maxP) * innerH;
        return p.price > 0 ? <circle key={i} cx={x} cy={y} r={2.5} fill="var(--accent)" /> : null;
      })}
    </svg>
  );
}