import { useState } from "react";
import { Alert, EmptyState, ErrorState, PageLoader } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import { timeAgo } from "../lib/format";
import { STATUS_LABEL, type SupportTicket } from "./types";

const STATUSES: [string, string][] = [["", "All"], ["open", "Open"], ["in_progress", "In progress"], ["resolved", "Resolved"], ["closed", "Closed"]];
const CATEGORIES = ["AUTH", "WRITE", "VIDEO", "IMAGE", "MUSIC", "VOICE", "LYRICS", "PROJECT", "PAYMENT", "USAGE", "UPLOAD", "GENERAL"];
const PRIORITY_STYLE: Record<string, string> = { critical: "bg-danger/15 text-danger", high: "bg-warn/15 text-warn", normal: "bg-raised text-muted", low: "bg-raised text-muted" };

function Ticket({ t, onChange }: { t: SupportTicket; onChange: (t: SupportTicket) => void }) {
  const [open, setOpen] = useState(false);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const call = async (path: string, method: string, json: unknown) => {
    setBusy(true); setErr("");
    try { onChange(await api<SupportTicket>(`/api/admin/support/tickets/${t.id}${path}`, { method, json })); return true; }
    catch (e) { setErr(errorMessage(e)); return false; } finally { setBusy(false); }
  };
  const ctx = Object.entries(t.diagnostic_context ?? {});
  return (
    <li className="card text-sm">
      <button className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="font-mono text-xs font-semibold">#{t.id}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{t.subject}</span>
        <span className={`rounded-full px-2 py-0.5 text-xs ${PRIORITY_STYLE[t.priority]}`}>{t.priority}</span>
        <span className="rounded-full bg-raised px-2 py-0.5 text-xs">{STATUS_LABEL[t.status]}</span>
        <span className="text-xs text-muted">{t.category_label} · {t.user?.email ?? "user"} · {timeAgo(t.created_at)}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-4 py-3">
          <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            <div><dt className="text-muted">User</dt><dd className="font-medium">{t.user?.name || "—"} · {t.user?.email ?? "—"}</dd></div>
            <div><dt className="text-muted">Page / feature</dt><dd className="font-medium">{t.page ?? "—"} · {t.feature ?? "—"}</dd></div>
            {t.error_message && <div className="sm:col-span-2"><dt className="text-muted">User-visible error</dt><dd className="break-words font-medium">{t.error_message}</dd></div>}
            {ctx.length > 0 && <div className="sm:col-span-2"><dt className="text-muted">Safe diagnostics</dt><dd className="break-words font-mono">{ctx.map(([k, v]) => `${k}=${v}`).join(" · ")}</dd></div>}
          </dl>
          <p className="whitespace-pre-line break-words">{t.description}</p>
          {t.messages.length > 0 && <ul className="space-y-1.5 rounded-lg border border-border p-3 text-xs">{t.messages.map((m, i) => <li key={i} className={m.author === "admin" ? "text-accent" : ""}><span className="font-semibold">{m.author === "admin" ? "Admin" : "User"}</span> · {timeAgo(m.created_at)}: <span className="whitespace-pre-line break-words">{m.body}</span></li>)}</ul>}
          {err && <Alert kind="error">{err}</Alert>}
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-secondary" disabled={busy || t.status === "in_progress"} onClick={() => void call("", "PATCH", { status: "in_progress" })}>Mark In Progress</button>
            <button className="btn-secondary" disabled={busy || t.status === "resolved"} onClick={() => void call("", "PATCH", { status: "resolved" })}>Resolve</button>
            <button className="btn-secondary" disabled={busy || t.status === "closed"} onClick={() => void call("", "PATCH", { status: "closed" })}>Close</button>
            <label className="ml-auto flex items-center gap-2 text-xs text-muted">Priority
              <select className="field !w-28 !py-1" value={t.priority} disabled={busy} onChange={(e) => void call("", "PATCH", { priority: e.target.value })}>{["low", "normal", "high", "critical"].map((p) => <option key={p}>{p}</option>)}</select></label>
          </div>
          <form onSubmit={async (e) => { e.preventDefault(); if (reply.trim() && await call("/reply", "POST", { message: reply.trim() })) setReply(""); }} className="space-y-2">
            <label htmlFor={`reply-${t.id}`} className="text-xs font-medium">Reply to the user (they see this under My tickets and get a notification)</label>
            <textarea id={`reply-${t.id}`} className="field" rows={3} maxLength={4000} value={reply} onChange={(e) => setReply(e.target.value)} />
            <button className="btn-primary" disabled={busy || !reply.trim()}>Send reply</button>
          </form>
        </div>)}
    </li>
  );
}

/** Admin → Support: every ticket with filters and actions. The API enforces admin access; this is only the screen. */
export function AdminSupport() {
  const [status, setStatus] = useState(""); const [category, setCategory] = useState(""); const [priority, setPriority] = useState(""); const [days, setDays] = useState("");
  const qs = new URLSearchParams(Object.entries({ status, category, priority, days }).filter(([, v]) => v)).toString();
  const { data, setData, loading, error, reload } = useAsync(() => api<{ tickets: SupportTicket[]; counts: Record<string, number> }>(`/api/admin/support/tickets${qs ? `?${qs}` : ""}`), [qs]);
  if (loading && !data) return <PageLoader />;
  if (error || !data) return <ErrorState message={error ?? "Could not load tickets."} onRetry={reload} />;
  const replace = (t: SupportTicket) => { setData({ ...data, tickets: data.tickets.map((x) => (x.id === t.id ? { ...t, user: x.user } : x)) }); void reload(); };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by status">
        {STATUSES.map(([v, l]) => <button key={v} className={status === v ? "btn-primary !py-1.5" : "btn-secondary !py-1.5"} aria-pressed={status === v} onClick={() => setStatus(v)}>{l}{v ? ` (${data.counts[v] ?? 0})` : ""}</button>)}
        <select aria-label="Category" className="field !w-36 !py-1.5" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>
        <select aria-label="Priority" className="field !w-32 !py-1.5" value={priority} onChange={(e) => setPriority(e.target.value)}><option value="">All priorities</option>{["critical", "high", "normal", "low"].map((p) => <option key={p}>{p}</option>)}</select>
        <select aria-label="Date" className="field !w-36 !py-1.5" value={days} onChange={(e) => setDays(e.target.value)}><option value="">Any date</option><option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select>
      </div>
      {data.tickets.length === 0 ? <EmptyState icon="🎧" title="No support tickets." hint="Issues users send from the Support chat appear here." />
        : <ul className="space-y-2">{data.tickets.map((t) => <Ticket key={t.id} t={t} onChange={replace} />)}</ul>}
    </div>
  );
}
