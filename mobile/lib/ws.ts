/**
 * WebSocket client for live charging session telemetry.
 *
 * Implements the WS protocol from /docs/ARCHITECTURE.md:
 *  - Connects to {ws_url}?token={jwt} (token passed as query param)
 *  - Auto-reconnects with exponential backoff (1s, 2s, 4s, 8s, ... capped at 30s)
 *  - Exposes subscribe/unsubscribe for telemetry frames
 *  - Sends client frames (ping, set_target_soc, end_session)
 */

import { API_BASE_URL } from "./config";
import { auth } from "./auth";
import type { ClientFrame, ServerFrame } from "./types";

export interface TelemetryClientOptions {
  sessionId: string;
  /** Override ws base; otherwise derived from API_BASE_URL */
  wsBase?: string;
  onFrame: (frame: ServerFrame) => void;
  onStatus?: (status: WsConnectionStatus) => void;
  onError?: (err: Error) => void;
}

export type WsConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "reconnecting"
  | "failed";

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;
const MAX_ATTEMPTS_BEFORE_FAIL = 8;

export class TelemetryClient {
  private ws: WebSocket | null = null;
  private wsBase: string;
  private sessionId: string;
  private onFrame: (frame: ServerFrame) => void;
  private onStatus: (status: WsConnectionStatus) => void;
  private onError: (err: Error) => void;

  private attempts = 0;
  private explicitlyClosed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private status: WsConnectionStatus = "idle";

  constructor(opts: TelemetryClientOptions) {
    this.sessionId = opts.sessionId;
    this.wsBase = opts.wsBase ?? API_BASE_URL.replace(/^http/, "ws");
    this.onFrame = opts.onFrame;
    this.onStatus = opts.onStatus ?? (() => undefined);
    this.onError = opts.onError ?? (() => undefined);
  }

  async connect(): Promise<void> {
    this.explicitlyClosed = false;
    const token = await auth.getAccessToken();
    if (!token) {
      const err = new Error("No access token for WS auth");
      this.onError(err);
      this.setStatus("failed");
      throw err;
    }
    this.openSocket(token);
  }

  private openSocket(token: string): void {
    const url =
      `${this.wsBase}/api/v1/charging/sessions/${encodeURIComponent(this.sessionId)}/stream` +
      `?token=${encodeURIComponent(token)}`;

    this.setStatus("connecting");
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.attempts = 0;
      this.setStatus("open");
      this.startPing();
    };

    ws.onmessage = (ev: WebSocketMessageEvent) => {
      const data = typeof ev.data === "string" ? ev.data : "";
      if (!data) return;
      try {
        const frame = JSON.parse(data) as ServerFrame;
        this.onFrame(frame);
      } catch (e) {
        this.onError(e instanceof Error ? e : new Error(String(e)));
      }
    };

    ws.onerror = () => {
      // The React Native WebSocket polyfill doesn't expose a useful message here;
      // the close event fires immediately after and is the real signal.
      this.onError(new Error("WebSocket error"));
    };

    ws.onclose = () => {
      this.stopPing();
      this.ws = null;
      if (this.explicitlyClosed) {
        this.setStatus("closed");
        return;
      }
      this.scheduleReconnect(token);
    };
  }

  private scheduleReconnect(token: string): void {
    if (this.attempts >= MAX_ATTEMPTS_BEFORE_FAIL) {
      this.setStatus("failed");
      return;
    }
    this.attempts += 1;
    const backoff = Math.min(BASE_BACKOFF_MS * 2 ** (this.attempts - 1), MAX_BACKOFF_MS);
    this.setStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.openSocket(token);
    }, backoff);
  }

  send(frame: ClientFrame): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // Queue not implemented — caller decides whether to retry
      this.onError(new Error("WS not open; frame dropped"));
      return;
    }
    this.ws.send(JSON.stringify(frame));
  }

  setTargetSoc(socPct: number): void {
    this.send({ type: "set_target_soc", soc_pct: socPct });
  }

  endSession(): void {
    this.send({ type: "end_session" });
  }

  close(): void {
    this.explicitlyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopPing();
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.setStatus("closed");
  }

  getStatus(): WsConnectionStatus {
    return this.status;
  }

  private setStatus(s: WsConnectionStatus): void {
    if (s !== this.status) {
      this.status = s;
      this.onStatus(s);
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      this.send({ type: "ping" });
    }, 20_000);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}
