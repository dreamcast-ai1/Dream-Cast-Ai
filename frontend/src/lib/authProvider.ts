/** Thin client for Supabase Auth's REST API (GoTrue). Only used when the backend reports AUTH_PROVIDER=supabase. */
export interface AuthConfig { provider: "local" | "supabase"; url: string | null; public_key: string | null; google_enabled?: boolean; email_verification?: boolean }
/** Returned by sign-up when an emailed one-time code must be entered before the account can be used. */
export interface VerificationInfo { email: string; expires_in: number; resend_after: number }

async function gotrue(cfg: AuthConfig, path: string, body?: unknown, token?: string, method = "POST") {
  const res = await fetch(`${cfg.url}/auth/v1${path}`, {
    method,
    headers: { apikey: cfg.public_key ?? "", "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.msg || data.error_description || data.message || "Authentication failed.");
  return data;
}

export const supabaseAuth = {
  password: (cfg: AuthConfig, email: string, password: string) => gotrue(cfg, "/token?grant_type=password", { email, password }),
  signUp: (cfg: AuthConfig, email: string, password: string, name: string) =>
    gotrue(cfg, "/signup", { email, password, data: { full_name: name } }),
  recover: (cfg: AuthConfig, email: string) =>
    gotrue(cfg, `/recover?redirect_to=${encodeURIComponent(location.origin + "/auth/callback")}`, { email }),
  updatePassword: (cfg: AuthConfig, token: string, password: string) => gotrue(cfg, "/user", { password }, token, "PUT"),
  googleUrl: (cfg: AuthConfig) => `${cfg.url}/auth/v1/authorize?provider=google&redirect_to=${encodeURIComponent(location.origin + "/auth/callback")}`,
};
