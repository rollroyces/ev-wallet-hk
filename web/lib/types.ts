/**
 * Shared TypeScript types for EV Wallet HK mobile + web.
 *
 * Source of truth: /docs/ARCHITECTURE.md
 *
 * Agent D owns this file. Agent E (web) keeps `web/lib/types.ts` byte-equal to this.
 *
 * Convention:
 *  - Money is decimal-as-string on the wire to avoid float drift; we type as `string`
 *    here. Convert at the UI layer if needed.
 *  - Timestamps are ISO-8601 strings.
 *  - UUIDs are opaque strings.
 */

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export type AuthProvider = "apple" | "google" | "email";

export interface User {
  id: string;
  email: string | null;
  phone_e164: string | null;
  display_name: string;
  locale: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
}

export interface WalletSummary {
  available_hkd: string;
  reserved_hkd: string;
}

export interface Session {
  access_token: string;
  expires_at: string;
  user: User;
}

// ---------------------------------------------------------------------------
// Wallet
// ---------------------------------------------------------------------------

export type WalletTransactionKind = "topup" | "charge" | "refund" | "fee";
export type WalletTransactionStatus = "pending" | "posted" | "reversed";

export interface WalletTransaction {
  id: string;
  wallet_id: string;
  user_id: string;
  kind: WalletTransactionKind;
  status: WalletTransactionStatus;
  amount: string;
  currency: string;
  external_ref: string | null;
  description: string;
  metadata: Record<string, unknown>;
  posted_at: string;
}

export interface Wallet {
  available_hkd: string;
  reserved_hkd: string;
  currency: string;
  recent_transactions: WalletTransaction[];
}

export interface PaginatedTransactions {
  transactions: WalletTransaction[];
  next_cursor: string | null;
}

export type TopUpSource = "stripe" | "apple_pay" | "google_pay";

export interface TopUpRequest {
  amount_hkd: string;
  source: TopUpSource;
  source_payload: Record<string, unknown>;
}

export interface TopUpResult {
  transaction_id: string;
  status: WalletTransactionStatus;
}

// ---------------------------------------------------------------------------
// Stations
// ---------------------------------------------------------------------------

export type ConnectorType =
  | "ccs2"
  | "type2"
  | "chademo"
  | "tesla"
  | "bs1363"
  | "type1"
  | "gbt_ac"
  | "tesla_nacs"
  | "tesla_wc";
export type SpeedTier = "ac_slow" | "ac_fast" | "dc_fast" | "dc_ultra";
export type StationProviderCode =
  | "hkev"
  | "clp"
  | "shell"
  | "tesla"
  | "epd"
  | "unknown";

export interface Station {
  id: string;
  external_id: string;
  provider_code: StationProviderCode;
  name: string;
  address: string;
  district: string | null;
  latitude: string;
  longitude: string;
  parking_fee_hkd: string;
  amenities: string[];
  distance_km?: number;
  last_synced_at: string;
}

export interface Pole {
  id: string;
  station_id: string;
  external_id: string;
  connector: ConnectorType;
  speed_tier: SpeedTier;
  max_kw: string;
  qr_code: string;
  status: "unknown" | "available" | "charging" | "offline" | "fault";
  status_updated_at: string;
}

export interface HourlyRate {
  id: number;
  pole_id: string;
  day_of_week: number; // 0=Mon ... 6=Sun
  hour_start_local: string; // HH:MM:SS
  price_per_kwh_hkd: string;
  parking_fee_hkd: string;
  valid_from: string;
  valid_to: string | null;
}

export interface StationSearch {
  lat: number;
  lng: number;
  radius_km?: number;
  connector?: ConnectorType;
  min_kw?: number;
  limit?: number;
}

export interface PaginatedStations {
  stations: Station[];
  total: number;
}

export interface StationDetail {
  station: Station;
  poles: Pole[];
  rates_next_24h: HourlyRate[];
}

// ---------------------------------------------------------------------------
// Charging sessions
// ---------------------------------------------------------------------------

export type ChargingSessionStatus =
  | "pending"
  | "active"
  | "completed"
  | "failed"
  | "cancelled";

export interface ChargingSession {
  id: string;
  user_id: string;
  pole_id: string;
  txn_reserve_id: string | null;
  status: ChargingSessionStatus;
  target_soc_pct: number | null;
  started_at: string;
  ended_at: string | null;
  kwh_delivered: string;
  peak_kw: string;
  running_cost_hkd: string;
  preauth_hkd: string;
  settled_hkd: string | null;
  idempotency_key: string;
  metadata: Record<string, unknown>;
}

export interface StartSessionRequest {
  qr_code: string;
  target_soc_pct?: number;
  preauth_hkd?: string;
}

export interface StartSessionResponse {
  session_id: string;
  status: ChargingSessionStatus;
  ws_url: string;
}

export interface SessionEndResult {
  final_cost_hkd: string;
  kwh_delivered: string;
  duration_seconds: number;
}

// ---------------------------------------------------------------------------
// WS telemetry
// ---------------------------------------------------------------------------

export interface TelemetryFrame {
  type: "telemetry";
  ts: string;
  kwh_cumulative: string;
  kw_instant: string;
  soc_pct: number | null;
  cost_hkd_cumulative: string;
  running_total_hkd: string;
}

export interface WsStatusFrame {
  type: "status";
  status: ChargingSessionStatus;
}

export interface WsTargetReachedFrame {
  type: "target_reached";
  soc_pct: number;
}

export interface WsErrorFrame {
  type: "error";
  code: string;
  message: string;
}

export interface WsPingFrame {
  type: "ping";
}

export type ServerFrame =
  | TelemetryFrame
  | WsStatusFrame
  | WsTargetReachedFrame
  | WsErrorFrame
  | WsPingFrame;

export interface ClientPingFrame {
  type: "ping";
}

export interface ClientSetTargetSocFrame {
  type: "set_target_soc";
  soc_pct: number;
}

export interface ClientEndSessionFrame {
  type: "end_session";
}

export type ClientFrame = ClientPingFrame | ClientSetTargetSocFrame | ClientEndSessionFrame;

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  trace_id?: string;
}

export interface ApiErrorBody {
  error: ApiErrorPayload;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly details: Record<string, unknown> | undefined;
  public readonly traceId: string | undefined;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code || "UNKNOWN_ERROR";
    this.details = payload.details;
    this.traceId = payload.trace_id;
  }

  toJSON(): { name: string; status: number; code: string; message: string } {
    return {
      name: this.name,
      status: this.status,
      code: this.code,
      message: this.message,
    };
  }
}

// ---------------------------------------------------------------------------
// Push notifications (extension beyond ARCHITECTURE.md — see final report)
// ---------------------------------------------------------------------------

export interface PushTokenRegistrationRequest {
  token: string;
  platform: "ios" | "android";
  app_version: string;
  device_id: string;
}

export interface PushTokenRegistrationResult {
  ok: boolean;
  registered_at: string;
}

// ---------------------------------------------------------------------------
// Internal: helper types for paginated lists in API responses
// ---------------------------------------------------------------------------

export interface Paginated<T> {
  items: T[];
  next_cursor: string | null;
  total?: number;
}
