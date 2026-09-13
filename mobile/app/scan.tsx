/**
 * QR scanner screen.
 *
 * - Uses expo-camera BarcodeScanner (new API).
 * - Requests camera permission; shows denial UI with a "type QR manually" fallback.
 * - On successful scan: POST /charging/sessions -> navigate to /session/{id}.
 */

import { CameraView, useCameraPermissions } from "expo-camera";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { api } from "../lib/api";

export default function ScanScreen(): React.JSX.Element {
  const router = useRouter();
  const [permission, requestPermission] = useCameraPermissions();
  const [scanned, setScanned] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualValue, setManualValue] = useState("");
  const processingRef = useRef(false);

  // Ask for permission on mount if not yet determined
  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) {
      void requestPermission();
    }
  }, [permission, requestPermission]);

  const startSession = useCallback(
    async (qrCode: string) => {
      if (processingRef.current) return;
      processingRef.current = true;
      setSubmitting(true);
      try {
        const res = await api.startSession(qrCode);
        router.replace(`/session/${res.session_id}`);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        Alert.alert("Couldn't start session", msg, [
          { text: "OK", onPress: () => {
            processingRef.current = false;
            setScanned(false);
            setSubmitting(false);
          } },
        ]);
      }
    },
    [router],
  );

  const handleBarCodeScanned = useCallback(
    ({ data }: { data: string }) => {
      if (scanned || submitting) return;
      setScanned(true);
      void startSession(data);
    },
    [scanned, submitting, startSession],
  );

  // Permission states
  if (!permission) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#fff" />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.deniedTitle}>Camera access required</Text>
        <Text style={styles.deniedBody}>
          EV Wallet needs your camera to scan station QR codes.
        </Text>
        <Pressable
          style={styles.primaryBtn}
          onPress={() => void requestPermission()}
        >
          <Text style={styles.primaryBtnText}>Grant camera access</Text>
        </Pressable>
        <Pressable
          style={styles.secondaryBtn}
          onPress={() => setManualOpen(true)}
        >
          <Text style={styles.secondaryBtnText}>Enter QR code manually</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <CameraView
        style={StyleSheet.absoluteFill}
        facing="back"
        barcodeScannerSettings={{
          barcodeTypes: ["qr"],
        }}
        onBarcodeScanned={scanned ? undefined : handleBarCodeScanned}
      />

      <View style={styles.overlay} pointerEvents="box-none">
        <View style={styles.reticle} />
        <Text style={styles.hint}>Align the station QR within the frame</Text>
        <Pressable
          style={styles.secondaryBtn}
          onPress={() => setManualOpen(true)}
        >
          <Text style={styles.secondaryBtnText}>Enter QR code manually</Text>
        </Pressable>
      </View>

      <Modal visible={manualOpen} transparent animationType="fade" onRequestClose={() => setManualOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Enter QR code</Text>
            <TextInput
              value={manualValue}
              onChangeText={setManualValue}
              placeholder="Paste the QR payload here"
              placeholderTextColor="#94a3b8"
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
            <View style={styles.modalRow}>
              <Pressable style={styles.secondaryBtn} onPress={() => setManualOpen(false)}>
                <Text style={styles.secondaryBtnText}>Cancel</Text>
              </Pressable>
              <Pressable
                style={styles.primaryBtn}
                onPress={() => {
                  setManualOpen(false);
                  if (manualValue.trim().length > 0) {
                    void startSession(manualValue.trim());
                  }
                }}
              >
                <Text style={styles.primaryBtnText}>Submit</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {submitting ? (
        <View style={styles.submitting}>
          <ActivityIndicator color="#fff" />
          <Text style={styles.submittingText}>Starting session…</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#000" },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#0f172a",
    padding: 24,
  },
  overlay: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  reticle: {
    width: 240,
    height: 240,
    borderWidth: 3,
    borderColor: "#22d3ee",
    borderRadius: 20,
    backgroundColor: "transparent",
  },
  hint: {
    color: "#fff",
    marginTop: 20,
    fontSize: 14,
    backgroundColor: "rgba(0,0,0,0.5)",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
  },
  deniedTitle: { color: "#fff", fontSize: 18, fontWeight: "700", marginBottom: 8 },
  deniedBody: { color: "#cbd5e1", textAlign: "center", marginBottom: 24 },
  primaryBtn: {
    backgroundColor: "#22d3ee",
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
    marginVertical: 4,
  },
  primaryBtnText: { color: "#0f172a", fontWeight: "700" },
  secondaryBtn: {
    backgroundColor: "rgba(255,255,255,0.15)",
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
    marginVertical: 4,
  },
  secondaryBtnText: { color: "#fff", fontWeight: "600" },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  modalCard: {
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 20,
    width: "100%",
    maxWidth: 400,
  },
  modalTitle: { fontSize: 16, fontWeight: "700", color: "#0f172a" },
  input: {
    borderWidth: 1,
    borderColor: "#cbd5e1",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginTop: 12,
    fontSize: 14,
    color: "#0f172a",
  },
  modalRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 12,
  },
  submitting: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0,0,0,0.7)",
    alignItems: "center",
    justifyContent: "center",
  },
  submittingText: { color: "#fff", marginTop: 12 },
});
