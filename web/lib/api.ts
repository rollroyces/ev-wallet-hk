/**
 * EV Wallet HK web API client (static-export build).
 *
 * Browser-only. The previous implementation used server components +
 * cookie forwarding + server actions, none of which work with
 * `next export` / GitHub Pages. This version:
 *   - Reads the JWT from localStorage and sends it as Authorization: Bearer.
 *   - Calls the public API base (NEXT_PUBLIC_API_BASE_URL) directly from
 *     the browser. In production this URL points at the Cloudflare Worker
 *     that proxies + CORS-rewrites requests to the FastAPI backend.
 *   - The backend still does the real auth check; this client just
 *     forwards the token.
 */

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
import { API_BASE_URL, API_PREFIX } from './config';
import { getToken } from './auth';

export interface ApiClient {
  login(email: string, password: string): Promise<Session>;
  register(email: string, password: string): Promise<Session>;
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
): Promise<T> {
  const url = `${API_BASE_URL}${buildPath(path, opts.query)}`;
  const headersInit: Record<string, string> = {
    Accept: 'application/json',
  };
  if (opts.body !== undefined) headersInit['Content-Type'] = 'application/json';

  const token = getToken();
  if (token) headersInit['Authorization'] = `Bearer ${token}`;

  const init: RequestInit = {
    method: opts.method ?? 'GET',
    headers: headersInit,
    cache: 'no-store',
    credentials: 'omit',
    mode: 'cors',
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
 * Browser-side ApiClient. Reads the JWT from localStorage on every call.
 */
export function getApiClient(): ApiClient {
  const call = <T>(path: string, opts: RequestOptions = {}) =>
    request<T>(path, opts);

  return {
    // ---------- Auth ----------
    async login(email, password) {
      return call<Session>('/auth/login', {
        method: 'POST',
        body: { email, password },
      });
    },
    async register(email, password) {
      // Backend may return either a Session directly (sets up cookies + token)
      // or just 201. If 201, the caller follows up with login().
      try {
        return await call<Session>('/auth/register', {
          method: 'POST',
          body: { email, password },
        });
      } catch (e) {
        // Some backends return 204 No Content on register. Fall back to login.
        if (e instanceof ApiError && e.status === 204) {
          return call<Session>('/auth/login', {
            method: 'POST',
            body: { email, password },
          });
        }
        throw e;
      }
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