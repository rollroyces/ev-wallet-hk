/**
 * Root layout — wraps the app with QueryClientProvider + AuthProvider
 * and the offline banner. expo-router's Stack lives inside.
 */

import "../lib/_polyfills";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { useMemo } from "react";
import { AuthProvider } from "../lib/auth-context";
import { OfflineBanner } from "../components/OfflineBanner";

export default function RootLayout(): React.JSX.Element {
  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
    [],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <SafeAreaProvider>
        <AuthProvider>
          <StatusBar style="light" />
          <OfflineBanner />
          <Stack
            screenOptions={{
              headerStyle: { backgroundColor: "#0f172a" },
              headerTintColor: "#fff",
              contentStyle: { backgroundColor: "#f8fafc" },
            }}
          >
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="scan" options={{ title: "Scan QR" }} />
            <Stack.Screen
              name="session/[id]"
              options={{ title: "Live session", headerBackTitle: "Back" }}
            />
          </Stack>
        </AuthProvider>
      </SafeAreaProvider>
    </QueryClientProvider>
  );
}
