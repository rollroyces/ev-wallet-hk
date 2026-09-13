/**
 * Charging session row — used in the Activity tab.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import type { ChargingSession } from "../lib/types";

export interface SessionCardProps {
  session: ChargingSession;
  onPress?: (s: ChargingSession) => void;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function statusColor(status: ChargingSession["status"]): string {
  switch (status) {
    case "active":
      return "#16a34a";
    case "completed":
      return "#2563eb";
    case "pending":
      return "#a16207";
    case "failed":
      return "#dc2626";
    case "cancelled":
      return "#64748b";
    default:
      return "#64748b";
  }
}

export function SessionCard({ session, onPress }: SessionCardProps): React.JSX.Element {
  return (
    <Pressable
      onPress={() => onPress?.(session)}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
      accessibilityRole="button"
    >
      <View style={styles.row}>
        <Text style={styles.label}>Started</Text>
        <Text style={styles.value}>{fmtDate(session.started_at)}</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Status</Text>
        <Text style={[styles.value, { color: statusColor(session.status) }]}>
          {session.status}
        </Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Energy</Text>
        <Text style={styles.value}>{session.kwh_delivered} kWh</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Cost</Text>
        <Text style={styles.value}>
          {session.settled_hkd ?? session.running_cost_hkd} HKD
        </Text>
      </View>
      {session.ended_at ? (
        <View style={styles.row}>
          <Text style={styles.label}>Ended</Text>
          <Text style={styles.value}>{fmtDate(session.ended_at)}</Text>
        </View>
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
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 3,
  },
  label: {
    fontSize: 13,
    color: "#64748b",
  },
  value: {
    fontSize: 13,
    fontWeight: "600",
    color: "#0f172a",
  },
});
