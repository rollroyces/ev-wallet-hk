/**
 * Telemetry gauge — used in the live session monitor.
 *
 * Shows a labeled numeric value with a unit. Used for kW instant,
 * kWh cumulative, SOC%, and cost (HKD).
 */

import { StyleSheet, Text, View } from "react-native";

export interface TelemetryGaugeProps {
  label: string;
  value: string;
  unit?: string;
  accent?: string;
}

export function TelemetryGauge({
  label,
  value,
  unit,
  accent = "#0f172a",
}: TelemetryGaugeProps): React.JSX.Element {
  return (
    <View style={styles.box}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.valueRow}>
        <Text style={[styles.value, { color: accent }]}>{value}</Text>
        {unit ? <Text style={styles.unit}>{unit}</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: "#ffffff",
    borderRadius: 12,
    padding: 14,
    flex: 1,
    minWidth: 140,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  label: {
    fontSize: 12,
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  valueRow: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  value: {
    fontSize: 24,
    fontWeight: "700",
  },
  unit: {
    fontSize: 13,
    color: "#64748b",
    marginLeft: 4,
  },
});
