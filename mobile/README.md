# EV Wallet HK — Mobile App

Expo + React Native + TypeScript app for EV Wallet HK (iOS + Android, managed workflow).

## Requirements

- Node.js 20+
- npm 10+
- iOS Simulator (Xcode 15+) and/or Android Emulator, or Expo Go on a physical device

## Install

```bash
cd mobile
npm install --legacy-peer-deps
```

## Configure API base URL

Copy the env example:

```bash
cp .env.example .env
```

Then edit `.env`:

```
EXPO_PUBLIC_API_BASE_URL=http://localhost:8000
```

For a real device pointed at your dev machine, use the LAN IP, e.g.
`http://192.168.1.10:8000`. For staging/prod, use `https://api.evwallet.com.hk`.

## Run

```bash
npx expo start            # opens Metro / QR code
npx expo start --ios      # boots iOS simulator
npx expo start --android  # boots Android emulator
```

If using Expo Go on a phone: scan the QR from the terminal.

## Typecheck

```bash
npx tsc --noEmit
```

## Project layout

```
mobile/
├── app/                    # expo-router routes
│   ├── _layout.tsx         # root: QueryClient + Auth providers, OfflineBanner
│   ├── (tabs)/
│   │   ├── _layout.tsx     # tabs (Map / Wallet / Activity / Profile)
│   │   ├── index.tsx       # Map screen
│   │   ├── wallet.tsx
│   │   ├── activity.tsx
│   │   └── profile.tsx
│   ├── scan.tsx            # QR scanner (expo-camera)
│   └── session/[id].tsx    # Live session monitor (WS)
├── lib/
│   ├── api.ts              # ApiClient — implements contract from ARCHITECTURE.md
│   ├── types.ts            # Shared types (mirror web/lib/types.ts byte-equal)
│   ├── auth.ts             # SecureStore wrapper (JWT)
│   ├── auth-context.tsx    # React context provider + useAuth()
│   ├── ws.ts               # TelemetryClient (WS auto-reconnect with backoff)
│   ├── config.ts           # API_BASE_URL from EXPO_PUBLIC_API_BASE_URL
│   └── _polyfills.ts       # polyfill anchor
├── components/
│   ├── StationCard.tsx
│   ├── SessionCard.tsx
│   ├── TelemetryGauge.tsx
│   ├── BalanceDisplay.tsx
│   └── OfflineBanner.tsx
├── app.config.ts           # Expo config (bundle id, scheme, plugins)
├── package.json
├── tsconfig.json           # strict
├── .env.example
└── README.md
```

## Notes

- All server state uses **@tanstack/react-query**.
- JWT is stored in **expo-secure-store** (Keychain on iOS, Keystore on Android).
- WS auto-reconnects with exponential backoff (1s → 30s cap).
- No fake data; if the backend is down, every screen shows an empty/error state with a retry button.
- The `ApiClient` interface exactly mirrors ARCHITECTURE.md, plus three documented extensions:
  - `registerPushToken()` (push notifications, mobile-only)
  - `getSession(id)` (used by Activity + live monitor)
  - `getSessions(limit)` (used by Activity tab)
