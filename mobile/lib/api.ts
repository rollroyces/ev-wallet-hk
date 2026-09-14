/**
 * EV Wallet HK mobile API client.
 *
 * Implements the ApiClient interface defined in /docs/ARCHITECTURE.md.
 *
 * Features:
 *  - JWT attached to Authorization header automatically (Bearer *** * JWT stored in expo-secure-store
 *  - On 401, attempts refresh-token flow; if refresh fails, logs out
 *  - Error envelope parsed; ApiError thrown on non-2xx
 *  - All methods return Promises
 *
 * Deviation from ARCHITECTURE.md (documented in final report):
 *   - Added: registerPushToken() (push notifications — required by mobile)
 *   - Added: getSession(id)      (used by Activity + live monitor)
 *   - Added: getSessions()       (used by Activity tab)
 *
 * NOTE: when Agent A's backend is up, set EXPO_PUBLIC_API_BASE_URL
 * (defaults to http://localhost:8000 in development).
 */

import { API_BASE_URL, API_TIMEOUT_MS } from "./config";
import { auth, logout } from "./auth";
import {
  ApiError,
  ApiErrorBody,
  ChargingSession,
  HourlyRate,
  PaginatedStations,
  PaginatedTransactions,
  PushTokenRegistrationRequest,
  PushTokenRegistrationResult,
  Session,
  SessionEndResult,
  StartSessionRequest,
  StartSessionResponse,
  StationDetail,
  StationSearch,
  TopUpRequest,
  TopUpResult,
  User,
  Wallet,
  WalletSummary,
} from "./types";

export interface ApiClient {
  // Auth
  login(email: string, password: string): Promise<Session>;
  loginApple(identityToken: string): Promise<Session>;
  loginGoogle(idToken: string): Promise<Session>;
  me(): Promise<{ user: User; wallet: WalletSummary }>;

  // Stations
  getStations(params: StationSearch): Promise<PaginatedStations>;
  getStation(id: string): Promise<StationDetail>;
  getStationRates(id: string, date: string): Promise<HourlyRate[]>;

  // Charging
  startSession(qrCode: string, opts?: { targetSocPct?: number }): Promise<StartSessionResponse>;
  endSession(id: string): Promise<SessionEndResult>;

  // Wallet
  getWallet(): Promise<Wallet>;
  getWalletTransactions(cursor?: string): Promise<PaginatedTransactions>;
  topUp(req: TopUpRequest): Promise<TopUpResult>;

  // --- Intent creation (kicks off a topup before client confirms payment) ---
  createStripeIntent(amountHkd: string): Promise<StripeIntentResult>;
  createApplePayIntent(): Promise<ApplePayIntentResult>;

  // --- Extensions beyond ARCHITECTURE.md contract ---
  registerPushToken(req: PushTokenRegistrationRequest): Promise<PushTokenRegistrationResult>;
  getSession(id: string): Promise<ChargingSession>;
  getSessions(limit?: number): Promise<ChargingSession[]>;
}

// Re-exported types the topup screen needs.
export interface StripeIntentResult {
  payment_intent_id: string;
  client_secret: string;
  amount_hkd: string;
  currency: string;
}

export interface ApplePayIntentResult {
  merchant_id: string;
  supported_networks: string[];
  merchant_capabilities: string[];
  currency: string;
  country_code: string;
}

interface FetchOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  skipAuth?: boolean;
  timeoutMs?: number;
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function buildUrl(path: string, query?: FetchOptions["query"]): string {
  const base = `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    params.append(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

async function parseErrorBody(res: Response): Promise<ApiErrorBody | null> {
  try {
    const text = await res.text();
    if (!text) return null;
    const parsed = JSON.parse(text) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      "error" in parsed &&
      parsed.error &&
      typeof parsed.error === "object"
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

/**
 * Internal request — handles auth headers, refresh-on-401, error parsing.
 */
async function request<T>(
  path: string,
  opts: FetchOptions = {},
  refreshFn?: () => Promise<boolean>,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const token = await auth.getAccessToken();
  if (!opts.skipAuth && token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers,
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
  }

  const url = buildUrl(path, opts.query);
  const timeoutMs = opts.timeoutMs ?? API_TIMEOUT_MS;

  let res: Response;
  try {
    res = await fetchWithTimeout(url, init, timeoutMs);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("abort")) {
      throw new ApiError(0, { code: "TIMEOUT", message: `Request to ${path} timed out` });
    }
    throw new ApiError(0, { code: "NETWORK_ERROR", message: `Network error: ${msg}` });
  }

  // 401 -> try refresh, then retry once
  if (res.status === 401 && !opts.skipAuth && refreshFn) {
    const refreshed = await refreshFn();
    if (refreshed) {
      return request<T>(path, opts, refreshFn);
    }
    await logout();
    const body = await parseErrorBody(res);
    throw new ApiError(
      401,
      body?.error ?? { code: "UNAUTHORIZED", message: "Session expired" },
    );
  }

  if (!res.ok) {
    const body = await parseErrorBody(res);
    throw new ApiError(
      res.status,
      body?.error ?? { code: `HTTP_${res.status}`, message: res.statusText },
    );
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Refresh-token flow
// ---------------------------------------------------------------------------

let refreshInflight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (refreshInflight) return refreshInflight;
  refreshInflight = (async () => {
    const refreshToken = await auth.getRefreshToken();
    if (!refreshToken) return false;
    try {
      const res = await fetchWithTimeout(
        buildUrl("/api/v1/auth/refresh"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        },
        API_TIMEOUT_MS,
      );
      if (!res.ok) return false;
      const json = (await res.json()) as {
        access_token: string;
        refresh_token?: string;
      };
      await auth.setAccessToken(json.access_token);
      if (json.refresh_token) {
        await auth.setRefreshToken(json.refresh_token);
      }
      return true;
    } catch {
      return false;
    } finally {
      refreshInflight = null;
    }
  })();
  return refreshInflight;
}

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

export const api: ApiClient = {
  // ---------- Auth ----------
  async login(email, password) {
    const session = await request<Session>(
      "/api/v1/auth/login",
      { method: "POST", body: { email, password }, skipAuth: true },
      tryRefresh,
    );
    await auth.setAccessToken(session.access_token);
    await auth.setUserId(session.user.id);
    return session;
  },

  async loginApple(identityToken) {
    const session = await request<Session>(
      "/api/v1/auth/apple",
      { method: "POST", body: { identity_token: identityToken }, skipAuth: true },
      tryRefresh,
    );
    await auth.setAccessToken(session.access_token);
    await auth.setUserId(session.user.id);
    return session;
  },

  async loginGoogle(idToken) {
    const session = await request<Session>(
      "/api/v1/auth/google",
      { method: "POST", body: { id_token: idToken }, skipAuth: true },
      tryRefresh,
    );
    await auth.setAccessToken(session.access_token);
    await auth.setUserId(session.user.id);
    return session;
  },

  async me() {
    return request<{ user: User; wallet: WalletSummary }>(
      "/api/v1/auth/me",
      { method: "GET" },
      tryRefresh,
    );
  },

  // ---------- Stations ----------
  async getStations(params) {
    return request<PaginatedStations>(
      "/api/v1/stations",
      {
        method: "GET",
        query: {
          lat: params.lat,
          lng: params.lng,
          radius_km: params.radius_km ?? 5,
          connector: params.connector,
          min_kw: params.min_kw,
          limit: params.limit ?? 50,
        },
      },
      tryRefresh,
    );
  },

  async getStation(id) {
    return request<StationDetail>(`/api/v1/stations/${encodeURIComponent(id)}`, { method: "GET" }, tryRefresh);
  },

  async getStationRates(id, date) {
    return request<HourlyRate[]>(
      `/api/v1/stations/${encodeURIComponent(id)}/rates`,
      { method: "GET", query: { date } },
      tryRefresh,
    );
  },

  // ---------- Charging sessions ----------
  async startSession(qrCode, opts) {
    const body: StartSessionRequest = {
      qr_code: qrCode,
      ...(opts?.targetSocPct !== undefined ? { target_soc_pct: opts.targetSocPct } : {}),
    };
    return request<StartSessionResponse>(
      "/api/v1/charging/sessions",
      { method: "POST", body },
      tryRefresh,
    );
  },

  async endSession(id) {
    return request<SessionEndResult>(
      `/api/v1/charging/sessions/${encodeURIComponent(id)}/end`,
      { method: "POST" },
      tryRefresh,
    );
  },

  // ---------- Wallet ----------
  async getWallet() {
    return request<Wallet>("/api/v1/wallet", { method: "GET" }, tryRefresh);
  },

  async getWalletTransactions(cursor) {
    return request<PaginatedTransactions>(
      "/api/v1/wallet/transactions",
      { method: "GET", query: { cursor } },
      tryRefresh,
    );
  },

  async topUp(req) {
    return request<TopUpResult>(
      "/api/v1/wallet/topup",
      {
        method: "POST",
        body: {
          amount_hkd: req.amount_hkd,
          source: req.source,
          source_payload: req.source_payload,
        },
      },
      tryRefresh,
    );
  },

  // ---------- Extensions ----------
  async registerPushToken(req) {
    return request<PushTokenRegistrationResult>(
      "/api/v1/auth/push-tokens",
      {
        method: "POST",
        body: {
          token: req.token,
          platform: req.platform,
          app_version: req.app_version,
          device_id: req.device_id,
        },
      },
      tryRefresh,
    );
  },

  async getSession(id) {
    return request<ChargingSession>(
      `/api/v1/charging/sessions/${encodeURIComponent(id)}`,
      { method: "GET" },
      tryRefresh,
    );
  },

  async getSessions(limit = 20) {
    return request<ChargingSession[]>(
      "/api/v1/charging/sessions",
      { method: "GET", query: { limit } },
      tryRefresh,
    );
  },

  // ---------- Topup intent creation ----------
  async createStripeIntent(amountHkd) {
    return request<StripeIntentResult>(
      "/api/v1/wallet/topup/stripe/intent",
      { method: "POST", body: { amount_hkd: amountHkd } },
      tryRefresh,
    );
  },

  async createApplePayIntent() {
    return request<ApplePayIntentResult>(
      "/api/v1/wallet/topup/apple/intent",
      { method: "POST" },
      tryRefresh,
    );
  },
};
