/**
 * Map screen — shows nearby charging stations.
 *
 * - Fetches /stations via api.getStations() using a default HK center
 *   (you can swap to device GPS via expo-location if needed).
 * - Falls back to a graceful empty state with retry when the API is down.
 * - Tap a marker -> opens a Station detail modal/sheet.
 */

import * as Location from "expo-location";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import MapView, { Marker, PROVIDER_GOOGLE, type Region } from "react-native-maps";
import { api } from "../../lib/api";
import type { Station } from "../../lib/types";
import { StationCard } from "../../components/StationCard";

// HK Central as a sensible default; replaced by real GPS when the user
// grants location permission (see the effect below).
const DEFAULT_REGION: Region = {
  latitude: 22.302711,
  longitude: 114.177216,
  latitudeDelta: 0.08,
  longitudeDelta: 0.08,
};

// HK bounds — used as a sanity check on the GPS reading (sometimes the
// emulator returns a default coordinate in California). If the fix is
// outside this box, treat the read as untrusted and fall back.
const HK_BBOX = {
  minLat: 22.15,
  maxLat: 22.55,
  minLng: 113.85,
  maxLng: 114.45,
};

function isInHKBbox(lat: number, lng: number): boolean {
  return (
    lat >= HK_BBOX.minLat &&
    lat <= HK_BBOX.maxLat &&
    lng >= HK_BBOX.minLng &&
    lng <= HK_BBOX.maxLng
  );
}

export default function MapScreen(): React.JSX.Element {
  const router = useRouter();
  const mapRef = useRef<MapView | null>(null);
  const [region, setRegion] = useState<Region>(DEFAULT_REGION);
  const [selected, setSelected] = useState<Station | null>(null);
  const [locating, setLocating] = useState(false);
  const [locStatus, setLocStatus] = useState<"unknown" | "granted" | "denied">("unknown");

  // Ask for location permission on mount (background: false is enough for
  // a "show me nearby" feature). If granted, pan to the user's current fix.
  // Implemented as a manual handler rather than useEffect so the user can
  // also tap the toolbar button to retry.
  const locateMe = async () => {
    setLocating(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") {
        setLocStatus("denied");
        Alert.alert(
          "Location access denied",
          "EV Wallet can still show you HK-wide stations; grant location in Settings for nearby results.",
        );
        return;
      }
      setLocStatus("granted");
      const fix = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      const { latitude, longitude } = fix.coords;
      if (!isInHKBbox(latitude, longitude)) {
        Alert.alert(
          "GPS returned an out-of-HK fix",
          "Using HK Central as the default. (Are you on a simulator with default Mountain View?)",
        );
        return;
      }
      const next: Region = {
        latitude,
        longitude,
        latitudeDelta: 0.08,
        longitudeDelta: 0.08,
      };
      setRegion(next);
      mapRef.current?.animateToRegion(next, 600);
    } catch (e) {
      Alert.alert("Couldn't get location", e instanceof Error ? e.message : String(e));
    } finally {
      setLocating(false);
    }
  };

  const stationsQuery = useQuery({
    queryKey: [
      "stations",
      region.latitude,
      region.longitude,
      Math.round(region.latitudeDelta * 111),
    ],
    queryFn: () =>
      api.getStations({
        lat: region.latitude,
        lng: region.longitude,
        radius_km: Math.max(2, Math.round(region.latitudeDelta * 111)),
        limit: 50,
      }),
  });

  const stations = useMemo(() => stationsQuery.data?.stations ?? [], [stationsQuery.data]);

  return (
    <View style={styles.container}>
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={Platform.OS === "android" ? PROVIDER_GOOGLE : undefined}
        initialRegion={DEFAULT_REGION}
        onRegionChangeComplete={setRegion}
      >
        {stations.map((s) => {
          const lat = Number(s.latitude);
          const lng = Number(s.longitude);
          if (!isFinite(lat) || !isFinite(lng)) return null;
          return (
            <Marker
              key={s.id}
              coordinate={{ latitude: lat, longitude: lng }}
              title={s.name}
              description={s.address}
              onPress={() => setSelected(s)}
            />
          );
        })}
      </MapView>

      <View style={styles.toolbar}>
        <Pressable
          style={[styles.toolbarBtn, locStatus === "granted" && styles.toolbarBtnActive]}
          onPress={() => void locateMe()}
          disabled={locating}
          accessibilityRole="button"
          accessibilityLabel="Use my location"
        >
          {locating ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.toolbarBtnText}>📍 My location</Text>
          )}
        </Pressable>
        <Pressable
          style={[styles.toolbarBtn, styles.scanBtn]}
          onPress={() => router.push("/scan")}
          accessibilityRole="button"
        >
          <Text style={styles.toolbarBtnText}>📷 Scan QR</Text>
        </Pressable>
      </View>

      {stationsQuery.isLoading ? (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator color="#0f172a" />
          <Text style={styles.loadingText}>Finding nearby stations…</Text>
        </View>
      ) : null}

      {stationsQuery.isError ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>Couldn't reach the server</Text>
          <Text style={styles.emptyBody}>
            Check your connection and try again. ({(stationsQuery.error as Error)?.message})
          </Text>
          <Pressable style={styles.retry} onPress={() => stationsQuery.refetch()}>
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      ) : null}

      {!stationsQuery.isLoading && !stationsQuery.isError && stations.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No stations in this area</Text>
          <Text style={styles.emptyBody}>Pan or zoom out to see more.</Text>
        </View>
      ) : null}

      <Modal
        visible={selected !== null}
        transparent
        animationType="slide"
        onRequestClose={() => setSelected(null)}
      >
        <Pressable style={styles.modalBackdrop} onPress={() => setSelected(null)}>
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <ScrollView>
              {selected ? (
                <View>
                  <Text style={styles.sheetTitle}>{selected.name}</Text>
                  <Text style={styles.sheetAddr}>{selected.address}</Text>
                  <Text style={styles.sheetMeta}>
                    Provider: {selected.provider_code.toUpperCase()}
                  </Text>
                  <Text style={styles.sheetMeta}>Parking fee: {selected.parking_fee_hkd} HKD</Text>
                  {selected.amenities.length > 0 ? (
                    <Text style={styles.sheetMeta}>
                      Amenities: {selected.amenities.join(", ")}
                    </Text>
                  ) : null}
                  <View style={{ height: 12 }} />
                  <StationCard station={selected} />
                  <Pressable
                    style={styles.closeBtn}
                    onPress={() => setSelected(null)}
                  >
                    <Text style={styles.closeBtnText}>Close</Text>
                  </Pressable>
                </View>
              ) : null}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f8fafc" },
  map: { flex: 1 },
  toolbar: {
    position: "absolute",
    top: 16,
    right: 16,
    gap: 8,
  },
  toolbarBtn: {
    backgroundColor: "#0f172a",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 24,
    shadowColor: "#000",
    shadowOpacity: 0.2,
    shadowRadius: 6,
    elevation: 4,
  },
  toolbarBtnActive: {
    backgroundColor: "#22d3ee",
  },
  toolbarBtnText: {
    color: "#fff",
    fontWeight: "700",
    fontSize: 14,
  },
  scanBtn: {
    // kept as a styling hook so the layout above reads naturally.
  },
  loadingOverlay: {
    position: "absolute",
    bottom: 16,
    left: 16,
    right: 16,
    backgroundColor: "#fff",
    padding: 14,
    borderRadius: 12,
    flexDirection: "row",
    alignItems: "center",
    shadowColor: "#000",
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  loadingText: {
    marginLeft: 12,
    color: "#0f172a",
  },
  empty: {
    position: "absolute",
    bottom: 16,
    left: 16,
    right: 16,
    backgroundColor: "#fff",
    padding: 14,
    borderRadius: 12,
    alignItems: "center",
  },
  emptyTitle: {
    fontWeight: "700",
    color: "#0f172a",
    fontSize: 15,
  },
  emptyBody: {
    color: "#475569",
    marginTop: 4,
    fontSize: 13,
    textAlign: "center",
  },
  retry: {
    marginTop: 10,
    backgroundColor: "#0f172a",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
  },
  retryText: {
    color: "#fff",
    fontWeight: "600",
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    maxHeight: "70%",
  },
  sheetTitle: {
    fontSize: 20,
    fontWeight: "700",
    color: "#0f172a",
  },
  sheetAddr: {
    fontSize: 14,
    color: "#475569",
    marginTop: 4,
  },
  sheetMeta: {
    fontSize: 13,
    color: "#64748b",
    marginTop: 4,
  },
  closeBtn: {
    marginTop: 12,
    backgroundColor: "#e2e8f0",
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: "center",
  },
  closeBtnText: {
    color: "#0f172a",
    fontWeight: "600",
  },
});
