/**
 * Wallet screen — balance + recent transactions.
 */

import { useQuery } from "@tanstack/react-query";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { api } from "../../lib/api";
import { BalanceDisplay } from "../../components/BalanceDisplay";
import type { WalletTransaction } from "../../lib/types";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function kindColor(kind: WalletTransaction["kind"]): string {
  switch (kind) {
    case "topup":
      return "#16a34a";
    case "charge":
      return "#dc2626";
    case "refund":
      return "#2563eb";
    case "fee":
      return "#a16207";
    default:
      return "#0f172a";
  }
}

function sign(amount: string, kind: WalletTransaction["kind"]): string {
  if (kind === "topup" || kind === "refund") return `+${amount}`;
  return `-${amount}`;
}

export default function WalletScreen(): React.JSX.Element {
  const walletQuery = useQuery({
    queryKey: ["wallet"],
    queryFn: () => api.getWallet(),
  });

  if (walletQuery.isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#0f172a" />
        <Text style={styles.muted}>Loading wallet…</Text>
      </View>
    );
  }

  if (walletQuery.isError || !walletQuery.data) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Couldn't load wallet</Text>
        <Text style={styles.muted}>
          {(walletQuery.error as Error)?.message ?? "Unknown error"}
        </Text>
        <Pressable style={styles.retry} onPress={() => walletQuery.refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const wallet = walletQuery.data;

  return (
    <View style={styles.container}>
      <BalanceDisplay wallet={wallet} currency={wallet.currency} />
      <Text style={styles.sectionTitle}>Recent transactions</Text>
      <FlatList
        data={wallet.recent_transactions}
        keyExtractor={(tx) => tx.id}
        renderItem={({ item }) => (
          <View style={styles.tx}>
            <View>
              <Text style={styles.txKind}>{item.kind}</Text>
              <Text style={styles.txDate}>{fmtDate(item.posted_at)}</Text>
              {item.description ? (
                <Text style={styles.txDesc}>{item.description}</Text>
              ) : null}
            </View>
            <Text style={[styles.txAmount, { color: kindColor(item.kind) }]}>
              {sign(item.amount, item.kind)} {item.currency}
            </Text>
          </View>
        )}
        ListEmptyComponent={
          <Text style={styles.empty}>No transactions yet. Top up to get started.</Text>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f8fafc" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  muted: { color: "#64748b", marginTop: 8 },
  errorTitle: { color: "#0f172a", fontWeight: "700", fontSize: 16 },
  retry: {
    marginTop: 14,
    backgroundColor: "#0f172a",
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 8,
  },
  retryText: { color: "#fff", fontWeight: "600" },
  sectionTitle: {
    fontSize: 13,
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 16,
    marginHorizontal: 16,
    marginBottom: 8,
  },
  tx: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#fff",
    padding: 14,
    marginHorizontal: 12,
    marginVertical: 4,
    borderRadius: 12,
  },
  txKind: {
    fontWeight: "700",
    color: "#0f172a",
    fontSize: 14,
  },
  txDate: {
    color: "#64748b",
    fontSize: 12,
    marginTop: 2,
  },
  txDesc: {
    color: "#475569",
    fontSize: 12,
    marginTop: 2,
  },
  txAmount: {
    fontWeight: "700",
    fontSize: 14,
  },
  empty: {
    color: "#64748b",
    textAlign: "center",
    marginTop: 24,
  },
});
