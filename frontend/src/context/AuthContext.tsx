import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, resolveUrl, tokenStore } from "../lib/api";
import { supabaseAuth, type AuthConfig, type VerificationInfo } from "../lib/authProvider";
import type { User } from "../lib/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  config: AuthConfig | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<{ needsConfirmation: boolean; verification?: VerificationInfo }>;
  verifyEmail: (email: string, code: string) => Promise<void>;
  resendCode: (email: string) => Promise<{ message: string; resend_after: number; expires_in: number }>;
  /** Starts Google sign-in. Resolves only if the browser is being redirected; rejects with a user-readable message otherwise (never silent). */
  loginWithGoogle: () => Promise<void>;
  /** "loading" while the server is being reached (a sleeping free-tier server can take a minute), "error" if it could not be reached. */
  configStatus: "loading" | "ready" | "error";
  retryConfig: () => void;
  forgotPassword: (email: string) => Promise<string>;
  resetPassword: (token: string, password: string) => Promise<string>;
  acceptExternalToken: (token: string) => Promise<void>;
  updateName: (name: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 12000];     // ~27 s in total: enough for a sleeping server to wake up
const transient = (e: unknown) => e instanceof ApiError ? e.status === 0 || e.status >= 500 : true;
const debug = (...a: unknown[]) => { if (import.meta.env.DEV) console.debug("[auth]", ...a); };      // development only; never logs tokens, codes or credentials

/** Retries only network failures and 5xx answers (a cold-starting server); a definite answer such as 401/403/404 is final. */
async function withRetry<T>(label: string, fn: () => Promise<T>, signal?: { cancelled: boolean }): Promise<T> {
  for (let i = 0; ; i++) {
    try { return await fn(); }
    catch (e) {
      if (!transient(e) || i >= RETRY_DELAYS_MS.length || signal?.cancelled) throw e;
      debug(`${label} failed (${e instanceof ApiError ? e.status : "network"}), retry ${i + 1}`);
      await new Promise((r) => setTimeout(r, RETRY_DELAYS_MS[i]));
    }
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const [configStatus, setConfigStatus] = useState<"loading" | "ready" | "error">("loading");
  const [configTry, setConfigTry] = useState(0);

  const loadMe = useCallback(async () => setUser(await api<User>("/api/auth/me")), []);

  // The sign-in options (is Google on?) are fetched independently of the session so the login page never waits for them and a slow
  // or sleeping server can't leave the Google button permanently dead.
  useEffect(() => {
    const flag = { cancelled: false };
    setConfigStatus("loading");
    withRetry("config", () => api<AuthConfig>("/api/auth/config"), flag)
      .then((c) => { if (!flag.cancelled) { setConfig(c); setConfigStatus("ready"); debug("config loaded", { provider: c.provider, google: !!c.google_enabled }); } })
      .catch(() => { if (!flag.cancelled) setConfigStatus("error"); });
    return () => { flag.cancelled = true; };
  }, [configTry]);

  useEffect(() => {
    (async () => {
      try {
        if (tokenStore.get()) await withRetry("me", loadMe);
      } catch (e) {
        // Only a definite "not signed in" answer ends the session. A network error or a sleeping server must not log the user out.
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) tokenStore.clear();
      } finally { setLoading(false); }
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
      const d = await api<{ access_token?: string; user?: User } & Partial<VerificationInfo> & { verification_required?: boolean }>("/api/auth/register", { method: "POST", json: { email, password, name } });
      if (d.verification_required) return { needsConfirmation: false, verification: { email: d.email!, expires_in: d.expires_in!, resend_after: d.resend_after! } };
      tokenStore.set(d.access_token!);
      setUser(d.user!);
      return { needsConfirmation: false };
    },
    async verifyEmail(email, code) {
      const d = await api<{ access_token: string; user: User }>("/api/auth/verify-email", { method: "POST", json: { email, code } });
      tokenStore.set(d.access_token);
      setUser(d.user);
    },
    resendCode(email) { return api("/api/auth/resend-otp", { method: "POST", json: { email } }); },
    configStatus,
    retryConfig: () => setConfigTry((n) => n + 1),
    async loginWithGoogle() {
      let cfg = config;
      if (!cfg) {      // the click arrived before the server answered: ask now (with retries) instead of silently doing nothing
        try { cfg = await withRetry("config", () => api<AuthConfig>("/api/auth/config")); setConfig(cfg); setConfigStatus("ready"); }
        catch { throw new Error("We couldn't reach the DreamCast server. It may be starting up: please wait a few seconds and try again."); }
      }
      if (cfg.provider === "supabase") { debug("redirecting to Google (supabase)"); window.location.href = supabaseAuth.googleUrl(cfg); return; }
      if (!cfg.google_enabled) throw new Error("Google sign-in isn't set up on this server yet. Please use your email and password.");
      debug("redirecting to Google");
      window.location.href = resolveUrl("/api/auth/google/start");     // the backend talks to Google; no secret is involved here
    },
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
  }), [user, loading, config, configStatus, loadMe]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
