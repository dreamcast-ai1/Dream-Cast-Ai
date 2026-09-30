import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, tokenStore } from "../lib/api";
import { supabaseAuth, type AuthConfig } from "../lib/authProvider";
import type { User } from "../lib/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  config: AuthConfig | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<{ needsConfirmation: boolean }>;
  loginWithGoogle: () => void;
  forgotPassword: (email: string) => Promise<string>;
  resetPassword: (token: string, password: string) => Promise<string>;
  acceptExternalToken: (token: string) => Promise<void>;
  updateName: (name: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => setUser(await api<User>("/api/auth/me")), []);

  useEffect(() => {
    (async () => {
      try {
        setConfig(await api<AuthConfig>("/api/auth/config"));
        if (tokenStore.get()) await loadMe();
      } catch { tokenStore.clear(); } finally { setLoading(false); }
    })();
    const onUnauth = () => setUser(null);
    window.addEventListener("dc-unauthorized", onUnauth);
    return () => window.removeEventListener("dc-unauthorized", onUnauth);
  }, [loadMe]);

  const value = useMemo<AuthState>(() => ({
    user, loading, config,
    async login(email, password) {
      if (config?.provider === "supabase") {
        const d = await supabaseAuth.password(config, email, password);
        tokenStore.set(d.access_token);
        await loadMe();
      } else {
        const d = await api<{ access_token: string; user: User }>("/api/auth/login", { method: "POST", json: { email, password } });
        tokenStore.set(d.access_token);
        setUser(d.user);
      }
    },
    async register(email, password, name) {
      if (config?.provider === "supabase") {
        const d = await supabaseAuth.signUp(config, email, password, name);
        if (!d.access_token) return { needsConfirmation: true };
        tokenStore.set(d.access_token);
        await loadMe();
        return { needsConfirmation: false };
      }
      const d = await api<{ access_token: string; user: User }>("/api/auth/register", { method: "POST", json: { email, password, name } });
      tokenStore.set(d.access_token);
      setUser(d.user);
      return { needsConfirmation: false };
    },
    loginWithGoogle() { if (config?.provider === "supabase") window.location.href = supabaseAuth.googleUrl(config); },
    async forgotPassword(email) {
      if (config?.provider === "supabase") { await supabaseAuth.recover(config, email); return "If an account exists for that email, a reset link has been sent."; }
      return (await api<{ message: string }>("/api/auth/forgot-password", { method: "POST", json: { email } })).message;
    },
    async resetPassword(token, password) {
      if (config?.provider === "supabase") { await supabaseAuth.updatePassword(config, tokenStore.get() ?? token, password); return "Password updated."; }
      return (await api<{ message: string }>("/api/auth/reset-password", { method: "POST", json: { token, password } })).message;
    },
    async acceptExternalToken(token) { tokenStore.set(token); await loadMe(); },
    async updateName(name) { setUser(await api<User>("/api/auth/me", { method: "PATCH", json: { name } })); },
    logout() { tokenStore.clear(); setUser(null); },
  }), [user, loading, config, loadMe]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
