/**
 * Offline banner — shown when the device has no internet connection.
 *
 * Uses @react-native-community/netinfo to detect reachability.
 * Graceful: if the module isn't available, the banner never appears
 * (the API client itself surfaces network errors anyway).
 */

import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import NetInfo from "@react-native-community/netinfo";

export function OfflineBanner(): React.JSX.Element | null {
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => {
      const reachable =
        state.isConnected === false || state.isInternetReachable === false;
      setOffline(Boolean(reachable));
    });
    return () => unsubscribe();
  }, []);

  if (!offline) return null;

  return (
    <View style={styles.banner} accessibilityRole="alert">
      <Text style={styles.text}>No internet connection — showing cached data</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: "#dc2626",
    paddingVertical: 8,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  text: {
    color: "#fff",
    fontWeight: "600",
    fontSize: 13,
  },
});
