/**
 * Topup screen — Modal screen opened from the Wallet tab.
 *
 * Lets the user pick an amount and pay via:
 *   - Apple Pay (iOS, via `POST /wallet/topup/apple/intent` for merchant
 *     config; PKPaymentToken capture is delegated to expo-apple-pay which
 *     is NOT yet installed — when it lands, swap in the real sheet)
 *   - Google Pay (Android, same caveat)
 *   - Stripe (test mode in dev — confirms via the client_secret and
 *     settles via the webhook)
 *
 * The "intent" endpoints return 503 `BackendUnavailableError` if the
 * backend isn't configured (no Apple Pay merchant id, no Stripe key);
 * the UI handles that gracefully by showing a friendly error and letting
 * the user pick another method.
 */

import { useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { api } from "../lib/api";

const PRESET_AMOUNTS = ["100", "200", "500", "1000"]; // HKD

type TopupMethod = "apple_pay" | "google_pay" | "stripe";

export default function TopupScreen(): React.JSX.Element {
  const router = useRouter();
  const [amount, setAmount] = useState<string>("200");
  const [submitting, setSubmitting] = useState<TopupMethod | null>(null);
  const [result, setResult] = useState<{ kind: "ok" | "fail"; message: string } | null>(null);

  const numericAmount = Number(amount);
  const valid = Number.isFinite(numericAmount) && numericAmount >= 50 && numericAmount <= 10000;

  const onApplePay = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    setSubmitting("apple_pay");
    setResult(null);
    try {
      await api.createApplePayIntent();
      // expo-apple-pay integration is not in the MVP. Until it's wired
      // up, just tell the user what would have happened.
      setResult({
        kind: "ok",
        message:
          "Apple Pay merchant verified. Native PKPaymentToken capture requires expo-apple-pay (not yet installed); use Stripe in the meantime.",
      });
    } catch (e) {
      setResult({ kind: "fail", message: String(e instanceof Error ? e.message : e) });
    } finally {
      setSubmitting(null);
    }
  };

  const onGooglePay = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    setSubmitting("google_pay");
    setResult(null);
    try {
      // Google Pay has no server-side intent; we POST the payload directly
      // to /wallet/topup once the token is captured. For now, surface that
      // we don't have expo-google-pay wired in.
      setResult({
        kind: "ok",
        message:
          "Google Pay requires expo-google-pay (not yet installed); use Stripe in the meantime.",
      });
    } catch (e) {
      setResult({ kind: "fail", message: String(e instanceof Error ? e.message : e) });
    } finally {
      setSubmitting(null);
    }
  };

  const onStripe = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    setSubmitting("stripe");
    setResult(null);
    try {
      const intent = await api.createStripeIntent(amount);
      // The MVP topup screen does NOT yet embed Stripe.js / the native
      // sheet — that's a follow-up. We surface the intent id so the user
      // (and dev) can verify the server round-trip works end-to-end.
      setResult({
        kind: "ok",
        message:
          `Stripe PaymentIntent created (id=${intent.payment_intent_id}). ` +
          "Client-side card collection needs Stripe.js (web) or stripe-react-native (mobile); wire in next pass.",
      });
    } catch (e) {
      setResult({ kind: "fail", message: String(e instanceof Error ? e.message : e) });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} style={styles.close}>
          <Text style={styles.closeText}>Close</Text>
        </Pressable>
        <Text style={styles.title}>Top up wallet</Text>
        <View style={{ width: 50 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.sectionLabel}>Amount (HKD)</Text>
        <View style={styles.amountRow}>
          <TextInput
            style={styles.amountInput}
            value={amount}
            onChangeText={setAmount}
            keyboardType="decimal-pad"
            placeholder="200"
            placeholderTextColor="#94a3b8"
          />
          <Text style={styles.currency}>HKD</Text>
        </View>

        <View style={styles.presetRow}>
          {PRESET_AMOUNTS.map((preset) => (
            <Pressable
              key={preset}
              style={[styles.preset, amount === preset && styles.presetActive]}
              onPress={() => setAmount(preset)}
            >
              <Text style={[styles.presetText, amount === preset && styles.presetTextActive]}>
                HK${preset}
              </Text>
            </Pressable>
          ))}
        </View>

        <Text style={styles.sectionLabel}>Payment method</Text>

        {Platform.OS === "ios" ? (
          <PayButton label="Apple Pay" note="Recommended on iOS" onPress={onApplePay} loading={submitting === "apple_pay"} />
        ) : null}
        {Platform.OS === "android" ? (
          <PayButton label="Google Pay" note="Recommended on Android" onPress={onGooglePay} loading={submitting === "google_pay"} />
        ) : null}
        <PayButton label="Credit / debit card" note="Powered by Stripe" onPress={onStripe} loading={submitting === "stripe"} />

        {result ? (
          <View style={[styles.result, result.kind === "ok" ? styles.resultOk : styles.resultFail]}>
            <Text style={styles.resultText}>{result.message}</Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

interface PayButtonProps {
  label: string;
  note?: string;
  onPress: () => void;
  loading?: boolean;
}

function PayButton({ label, note, onPress, loading }: PayButtonProps): React.JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [styles.payBtn, pressed && styles.payBtnPressed]}
      onPress={onPress}
      disabled={loading}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      <View style={{ flex: 1 }}>
        <Text style={styles.payBtnLabel}>{label}</Text>
        {note ? <Text style={styles.payBtnNote}>{note}</Text> : null}
      </View>
      {loading ? <ActivityIndicator color="#fff" /> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f8fafc" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 14,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#e2e8f0",
  },
  close: { paddingVertical: 6, paddingHorizontal: 10 },
  closeText: { color: "#0f172a", fontWeight: "600", fontSize: 14 },
  title: { fontSize: 16, fontWeight: "700", color: "#0f172a" },
  content: { padding: 20 },
  sectionLabel: {
    color: "#64748b",
    fontSize: 13,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 16,
    marginBottom: 8,
  },
  amountRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  amountInput: {
    flex: 1,
    fontSize: 28,
    fontWeight: "700",
    color: "#0f172a",
    paddingVertical: 0,
  },
  currency: { fontSize: 16, color: "#64748b", fontWeight: "600" },
  presetRow: { flexDirection: "row", flexWrap: "wrap", marginTop: 10, gap: 8 },
  preset: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: "#fff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#e2e8f0",
  },
  presetActive: { backgroundColor: "#0f172a", borderColor: "#0f172a" },
  presetText: { color: "#0f172a", fontWeight: "600", fontSize: 14 },
  presetTextActive: { color: "#fff" },
  payBtn: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#0f172a",
    borderRadius: 12,
    paddingHorizontal: 18,
    paddingVertical: 16,
    marginTop: 10,
  },
  payBtnPressed: { opacity: 0.7 },
  payBtnLabel: { color: "#fff", fontWeight: "700", fontSize: 15 },
  payBtnNote: { color: "#94a3b8", fontSize: 12, marginTop: 2 },
  result: {
    marginTop: 16,
    borderRadius: 10,
    padding: 14,
  },
  resultOk: { backgroundColor: "#dcfce7" },
  resultFail: { backgroundColor: "#fee2e2" },
  resultText: { color: "#0f172a", fontSize: 13 },
});