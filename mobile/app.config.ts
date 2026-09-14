import type { ExpoConfig } from "expo/config";

/**
 * Expo config for EV Wallet HK mobile app.
 *
 * - Bundle id / package: com.evwallet.hk
 * - Scheme: evwallet (for deep linking to /scan, /session/[id])
 * - iOS + Android both declared; managed workflow.
 * - Plugins: camera (QR), secure-store (token), notifications (push)
 * - API base URL: read from EXPO_PUBLIC_API_BASE_URL at runtime
 */
const config: ExpoConfig = {
  name: "EV Wallet HK",
  slug: "evwallet-hk",
  scheme: "evwallet",
  version: "0.1.0",
  orientation: "portrait",
  // icon: "./assets/icon.png",  // add a 1024x1024 PNG before shipping
  userInterfaceStyle: "automatic",
  newArchEnabled: true,
  splash: {
    // image: "./assets/splash.png",  // add a 1284x2778 PNG before shipping
    resizeMode: "contain",
    backgroundColor: "#0f172a",
  },
  assetBundlePatterns: ["**/*"],
  ios: {
    bundleIdentifier: "com.evwallet.hk",
    supportsTablet: true,
    infoPlist: {
      NSCameraUsageDescription:
        "EV Wallet needs camera access to scan charging station QR codes.",
      NSLocationWhenInUseUsageDescription:
        "EV Wallet uses your location to show nearby charging stations.",
      UIBackgroundModes: ["remote-notification"],
    },
  },
  android: {
    package: "com.evwallet.hk",
    versionCode: 1,
    adaptiveIcon: {
      // foregroundImage: "./assets/icon.png",
      backgroundColor: "#0f172a",
    },
    permissions: [
      "android.permission.CAMERA",
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.ACCESS_COARSE_LOCATION",
      "android.permission.INTERNET",
      "android.permission.POST_NOTIFICATIONS",
    ],
  },
  web: {
    bundler: "metro",
    output: "static",
  },
  plugins: [
    [
      "expo-camera",
      {
        cameraPermission: "Allow EV Wallet to access your camera to scan charging station QR codes.",
      },
    ],
    [
      "expo-secure-store",
      {
        // No native config needed; keychain on iOS, Keystore on Android
      },
    ],
    [
      "expo-notifications",
      {
        // Use FCM/APNs tokens for push notifications
        color: "#0f172a",
      },
    ],
    "expo-router",
    [
      "expo-splash-screen",
      {
        backgroundColor: "#0f172a",
        resizeMode: "contain",
      },
    ],
    // Native payment integrations. Both require EAS Build (managed
    // workflow can't compile these native modules); they won't work in
    // Expo Go. The mobile/app/topup.tsx screen falls back gracefully if
    // the modules aren't available.
    "@stripe/stripe-react-native",
    "expo-apple-pay",
    "expo-google-pay",
  ],
  experiments: {
    typedRoutes: true,
    tsconfigPaths: true,
  },
  extra: {
    router: {
      origin: false,
    },
    eas: {
      projectId: "00000000-0000-0000-0000-000000000000",
    },
  },
};

export default config;
