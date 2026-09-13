/**
 * Auth context: provides the current user + token state to the app tree.
 *
 * - On mount, tries to fetch /auth/me using the stored JWT.
 * - If no token or /auth/me returns 401, isAuthenticated = false.
 * - login()/logout() update the in-memory state and persist the token.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { api } from "./api";
import { auth } from "./auth";
import type { Session, User, WalletSummary } from "./types";

export interface AuthState {
  loading: boolean;
  isAuthenticated: boolean;
  user: User | null;
  walletSummary: WalletSummary | null;
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  loginApple: (idToken: string) => Promise<void>;
  loginGoogle: (idToken: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function applySession(
  session: Session,
  setState: Dispatch<SetStateAction<AuthState>>,
): void {
  setState({
    isAuthenticated: true,
    user: session.user,
    walletSummary: null, // fetched lazily via /auth/me
    loading: false,
  });
}

export function AuthProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [state, setState] = useState<AuthState>({
    loading: true,
    isAuthenticated: false,
    user: null,
    walletSummary: null,
  });

  const refresh = useCallback(async (): Promise<void> => {
    const token = await auth.getAccessToken();
    if (!token) {
      setState({ loading: false, isAuthenticated: false, user: null, walletSummary: null });
      return;
    }
    try {
      const me = await api.me();
      setState({
        loading: false,
        isAuthenticated: true,
        user: me.user,
        walletSummary: me.wallet,
      });
    } catch {
      // Token invalid; clear state
      await auth.clear();
      setState({ loading: false, isAuthenticated: false, user: null, walletSummary: null });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    setState((s) => ({ ...s, loading: true }));
    const session = await api.login(email, password);
    applySession(session, setState);
  }, []);

  const loginApple = useCallback(async (idToken: string) => {
    setState((s) => ({ ...s, loading: true }));
    const session = await api.loginApple(idToken);
    applySession(session, setState);
  }, []);

  const loginGoogle = useCallback(async (idToken: string) => {
    setState((s) => ({ ...s, loading: true }));
    const session = await api.loginGoogle(idToken);
    applySession(session, setState);
  }, []);

  const logout = useCallback(async () => {
    await auth.clear();
    setState({ loading: false, isAuthenticated: false, user: null, walletSummary: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, loginApple, loginGoogle, logout, refresh }),
    [state, login, loginApple, loginGoogle, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
