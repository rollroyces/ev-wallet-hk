/**
 * Secure token storage using expo-secure-store.
 *
 * Tokens are stored in the platform secure enclave (Keychain on iOS,
 * EncryptedSharedPreferences / Android Keystore on Android).
 *
 * DO NOT use AsyncStorage for JWTs — they end up in plaintext on disk.
 */

import * as SecureStore from "expo-secure-store";

const ACCESS_TOKEN_KEY = "evwallet.access_token";
const REFRESH_TOKEN_KEY = "evwallet.refresh_token";
const USER_ID_KEY = "evwallet.user_id";

// iOS only — opt-in to iCloud sync of Keychain items; we keep it disabled
// so a token revocation on one device doesn't leak to another.
const SECURE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
};

export const auth = {
  async getAccessToken(): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(ACCESS_TOKEN_KEY, SECURE_OPTIONS);
    } catch {
      return null;
    }
  },

  async setAccessToken(token: string): Promise<void> {
    await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, token, SECURE_OPTIONS);
  },

  async getRefreshToken(): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(REFRESH_TOKEN_KEY, SECURE_OPTIONS);
    } catch {
      return null;
    }
  },

  async setRefreshToken(token: string): Promise<void> {
    await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token, SECURE_OPTIONS);
  },

  async getUserId(): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(USER_ID_KEY, SECURE_OPTIONS);
    } catch {
      return null;
    }
  },

  async setUserId(userId: string): Promise<void> {
    await SecureStore.setItemAsync(USER_ID_KEY, userId, SECURE_OPTIONS);
  },

  async clear(): Promise<void> {
    await Promise.all([
      SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY, SECURE_OPTIONS).catch(() => undefined),
      SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY, SECURE_OPTIONS).catch(() => undefined),
      SecureStore.deleteItemAsync(USER_ID_KEY, SECURE_OPTIONS).catch(() => undefined),
    ]);
  },
};

/**
 * Higher-level logout: clears tokens AND notifies any listeners.
 * Used by api.ts when refresh fails, and by the profile screen.
 */
export async function logout(onLoggedOut?: () => void): Promise<void> {
  await auth.clear();
  if (onLoggedOut) onLoggedOut();
}
