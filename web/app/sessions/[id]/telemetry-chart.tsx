'use client';

import { useEffect, useRef, useState } from 'react';
import type { TelemetryFrame } from '@/lib/types';

/**
 * Live telemetry chart for a charging session.
 *
 * Connecting from the browser to the WebSocket endpoint requires forwarding
 * the HTTP-only cookie — which the browser does not expose to JS, and
 * WebSockets cannot carry HttpOnly cookies on cross-origin upgrades.
 *
 * Two production fixes (out of scope for the initial portal):
 *   1. Same-origin WS proxy: rewrite /api/v1/charging/sessions/* /stream
 *      through the Next.js origin so cookies flow automatically.
 *   2. Short-lived signed WS token: a server action issues a one-shot
 *      token; the client opens the WS with ?token=<that>.
 *
 * For now we render a friendly message and the snapshot only.
 */
export function TelemetryChart({ sessionId }: { sessionId: string }) {
  const [points, setPoints] = useState<TelemetryFrame[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    wsRef.current = null;
    return () => {
      wsRef.current?.close();
    };
  }, [sessionId]);

  // We keep `points` / `wsRef` so a future implementation can drop in the
  // WS handler without rewriting the component shape.
  void points;

  return (
    <div className="card" style={{ padding: '1rem' }}>
      <p className="muted" style={{ margin: 0 }}>
        Live telemetry requires an authenticated WebSocket — connect via the
        mobile app for live updates. This page shows the session snapshot.
      </p>
      <p className="muted" style={{ margin: '0.5rem 0 0', fontSize: '0.8rem' }}>
        Session: {sessionId}
      </p>
    </div>
  );
}