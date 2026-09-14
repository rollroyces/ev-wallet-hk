/**
 * Reusable station card — used in search results and "nearby" lists.
 */

import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import type { Station } from "../lib/types";
import { mapsNavigationUrl } from "../lib/navigation";

export interface StationCardProps {
  station: Station;
  onPress?: (s: Station) => void;
  /** When true, shows a "Navigate" button that opens the platform's
   * maps app with turn-by-turn directions. Defaults to true on mobile,
   * hidden on web (where it would just be a static link). */
  showNavigate?: boolean;
}

export function StationCard({
  station,
  onPress,
  showNavigate = true,
}: StationCardProps): React.JSX.Element {
  const openNavigate = () => {
    void Linking.openURL(
      mapsNavigationUrl({
        latitude: Number(station.latitude),
        longitude: Number(station.longitude),
        name: station.name,
      }),
    );
  };

  return (
    <Pressable
      onPress={() => onPress?.(station)}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
      accessibilityRole="button"
      accessibilityLabel={`${station.name}, ${station.address}`}
    >
      <View style={styles.header}>
        <Text style={styles.name} numberOfLines={1}>
          {station.name}
        </Text>
        <Text style={styles.provider}>{station.provider_code.toUpperCase()}</Text>
      </View>
      <Text style={styles.address} numberOfLines={2}>
        {station.address}
      </Text>
      <View style={styles.meta}>
        {typeof station.distance_km === "number" ? (
          <Text style={styles.metaText}>{station.distance_km.toFixed(1)} km away</Text>
        ) : null}
        {station.district ? (
          <Text style={styles.metaText}>· {station.district}</Text>
        ) : null}
        {station.amenities.length > 0 ? (
          <Text style={styles.metaText}>· {station.amenities.slice(0, 2).join(", ")}</Text>
        ) : null}
      </View>
      {showNavigate ? (
        <Pressable
          style={styles.navigateBtn}
          onPress={(e) => {
            e.stopPropagation?.();
            openNavigate();
          }}
          accessibilityRole="button"
          accessibilityLabel={`Navigate to ${station.name}`}
        >
          <Text style={styles.navigateBtnText}>Navigate</Text>
        </Pressable>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 12,
    padding: 14,
    marginVertical: 6,
    marginHorizontal: 12,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  pressed: {
    opacity: 0.7,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  name: {
    fontSize: 16,
    fontWeight: "600",
    color: "#0f172a",
    flex: 1,
    marginRight: 8,
  },
  provider: {
    fontSize: 11,
    color: "#64748b",
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  address: {
    fontSize: 13,
    color: "#475569",
    marginTop: 4,
  },
  meta: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginTop: 8,
  },
  metaText: {
    fontSize: 12,
    color: "#64748b",
    marginRight: 6,
  },
  navigateBtn: {
    alignSelf: "flex-start",
    marginTop: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: "#0f172a",
    borderRadius: 8,
  },
  navigateBtnText: {
    color: "#fff",
    fontSize: 13,
    fontWeight: "600",
  },
});
