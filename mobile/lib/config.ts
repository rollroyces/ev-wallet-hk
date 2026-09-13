/**
 * Runtime config for the EV Wallet HK mobile app.
 *
 * API_BASE_URL is the base URL of the EV Wallet backend (no trailing slash).
 * Defaults to localhost:8000 for development; override via the EXPO_PUBLIC_API_BASE_URL
 * environment variable in `.env` or the build command.
 */

const RAW_BASE =
  (typeof process !== "undefined" && process.env && process.env.EXPO_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export const API_BASE_URL: string = RAW_BASE.replace(/\/+$/, "");

export const WS_BASE_URL: string = API_BASE_URL.replace(/^http/, "ws");

export const API_TIMEOUT_MS: number = 15000;

export const APP_NAME: string = "EV Wallet HK";

export const APP_VERSION: string = "0.1.0";
