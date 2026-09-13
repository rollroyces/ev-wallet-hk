/**
 * Activity screen — past charging sessions.
 */

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { api } from "../../lib/api";
import { SessionCard } from "../../components/SessionCard";

export default function ActivityScreen(): React.JSX.Element {
  const router = useRouter();
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.getSessions(50),
  });

  if (sessionsQuery.isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#0f172a" />
        <Text style={styles.muted}>Loading sessions…</Text>
      </View>
    );
  }

  if (sessionsQuery.isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Couldn't load sessions</Text>
        <Text style={styles.muted}>
          {(sessionsQuery.error as Error)?.message ?? "Unknown error"}
        </Text>
        <Pressable style={styles.retry} onPress={() => sessionsQuery.refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const sessions = sessionsQuery.data ?? [];

  return (
    <View style={styles.container}>
      <FlatList
        data={sessions}
        keyExtractor={(s) => s.id}
        renderItem={({ item }) => (
          <SessionCard
            session={item}
            onPress={(s) => router.push(`/session/${s.id}`)}
          />
        )}
        ListEmptyComponent={
          <View style={styles.emptyBox}>
            <Text style={styles.emptyTitle}>No charging sessions yet</Text>
            <Text style={styles.emptyBody}>
              Tap the map tab and scan a station QR to start your first session.
            </Text>
          </View>
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
  emptyBox: {
    alignItems: "center",
    marginTop: 60,
    paddingHorizontal: 24,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0f172a",
  },
  emptyBody: {
    color: "#64748b",
    marginTop: 6,
    textAlign: "center",
  },
});
