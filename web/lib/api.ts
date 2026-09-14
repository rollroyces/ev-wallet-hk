/**
 * EV Wallet HK web API client.
 *
 * Implements the ApiClient interface, mirroring mobile/lib/api.ts structure
 * so mobile + web stay drop-in siblings. Type names match the shared
 * mobile/lib/types.ts (which is byte-identical to web/lib/types.ts).
 *
 * Differences from mobile/lib/api.ts:
 *   - No refresh-token flow (HTTP-only cookie; server handles refresh).
 *   - No Bearer header (browser / server-side cookie forwarding instead).
 *   - No push-token registration (web doesn't use push).
 *
 * Two execution modes:
 *   - Server:  getApiClient(cookieHeader) — forwards the incoming Cookie
 *     header to the backend so the same JWT is presented.
 *   - Browser: getApiClient() — the browser sends the HTTP-only cookie on
 *     same-origin requests automatically.
 *
 * Base URL: API_BASE_URL from lib/config (defaults to NEXT_PUBLIC_API_BASE_URL).
 */

import { headers } from 'next/headers';
import { API_BASE_URL, API_PREFIX } from './config';
import {
  ApiError,
  type ApiErrorBody,
  type ChargingSession,
  type HourlyRate,
  type PaginatedStations,
  type PaginatedTransactions,
  type Session,
  type SessionEndResult,
  type StartSessionResponse,
  type Station,
  type StationDetail,
  type StationSearch,
  type TopUpRequest,
  type TopUpResult,
  type User,
  type Wallet,
  type WalletSummary,
} from './types';

export interface ApiClient {
  login(email: string, password: string): Promise<Session>;
  loginApple(identityToken: string): Promise<Session>;
  loginGoogle(idToken: string): Promise<Session>;
  me(): Promise<{ user: User; wallet: WalletSummary }>;

  getStations(params: StationSearch): Promise<PaginatedStations>;
  getStation(id: string): Promise<StationDetail>;
  getStationRates(id: string, date: string): Promise<HourlyRate[]>;

  startSession(
    qrCode: string,
    opts?: { targetSocPct?: number },
  ): Promise<StartSessionResponse>;
  endSession(id: string): Promise<SessionEndResult>;

  getWallet(): Promise<Wallet>;
  getWalletTransactions(cursor?: string): Promise<PaginatedTransactions>;
  topUp(req: TopUpRequest): Promise<TopUpResult>;

  // Extensions beyond ARCHITECTURE.md — mirrored from mobile.
  getSession(id: string): Promise<ChargingSession>;
  getSessions(limit?: number): Promise<ChargingSession[]>;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildPath(path: string, query?: RequestOptions['query']): string {
  const prefix = API_PREFIX.endsWith('/') ? API_PREFIX.slice(0, -1) : API_PREFIX;
  const full = path.startsWith('/') ? `${prefix}${path}` : `${prefix}/${path}`;
  if (!query) return full;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    sp.append(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `${full}?${qs}` : full;
}

async function parseErrorBody(res: Response): Promise<ApiErrorBody | null> {
  try {
    const text = await res.text();
    if (!text) return null;
    const parsed = JSON.parse(text) as unknown;
    if (
      parsed &&
      typeof parsed === 'object' &&
      'error' in parsed &&
      parsed.error &&
      typeof parsed.error === 'object'
    ) {
      return parsed as ApiErrorBody;
    }
    return {
      error: {
        code: `HTTP_${res.status}`,
        message: text.slice(0, 500),
      },
    };
  } catch {
    return null;
  }
}

async function request<T>(
  path: string,
  opts: RequestOptions,
  cookieHeader: string | undefined,
): Promise<T> {
  const url = `${API_BASE_URL}${buildPath(path, opts.query)}`;
  const headersInit: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headersInit['Content-Type'] = 'application/json';
  if (cookieHeader) headersInit['Cookie'] = cookieHeader;

  // The backend's auth middleware expects ``Authorization: Bearer <token>``,
  // not a Cookie header. Extract the JWT from the forwarded ``evw_auth``
  // cookie so server-rendered pages can call authenticated endpoints.
  if (cookieHeader) {
    const match = /(?:^|;\s*)evw_auth=([^;]+)/.exec(cookieHeader);
    if (match && match[1]) {
      headersInit['Authorization'] = `Bearer ${decodeURIComponent(match[1])}`;
    }
  }

  const init: RequestInit = {
    method: opts.method ?? 'GET',
    headers: headersInit,
    cache: 'no-store',
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ApiError(0, {
      code: 'NETWORK_ERROR',
      message: `Network error contacting API at ${API_BASE_URL}: ${msg}`,
    });
  }

  if (!res.ok) {
    const body = await parseErrorBody(res);
    throw new ApiError(
      res.status,
      body?.error ?? {
        code: `HTTP_${res.status}`,
        message: res.statusText || 'Request failed',
      },
    );
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Server-side convenience: extract the incoming Cookie header so we can
 * forward it to the backend. Returns undefined when called from a context
 * without request headers (e.g., during static analysis at build time).
 */
export async function getCookieHeader(): Promise<string | undefined> {
  try {
    const h = await headers();
    return h.get('cookie') ?? undefined;
  } catch {
    return undefined;
  }
}

/**
 * Build an ApiClient bound to a particular execution context.
 *   - Pass `cookieHeader` from server components / route handlers / server actions.
 *   - Omit it for client components (the browser sends the cookie).
 */
export function getApiClient(cookieHeader?: string): ApiClient {
  const call = <T>(path: string, opts: RequestOptions = {}) =>
    request<T>(path, opts, cookieHeader);

  return {
    // ---------- Auth ----------
    async login(email, password) {
      return call<Session>('/auth/login', {
        method: 'POST',
        body: { email, password },
      });
    },
    async loginApple(identityToken) {
      return call<Session>('/auth/apple', {
        method: 'POST',
        body: { identity_token: identityToken },
      });
    },
    async loginGoogle(idToken) {
      return call<Session>('/auth/google', {
        method: 'POST',
        body: { id_token: idToken },
      });
    },
    async me() {
      return call<{ user: User; wallet: WalletSummary }>('/auth/me');
    },

    // ---------- Stations ----------
    async getStations(params) {
      return call<PaginatedStations>('/stations', {
        method: 'GET',
        query: {
          lat: params.lat,
          lng: params.lng,
          radius_km: params.radius_km,
          connector: params.connector,
          min_kw: params.min_kw,
          limit: params.limit,
        },
      });
    },
    async getStation(id) {
      return call<StationDetail>(`/stations/${encodeURIComponent(id)}`);
    },
    async getStationRates(id, date) {
      return call<HourlyRate[]>(
        `/stations/${encodeURIComponent(id)}/rates`,
        { method: 'GET', query: { date } },
      );
    },

    // ---------- Charging sessions ----------
    async startSession(qrCode, opts) {
      return call<StartSessionResponse>('/charging/sessions', {
        method: 'POST',
        body: {
          qr_code: qrCode,
          ...(opts?.targetSocPct !== undefined
            ? { target_soc_pct: opts.targetSocPct }
            : {}),
        },
      });
    },
    async endSession(id) {
      return call<SessionEndResult>(
        `/charging/sessions/${encodeURIComponent(id)}/end`,
        { method: 'POST' },
      );
    },

    // ---------- Wallet ----------
    async getWallet() {
      return call<Wallet>('/wallet');
    },
    async getWalletTransactions(cursor) {
      return call<PaginatedTransactions>('/wallet/transactions', {
        method: 'GET',
        query: { cursor },
      });
    },
    async topUp(req) {
      return call<TopUpResult>('/wallet/topup', {
        method: 'POST',
        body: {
          amount_hkd: req.amount_hkd,
          source: req.source,
          source_payload: req.source_payload,
        },
      });
    },

    // ---------- Extensions ----------
    async getSession(id) {
      return call<ChargingSession>(
        `/charging/sessions/${encodeURIComponent(id)}`,
      );
    },
    async getSessions(limit = 20) {
      return call<ChargingSession[]>('/charging/sessions', {
        method: 'GET',
        query: { limit },
      });
    },
  };
}

export { ApiError };
export type { Station };