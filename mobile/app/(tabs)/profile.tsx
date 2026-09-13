/**
 * Profile screen — user info + logout.
 */

import { useRouter } from "expo-router";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useAuth } from "../../lib/auth-context";

export default function ProfileScreen(): React.JSX.Element {
  const { isAuthenticated, loading, user, walletSummary, logout } = useAuth();
  const router = useRouter();

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#0f172a" />
      </View>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <View style={styles.container}>
        <View style={styles.card}>
          <Text style={styles.title}>You're not signed in</Text>
          <Text style={styles.body}>
            Sign in to view your profile, top up your wallet, and start charging sessions.
          </Text>
          <Text style={styles.body}>
            (Auth screens are out of scope for this PR — wire to your IdP provider in
            the console/validate phase.)
          </Text>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>{user.display_name || "EV Wallet user"}</Text>
        {user.email ? <Text style={styles.body}>{user.email}</Text> : null}
        {user.phone_e164 ? <Text style={styles.body}>{user.phone_e164}</Text> : null}
        <Text style={styles.meta}>Locale: {user.locale}</Text>
        {user.is_admin ? <Text style={styles.meta}>Role: admin</Text> : null}
      </View>

      {walletSummary ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Wallet</Text>
          <Text style={styles.body}>Available: {walletSummary.available_hkd} HKD</Text>
          <Text style={styles.body}>Reserved: {walletSummary.reserved_hkd} HKD</Text>
        </View>
      ) : null}

      <Pressable
        style={styles.logoutBtn}
        onPress={async () => {
          await logout();
          router.replace("/(tabs)");
        }}
        accessibilityRole="button"
      >
        <Text style={styles.logoutText}>Log out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f8fafc", padding: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 16,
    marginVertical: 6,
  },
  title: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  body: { color: "#475569", marginTop: 4, fontSize: 14 },
  meta: { color: "#64748b", marginTop: 6, fontSize: 12 },
  sectionTitle: {
    fontSize: 12,
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  logoutBtn: {
    marginTop: 16,
    backgroundColor: "#dc2626",
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: "center",
  },
  logoutText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
