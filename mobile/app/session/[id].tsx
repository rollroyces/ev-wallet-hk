/**
 * Live session monitor — subscribes to the WS telemetry stream.
 *
 * - Connects to /api/v1/charging/sessions/{id}/stream using the JWT.
 * - Renders the latest telemetry frame (kWh, kW, SOC%, cost HKD).
 * - Buttons to set target SOC and end session.
 * - Auto-reconnects with backoff (handled by lib/ws.ts TelemetryClient).
 */

import { useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { api } from "../../lib/api";
import { TelemetryClient, type WsConnectionStatus } from "../../lib/ws";
import { TelemetryGauge } from "../../components/TelemetryGauge";
import type {
  ChargingSession,
  ServerFrame,
  TelemetryFrame,
} from "../../lib/types";

interface LiveState {
  session: ChargingSession | null;
  latest: TelemetryFrame | null;
  status: ChargingSession["status"] | null;
  errorMsg: string | null;
  targetReachedPct: number | null;
}

const INITIAL: LiveState = {
  session: null,
  latest: null,
  status: null,
  errorMsg: null,
  targetReachedPct: null,
};

export default function SessionMonitorScreen(): React.JSX.Element {
  const params = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const id = typeof params.id === "string" ? params.id : "";

  const [state, setState] = useState<LiveState>(INITIAL);
  const [wsStatus, setWsStatus] = useState<WsConnectionStatus>("idle");
  const [targetInput, setTargetInput] = useState("");
  const [ending, setEnding] = useState(false);
  const clientRef = useRef<TelemetryClient | null>(null);

  // Load session detail + connect WS
  useEffect(() => {
    if (!id) return;

    let cancelled = false;

    void (async () => {
      try {
        const session = await api.getSession(id);
        if (cancelled) return;
        setState((s) => ({ ...s, session, status: session.status }));
      } catch (e) {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          errorMsg: e instanceof Error ? e.message : String(e),
        }));
      }
    })();

    const client = new TelemetryClient({
      sessionId: id,
      onFrame: (frame: ServerFrame) => {
        setState((s) => {
          switch (frame.type) {
            case "telemetry":
              return { ...s, latest: frame };
            case "status":
              return { ...s, status: frame.status };
            case "target_reached":
              return { ...s, targetReachedPct: frame.soc_pct };
            case "error":
              return { ...s, errorMsg: `${frame.code}: ${frame.message}` };
            case "ping":
            default:
              return s;
          }
        });
      },
      onStatus: setWsStatus,
      onError: (err) =>
        setState((s) => ({ ...s, errorMsg: err.message })),
    });

    clientRef.current = client;
    void client.connect().catch(() => undefined);

    return () => {
      cancelled = true;
      client.close();
      clientRef.current = null;
    };
  }, [id]);

  const handleSetTarget = useCallback(() => {
    const pct = parseInt(targetInput, 10);
    if (!isFinite(pct) || pct < 0 || pct > 100) {
      Alert.alert("Invalid target", "Enter a SOC percentage between 0 and 100.");
      return;
    }
    clientRef.current?.setTargetSoc(pct);
    setTargetInput("");
  }, [targetInput]);

  const handleEnd = useCallback(() => {
    Alert.alert("End session?", "This will stop charging and settle your wallet.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "End session",
        style: "destructive",
        onPress: async () => {
          setEnding(true);
          try {
            // Tell WS first so the server can settle
            clientRef.current?.endSession();
            const result = await api.endSession(id);
            Alert.alert(
              "Session ended",
              `Cost: ${result.final_cost_hkd} HKD\nEnergy: ${result.kwh_delivered} kWh\nDuration: ${result.duration_seconds}s`,
              [{ text: "OK", onPress: () => router.replace("/(tabs)/activity") }],
            );
          } catch (e) {
            Alert.alert(
              "Couldn't end session",
              e instanceof Error ? e.message : String(e),
            );
          } finally {
            setEnding(false);
          }
        },
      },
    ]);
  }, [id, router]);

  if (!id) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>No session id</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 32 }}>
      <View style={styles.statusRow}>
        <Text style={styles.wsLabel}>WS:</Text>
        <Text style={[styles.wsValue, { color: wsColor(wsStatus) }]}>{wsStatus}</Text>
        {state.status ? (
          <>
            <Text style={styles.wsLabel}>Session:</Text>
            <Text style={[styles.wsValue, { color: statusColor(state.status) }]}>
              {state.status}
            </Text>
          </>
        ) : null}
      </View>

      <View style={styles.grid}>
        <TelemetryGauge
          label="Energy"
          value={state.latest?.kwh_cumulative ?? "0.000"}
          unit="kWh"
        />
        <TelemetryGauge
          label="Power"
          value={state.latest?.kw_instant ?? "0.0"}
          unit="kW"
          accent="#16a34a"
        />
        <TelemetryGauge
          label="Battery"
          value={state.latest?.soc_pct != null ? String(state.latest.soc_pct) : "—"}
          unit="%"
          accent="#2563eb"
        />
        <TelemetryGauge
          label="Cost"
          value={state.latest?.running_total_hkd ?? "0.00"}
          unit="HKD"
          accent="#a16207"
        />
      </View>

      {state.targetReachedPct != null ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>
            ✅ Target reached: {state.targetReachedPct}% SOC
          </Text>
        </View>
      ) : null}

      {state.errorMsg ? (
        <View style={[styles.banner, styles.bannerErr]}>
          <Text style={styles.bannerText}>{state.errorMsg}</Text>
        </View>
      ) : null}

      <View style={styles.actions}>
        <Text style={styles.sectionTitle}>Set target SOC</Text>
        <View style={styles.row}>
          <TextInput
            value={targetInput}
            onChangeText={setTargetInput}
            placeholder="e.g. 80"
            placeholderTextColor="#94a3b8"
            keyboardType="number-pad"
            style={styles.input}
            maxLength={3}
          />
          <Pressable style={styles.primaryBtn} onPress={handleSetTarget}>
            <Text style={styles.primaryBtnText}>Set</Text>
          </Pressable>
        </View>

        <Text style={styles.sectionTitle}>End session</Text>
        <Pressable
          style={[styles.dangerBtn, ending && styles.btnDisabled]}
          onPress={handleEnd}
          disabled={ending}
        >
          {ending ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.dangerBtnText}>End charging</Text>
          )}
        </Pressable>
      </View>
    </ScrollView>
  );
}

function wsColor(s: WsConnectionStatus): string {
  switch (s) {
    case "open":
      return "#16a34a";
    case "connecting":
    case "reconnecting":
      return "#a16207";
    case "closed":
    case "failed":
      return "#dc2626";
    default:
      return "#64748b";
  }
}

function statusColor(s: ChargingSession["status"]): string {
  switch (s) {
    case "active":
      return "#16a34a";
    case "completed":
      return "#2563eb";
    case "failed":
      return "#dc2626";
    case "cancelled":
      return "#64748b";
    case "pending":
    default:
      return "#a16207";
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f8fafc" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  errorTitle: { color: "#dc2626", fontWeight: "700" },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    backgroundColor: "#fff",
  },
  wsLabel: { fontSize: 12, color: "#64748b", textTransform: "uppercase" },
  wsValue: { fontSize: 12, fontWeight: "700", marginRight: 12 },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    paddingHorizontal: 8,
    marginTop: 8,
  },
  banner: {
    backgroundColor: "#dcfce7",
    padding: 12,
    marginHorizontal: 12,
    marginTop: 12,
    borderRadius: 10,
  },
  bannerErr: {
    backgroundColor: "#fee2e2",
  },
  bannerText: {
    color: "#0f172a",
    fontWeight: "600",
  },
  actions: {
    padding: 12,
    marginTop: 16,
  },
  sectionTitle: {
    fontSize: 12,
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#cbd5e1",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: "#0f172a",
    backgroundColor: "#fff",
  },
  primaryBtn: {
    backgroundColor: "#2563eb",
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "700" },
  dangerBtn: {
    backgroundColor: "#dc2626",
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: "center",
  },
  dangerBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  btnDisabled: { opacity: 0.5 },
});
