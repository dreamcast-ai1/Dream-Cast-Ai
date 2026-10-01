import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { GoogleButton } from "../components/GoogleButton";
import { Logo } from "../components/layout/AppShell";
import { TextField } from "../components/ui/Field";
import { Alert, PageLoader, Spinner } from "../components/ui/feedback";
import { useAuth } from "../context/AuthContext";
import { GENERIC_ERROR, errorMessage } from "../lib/api";
import type { VerificationInfo } from "../lib/authProvider";

function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-10">
      <div className="mb-6"><Logo /></div>
      <div className="card w-full max-w-sm p-6">
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}

const msg = (e: unknown) => (e instanceof Error && !(e as { status?: number }).status ? e.message || GENERIC_ERROR : errorMessage(e));
const GOOGLE_ERRORS: Record<string, string> = { google_failed: "Google sign-in didn't work. Please try again.", google_cancelled: "Google sign-in was cancelled.", google_unavailable: "Google sign-in isn't available right now." };
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** Counts down once per second from `from`; restart it by changing `key`. */
function useCountdown(from: number, key: number) {
  const [left, setLeft] = useState(from);
  useEffect(() => {
    setLeft(from);
    const t = setInterval(() => setLeft((n) => (n > 0 ? n - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, [from, key]);
  return left;
}

/** "Verify your email": enter the 6-digit code that was emailed. Signs the user in on success. */
function VerifyEmail({ info, onChangeEmail }: { info: VerificationInfo; onChangeEmail: () => void }) {
  const { verifyEmail, resendCode } = useAuth();
  const nav = useNavigate();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [note, setNote] = useState("");
  const [round, setRound] = useState(0); const [expiresIn, setExpiresIn] = useState(info.expires_in || 600); const [wait, setWait] = useState(info.resend_after);
  const expires = useCountdown(expiresIn, round);
  const cooldown = useCountdown(wait, round);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(""); setNote("");
    try { await verifyEmail(info.email, code.trim()); nav("/dashboard", { replace: true }); } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  };
  const resend = async () => {
    setBusy(true); setError(""); setNote("");
    try { const r = await resendCode(info.email); setNote(r.message); setExpiresIn(r.expires_in); setWait(r.resend_after); setCode(""); setRound((n) => n + 1); }
    catch (err) { setError(msg(err)); } finally { setBusy(false); }
  };
  return (
    <AuthLayout title="Verify your email" subtitle={`We sent a 6-digit code to ${info.email}.`}>
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert kind="error">{error}</Alert>}
        {note && <Alert kind="info">{note}</Alert>}
        <TextField label="Verification code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} hint={expires > 0 ? `The code expires in ${mmss(expires)}.` : "This code has expired. Request a new one."} />
        <button className="btn-primary w-full" disabled={busy || code.length !== 6}>{busy ? <Spinner /> : "Verify email"}</button>
      </form>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
        <button type="button" className="text-accent hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline" onClick={resend} disabled={busy || cooldown > 0}>
          {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}</button>
        <button type="button" className="text-muted hover:text-fg hover:underline" onClick={onChangeEmail}>Use a different email</button>
      </div>
    </AuthLayout>
  );
}

export function Login() {
  const { user, login, config, resendCode } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [params] = useSearchParams();
  const from = (loc.state as { from?: string } | null)?.from ?? "/dashboard";
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(GOOGLE_ERRORS[params.get("error") ?? ""] ?? "");
  const [verify, setVerify] = useState<VerificationInfo | null>(null);
  if (user) return <Navigate to={from} replace />;
  if (verify) return <VerifyEmail info={verify} onChangeEmail={() => setVerify(null)} />;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { await login(email.trim(), password); nav(from, { replace: true }); }
    catch (err) {
      if ((err as { code?: string }).code === "email_not_verified") {          // the password was right but the email was never verified: send a fresh code
        try { const r = await resendCode(email.trim()); setVerify({ email: email.trim().toLowerCase(), expires_in: r.expires_in, resend_after: r.resend_after }); }
        catch (e2) { setError(msg(e2)); }
      } else setError(msg(err));
    } finally { setBusy(false); }
  };
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to continue your story.">
      <div className="space-y-4">
        <GoogleButton />
        <div className="flex items-center gap-3 text-xs text-muted"><span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" /></div>
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert kind="error">{error}</Alert>}
          <TextField label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <TextField label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn-primary w-full" disabled={busy || !config}>{busy ? <Spinner /> : "Sign in"}</button>
        </form>
        <div className="flex justify-between text-sm"><Link className="text-accent hover:underline" to="/forgot-password">Forgot password?</Link>
          <Link className="text-accent hover:underline" to="/register">Create account</Link></div>
      </div>
    </AuthLayout>
  );
}

export function Register() {
  const { user, register, config } = useAuth();
  const nav = useNavigate();
  const [name, setName] = useState(""); const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [confirm, setConfirm] = useState(false);
  const [verify, setVerify] = useState<VerificationInfo | null>(null);
  if (user) return <Navigate to="/dashboard" replace />;
  if (verify) return <VerifyEmail info={verify} onChangeEmail={() => setVerify(null)} />;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== again) { setError("The two passwords don't match."); return; }
    setBusy(true); setError("");
    try {
      const r = await register(email.trim(), password, name.trim());
      if (r.verification) setVerify(r.verification); else if (r.needsConfirmation) setConfirm(true); else nav("/dashboard", { replace: true });
    } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  };
  if (confirm) return <AuthLayout title="Check your inbox"><Alert kind="success">We sent a confirmation link to {email}. Click it, then sign in.</Alert><Link to="/login" className="btn-primary mt-4 w-full">Go to sign in</Link></AuthLayout>;
  return (
    <AuthLayout title="Create your account" subtitle="Start building your first project.">
      <div className="space-y-4">
        <GoogleButton />
        <div className="flex items-center gap-3 text-xs text-muted"><span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" /></div>
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert kind="error">{error}</Alert>}
          <TextField label="Name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
          <TextField label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <TextField label="Password" type="password" autoComplete="new-password" required minLength={8} hint="At least 8 characters." value={password} onChange={(e) => setPassword(e.target.value)} />
          <TextField label="Confirm password" type="password" autoComplete="new-password" required minLength={8} value={again} onChange={(e) => setAgain(e.target.value)} />
          <button className="btn-primary w-full" disabled={busy || !config}>{busy ? <Spinner /> : "Create account"}</button>
        </form>
        <p className="text-center text-sm text-muted">Already have an account? <Link className="text-accent hover:underline" to="/login">Sign in</Link></p>
      </div>
    </AuthLayout>
  );
}

export function ForgotPassword() {
  const { forgotPassword } = useAuth();
  const [email, setEmail] = useState(""); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [done, setDone] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { setDone(await forgotPassword(email.trim())); } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  };
  return (
    <AuthLayout title="Reset your password" subtitle="We'll email you a link to choose a new password.">
      {done ? <Alert kind="success">{done}</Alert> : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert kind="error">{error}</Alert>}
          <TextField label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn-primary w-full" disabled={busy}>{busy ? <Spinner /> : "Send reset link"}</button>
        </form>
      )}
      <p className="mt-4 text-center text-sm"><Link className="text-accent hover:underline" to="/login">Back to sign in</Link></p>
    </AuthLayout>
  );
}

export function ResetPassword() {
  const { resetPassword } = useAuth();
  const [params] = useSearchParams();
  const [password, setPassword] = useState(""); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [done, setDone] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { setDone(await resetPassword(params.get("token") ?? "", password)); } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  };
  return (
    <AuthLayout title="Choose a new password">
      {done ? <><Alert kind="success">{done}</Alert><Link to="/login" className="btn-primary mt-4 w-full">Sign in</Link></> : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert kind="error">{error}</Alert>}
          <TextField label="New password" type="password" autoComplete="new-password" required minLength={8} hint="At least 8 characters." value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn-primary w-full" disabled={busy}>{busy ? <Spinner /> : "Update password"}</button>
        </form>
      )}
    </AuthLayout>
  );
}

/** Landing point for Supabase redirects (Google OAuth, password recovery). Tokens arrive in the URL hash. */
export function AuthCallback() {
  const { acceptExternalToken } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState("");
  useEffect(() => {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    const token = p.get("access_token");
    history.replaceState(null, "", location.pathname);
    if (!token) { setError(p.get("error_description") ?? "Sign-in did not complete. Please try again."); return; }
    acceptExternalToken(token).then(() => nav(p.get("type") === "recovery" ? "/reset-password" : "/dashboard", { replace: true }))
      .catch((e) => setError(errorMessage(e)));
  }, [acceptExternalToken, nav]);
  if (error) return <AuthLayout title="Sign-in failed"><Alert kind="error">{error}</Alert><Link to="/login" className="btn-primary mt-4 w-full">Back to sign in</Link></AuthLayout>;
  return <PageLoader label="Signing you in…" />;
}
