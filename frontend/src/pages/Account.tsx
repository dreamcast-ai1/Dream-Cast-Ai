import { LogOut } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { TextField } from "../components/ui/Field";
import { Alert, PageHeader } from "../components/ui/feedback";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { api } from "../lib/api";
import type { CurrentSubscription } from "../lib/types";
import { errorMessage } from "../lib/api";
import { formatDate, titleCase } from "../lib/format";

export function AccountPanel() {
  const { user, updateName, logout } = useAuth();
  const nav = useNavigate();
  const plan = useAsync(() => api<CurrentSubscription>("/api/subscription/current"));
  const [name, setName] = useState(user?.name ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  if (!user) return null;

  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try { await updateName(name.trim()); setMsg({ kind: "success", text: "Profile updated." }); } catch (err) { setMsg({ kind: "error", text: errorMessage(err) }); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-6">
      <div className="card flex items-center gap-4 p-5">
        {user.avatar_url ? <img src={user.avatar_url} alt={`${user.name} profile`} referrerPolicy="no-referrer" className="h-16 w-16 rounded-full object-cover" />
          : <span aria-hidden className="flex h-16 w-16 items-center justify-center rounded-full bg-accent text-2xl font-semibold text-accent-fg">{(user.name || user.email)[0].toUpperCase()}</span>}
        <div className="min-w-0"><p className="truncate text-lg font-semibold">{user.name}</p><p className="truncate text-sm text-muted">{user.email}</p></div>
      </div>
      <dl className="card grid gap-4 p-5 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="text-muted">Sign-in method</dt><dd className="mt-0.5 font-medium">{user.auth_provider === "local" ? "Email & password" : titleCase(user.auth_provider)}</dd></div>
        <div><dt className="text-muted">Member since</dt><dd className="mt-0.5 font-medium">{formatDate(user.created_at)}</dd></div>
        <div><dt className="text-muted">Role</dt><dd className="mt-0.5 font-medium">{titleCase(user.role)}</dd></div>
        <div><dt className="text-muted">Plan</dt><dd className="mt-0.5 font-medium">{plan.data ? <Link className="text-accent hover:underline" to="/plans">{plan.data.plan.name}</Link> : "…"}</dd></div>
      </dl>
      <form onSubmit={save} className="card space-y-4 p-5">
        <h2 className="text-lg font-semibold">Profile</h2>
        {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        <TextField label="Display name" value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
        <TextField label="Email" value={user.email} disabled readOnly hint="Email is managed by your sign-in method." />
        <button className="btn-primary" disabled={busy || !name.trim() || name.trim() === user.name}>{busy ? "Saving…" : "Save changes"}</button>
      </form>
      <button className="btn-secondary" onClick={() => { logout(); nav("/"); }}><LogOut className="h-4 w-4" aria-hidden /> Log out</button>
    </div>
  );
}

export default function Account() {
  return <div className="mx-auto max-w-2xl"><PageHeader title="Account" /><AccountPanel /></div>;
}
