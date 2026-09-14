/**
 * Deep-link helpers for Apple Maps / Google Maps.
 *
 * Used by both the mobile app and the web portal's "Navigate" buttons.
 * Platform-aware: iOS → Apple Maps URL scheme; Android → geo: URI with
 * Google Maps fallback; web (and any other) → universal Google Maps URL.
 *
 * Both Apple and Google URL schemes are stable and documented by their
 * respective platforms; no app-side entitlements required beyond
 * `LSApplicationQueriesSchemes` for iOS (we only use the public URL
 * schemes below, which don't require it for opening, only for
 * checking `canOpenURL`).
 */

import { Platform } from "react-native";

export interface NavTarget {
  latitude: number;
  longitude: number;
  /** Optional name/title for the destination (e.g. "Shell Recharge — Central"). */
  name?: string;
}

/**
 * Returns the URL that, when opened, will hand the user off to their
 * preferred maps application with turn-by-turn directions to ``target``.
 *
 * On iOS: Apple Maps (`maps://`) with driving directions.
 * On Android: `geo:` URI (chooser lets the user pick); falls back to the
 *     Google Maps web URL if `geo:` isn't registered.
 * On web: universal Google Maps URL that works on any device.
 */
export function mapsNavigationUrl(target: NavTarget): string {
  const lat = target.latitude;
  const lng = target.longitude;
  const label = (target.name ?? "Charging station")
    .replace(/[\\n\\r]/g, " ")
    .slice(0, 120);

  if (Platform.OS === "ios") {
    // Apple Maps URL scheme — opens the native Maps app with directions.
    // See: https://developer.apple.com/library/ios/featuredarticles/iPhoneURLScheme_Reference/MapLinks/MapLinks.html
    const params = new URLSearchParams({
      daddr: `${lat},${lng}`,
      q: label,
    });
    return `maps://?${params.toString()}`;
  }

  if (Platform.OS === "android") {
    // Android `geo:` URI — Android system will present a chooser if both
    // Google Maps and another maps app are installed. The `mode=d` requests
    // driving directions.
    // See: https://developer.android.com/guide/components/intents-common#Maps
    const q = label.replace(/[,()]/g, " ");
    return `geo:${lat},${lng}?q=${lat},${lng}(${encodeURIComponent(q)})&mode=d`;
  }

  // Web — universal Google Maps URL that works on iOS Safari, Android Chrome,
  // and desktop. `data=` forces directions mode; `travelmode=driving` sets
  // the mode. Apple devices with Google Maps installed will be deep-linked
  // automatically.
  const params = new URLSearchParams({
    api: "1",
    destination: `${lat},${lng}`,
    travelmode: "driving",
  });
  if (label) params.set("destination_place_id", label);
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

/**
 * Returns `true` if the device has a maps app that can open deep-links.
 * Best-effort — returns `null` if we can't tell (web), `false` if the
 * device is iOS but no Apple Maps (very unlikely on real iOS), `true` on
 * Android (we assume at least one maps app is present).
 *
 * Use this only for UI hints; don't gate functionality on it.
 */
export async function canOpenMaps(): Promise<boolean | null> {
  if (Platform.OS === "web") return null;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { Linking } = require("react-native");
  try {
    if (Platform.OS === "android") return true;
    return await Linking.canOpenURL("maps://?");
  } catch {
    return false;
  }
}