/**
 * Topup screen — Modal opened from the Wallet tab.
 *
 * Three payment methods, each with a fallback when the native module
 * isn't compiled in (Expo Go or web):
 *
 *   - Apple Pay (iOS) via expo-apple-pay. PKPaymentToken is generated
 *     client-side, validated server-side via POST /wallet/topup?source=apple_pay.
 *   - Google Pay (Android) via expo-google-pay. Same pattern.
 *   - Credit/debit card via @stripe/stripe-react-native. Server creates a
 *     PaymentIntent, client confirms with stripe.confirmPayment().
 *
 * The screen falls back to a friendly error message if any of the native
 * modules are missing — that way the screen is testable in Expo Go (where
 * these libs don't compile) and graceful when an integration isn't yet
 * configured server-side.
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

// Lazy-require the native modules so the screen still loads in Expo Go
// (where these aren't compiled). `require` is wrapped in try/catch so a
// missing module falls through gracefully.
function tryRequire<T = unknown>(name: string): T | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require(name) as T;
  } catch {
    return null;
  }
}

const PRESET_AMOUNTS = ["100", "200", "500", "1000"]; // HKD

type TopupMethod = "apple_pay" | "google_pay" | "stripe";

interface ApplePayApi {
  canMakePaymentsAsync?: () => Promise<boolean>;
  presentApplePayAsync: (opts: {
    cartItems: Array<{
      label: string;
      amount: string;
      paymentType?: "pending" | "final-on-payment";
    }>;
    country: string;
    currency: string;
    merchantIdentifier: string;
    requiredBillingContactFields?: string[];
    requiredShippingContactFields?: string[];
  }) => Promise<{ token: unknown; status: number }>;
}

const ApplePay: ApplePayApi | null = tryRequire<ApplePayApi>("expo-apple-pay");

interface GooglePayApi {
  GooglePayStatus: { AVAILABLE: number };
  isReadyToPay: (opts: {
    apiVersion: number;
    apiVersionMinor: number;
    allowedPaymentMethods: Array<{
      type: string;
      parameters: Record<string, unknown>;
    }>;
    existingPaymentMethodRequired?: boolean;
  }) => Promise<{ status: number }>;
  requestPayment: (opts: {
    apiVersion: number;
    apiVersionMinor: number;
    paymentMethodTokenizationParameters: {
      tokenizationType: string;
      parameters: Record<string, string>;
    };
    allowedPaymentMethods: Array<{
      type: string;
      parameters: Record<string, unknown>;
    }>;
    transaction: {
      totalPrice: string;
      totalPriceStatus: string;
      currencyCode: string;
    };
  }) => Promise<{ paymentMethodToken: { token: string } }>;
}

const GooglePay = tryRequire<GooglePayApi>("expo-google-pay");

interface StripeApi {
  initPaymentSheet: (opts: {
    paymentIntentClientSecret: string;
    merchantDisplayName?: string;
  }) => Promise<{ error?: { message?: string } }>;
  presentPaymentSheet: () => Promise<{ error?: { message?: string } }>;
}

const Stripe: StripeApi | null =
  tryRequire<{ default: StripeApi }>("@stripe/stripe-react-native")?.default ??
  tryRequire<StripeApi>("@stripe/stripe-react-native");

export default function TopupScreen(): React.JSX.Element {
  const router = useRouter();
  const [amount, setAmount] = useState<string>("200");
  const [submitting, setSubmitting] = useState<TopupMethod | null>(null);
  const [result, setResult] = useState<{ kind: "ok" | "fail"; message: string } | null>(null);

  const numericAmount = Number(amount);
  const valid = Number.isFinite(numericAmount) && numericAmount >= 50 && numericAmount <= 10000;

  const onApplePay = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    if (!ApplePay) {
      setResult({
        kind: "fail",
        message: "expo-apple-pay native module not compiled. Run `eas build` (or use Stripe in dev).",
      });
      return;
    }
    setSubmitting("apple_pay");
    setResult(null);
    try {
      const intent = await api.createApplePayIntent();
      const res = await ApplePay.presentApplePayAsync({
        cartItems: [
          {
            label: `Wallet top-up HK$${amount}`,
            amount: String(amount),
            paymentType: "pending",
          },
        ],
        country: "HK",
        currency: "HKD",
        merchantIdentifier: intent.merchant_id,
      });
      // Hand the PKPaymentToken to the backend for validation + settlement.
      const settlement = await api.topUp({
        amount_hkd: amount,
        source: "apple_pay",
        source_payload: {
          pkpayment_token: JSON.stringify(res.token),
          merchant_id: intent.merchant_id,
        },
      });
      setResult({
        kind: "ok",
        message: `Top-up complete. Transaction ${settlement.transaction_id.slice(0, 8)}… (${settlement.amount_hkd} HKD)`,
      });
    } catch (e) {
      setResult({ kind: "fail", message: e instanceof Error ? e.message : String(e) });
    } finally {
      setSubmitting(null);
    }
  };

  const onGooglePay = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    if (!GooglePay) {
      setResult({
        kind: "fail",
        message: "expo-google-pay native module not compiled. Run `eas build` (or use Stripe in dev).",
      });
      return;
    }
    setSubmitting("google_pay");
    setResult(null);
    try {
      const ready = await GooglePay.isReadyToPay({
        apiVersion: 2,
        apiVersionMinor: 0,
        allowedPaymentMethods: [
          {
            type: "CARD",
            parameters: { allowedAuthMethods: ["PAN_ONLY", "CRYPTOGRAM_3DS"], allowedCardNetworks: ["VISA", "MASTERCARD"] },
          },
        ],
      });
      if (ready.status !== GooglePay.GooglePayStatus.AVAILABLE) {
        setResult({ kind: "fail", message: "Google Pay is not available on this device." });
        return;
      }
      const tokenRes = await GooglePay.requestPayment({
        apiVersion: 2,
        apiVersionMinor: 0,
        paymentMethodTokenizationParameters: {
          tokenizationType: "PAYMENT_GATEWAY",
          parameters: { gateway: "example", gatewayMerchantId: "evwallet-merchant" },
        },
        allowedPaymentMethods: [
          {
            type: "CARD",
            parameters: { allowedAuthMethods: ["PAN_ONLY", "CRYPTOGRAM_3DS"], allowedCardNetworks: ["VISA", "MASTERCARD"] },
          },
        ],
        transaction: {
          totalPrice: String(amount),
          totalPriceStatus: "FINAL",
          currencyCode: "HKD",
        },
      });
      const settlement = await api.topUp({
        amount_hkd: amount,
        source: "google_pay",
        source_payload: {
          google_pay_token: tokenRes.paymentMethodToken.token,
        },
      });
      setResult({
        kind: "ok",
        message: `Top-up complete. Transaction ${settlement.transaction_id.slice(0, 8)}… (${settlement.amount_hkd} HKD)`,
      });
    } catch (e) {
      setResult({ kind: "fail", message: e instanceof Error ? e.message : String(e) });
    } finally {
      setSubmitting(null);
    }
  };

  const onStripe = async () => {
    if (!valid) return Alert.alert("Enter an amount first", "Min HK$50, max HK$10,000.");
    if (!Stripe) {
      setResult({
        kind: "fail",
        message: "@stripe/stripe-react-native native module not compiled. Run `eas build` (or use web topup in dev).",
      });
      return;
    }
    setSubmitting("stripe");
    setResult(null);
    try {
      const intent = await api.createStripeIntent(amount);
      const init = await Stripe.initPaymentSheet({
        paymentIntentClientSecret: intent.client_secret,
        merchantDisplayName: "EV Wallet HK",
      });
      if (init.error) throw new Error(init.error.message);
      const present = await Stripe.presentPaymentSheet();
      if (present.error) throw new Error(present.error.message);
      setResult({
        kind: "ok",
        message: `Payment confirmed for PaymentIntent ${intent.payment_intent_id.slice(-8)}. Wallet credits via webhook.`,
      });
    } catch (e) {
      setResult({ kind: "fail", message: e instanceof Error ? e.message : String(e) });
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
          <PayButton
            label={` Pay`}
            note={ApplePay ? "Native Apple Pay sheet" : "Native module not compiled (Expo Go?)"}
            onPress={onApplePay}
            loading={submitting === "apple_pay"}
            disabled={!ApplePay}
          />
        ) : null}
        {Platform.OS === "android" ? (
          <PayButton
            label="G Pay"
            note={GooglePay ? "Native Google Pay sheet" : "Native module not compiled (Expo Go?)"}
            onPress={onGooglePay}
            loading={submitting === "google_pay"}
            disabled={!GooglePay}
          />
        ) : null}
        <PayButton
          label="Credit / debit card"
          note={Stripe ? "Powered by Stripe" : "Native module not compiled (Expo Go?)"}
          onPress={onStripe}
          loading={submitting === "stripe"}
          disabled={!Stripe}
        />

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
  disabled?: boolean;
}

function PayButton({ label, note, onPress, loading, disabled }: PayButtonProps): React.JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.payBtn,
        pressed && styles.payBtnPressed,
        disabled && styles.payBtnDisabled,
      ]}
      onPress={onPress}
      disabled={loading || disabled}
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
  payBtnDisabled: { opacity: 0.4 },
  payBtnLabel: { color: "#fff", fontWeight: "700", fontSize: 15 },
  payBtnNote: { color: "#94a3b8", fontSize: 12, marginTop: 2 },
  result: { marginTop: 16, borderRadius: 10, padding: 14 },
  resultOk: { backgroundColor: "#dcfce7" },
  resultFail: { backgroundColor: "#fee2e2" },
  resultText: { color: "#0f172a", fontSize: 13 },
});