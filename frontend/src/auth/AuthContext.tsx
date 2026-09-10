import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../api/client";
import type { RegisterInput } from "../api/client";
import type { CurrentUser } from "../api/types";

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  loginWithToken: (token: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    if (!api.getToken()) {
      setUser(null);
      return;
    }
    try {
      setUser(await api.getCurrentUser());
    } catch {
      // Stale/invalid/expired token - drop it rather than looping forever
      // on a token that will never work.
      api.clearToken();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refreshUser().finally(() => setLoading(false));
  }, [refreshUser]);

  const loginWithPassword = useCallback(async (email: string, password: string) => {
    const token = await api.login(email, password);
    api.setToken(token);
    await refreshUser();
  }, [refreshUser]);

  const loginWithToken = useCallback(async (token: string) => {
    api.setToken(token);
    await refreshUser();
  }, [refreshUser]);

  const register = useCallback(async (input: RegisterInput) => {
    await api.register(input);
    await loginWithPassword(input.email, input.password);
  }, [loginWithPassword]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // The JWT stays valid server-side until it expires (this backend
      // doesn't track a revocation list - see the main repo's README under
      // "What it doesn't do (yet)"), but clearing the client's copy is
      // still what "log out" has to mean from this browser's perspective.
      api.clearToken();
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, loading, loginWithPassword, loginWithToken, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
