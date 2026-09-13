/**
 * Wallet balance display — large HKD figure with reserved sub-line.
 */

import { StyleSheet, Text, View } from "react-native";
import type { Wallet, WalletSummary } from "../lib/types";

export interface BalanceDisplayProps {
  wallet: Wallet | WalletSummary;
  currency?: string;
}

function asSummary(w: Wallet | WalletSummary): WalletSummary {
  if ("available_hkd" in w) return w;
  return w;
}

export function BalanceDisplay({
  wallet,
  currency = "HKD",
}: BalanceDisplayProps): React.JSX.Element {
  const s = asSummary(wallet);
  return (
    <View style={styles.box}>
      <Text style={styles.label}>Available balance</Text>
      <Text style={styles.amount}>
        {s.available_hkd} <Text style={styles.currency}>{currency}</Text>
      </Text>
      <Text style={styles.reserved}>
        Reserved: {s.reserved_hkd} {currency}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: "#0f172a",
    borderRadius: 16,
    padding: 20,
    margin: 12,
  },
  label: {
    fontSize: 13,
    color: "#94a3b8",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  amount: {
    fontSize: 36,
    fontWeight: "700",
    color: "#fff",
    marginTop: 8,
  },
  currency: {
    fontSize: 18,
    fontWeight: "500",
    color: "#cbd5e1",
  },
  reserved: {
    fontSize: 13,
    color: "#94a3b8",
    marginTop: 8,
  },
});
