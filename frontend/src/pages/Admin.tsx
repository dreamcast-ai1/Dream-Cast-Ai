import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, EmptyState, ErrorState, PageHeader, PageLoader, Skeleton, StatusBadge } from "../components/ui/feedback";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useAuth } from "../context/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import { formatDate, timeAgo } from "../lib/format";
import type { AdminStats, AdminUser, Job, ProviderInfo } from "../lib/types";

const PLAN_CHOICES: [string, string][] = [["trailer", "Trailer"], ["indie", "Indie"], ["blockbuster", "Blockbuster"]];
const TABS = [{ id: "overview", label: "Overview" }, { id: "users", label: "Users" }, { id: "limits", label: "Limits" },
  { id: "jobs", label: "Failed jobs" }, { id: "providers", label: "Providers" }];

function Overview() {
  const { data, loading, error, reload } = useAsync(() => api<AdminStats>("/api/admin/stats"));
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const cards: [string, number | undefined][] = [["Total users", data?.total_users], ["Active users", data?.active_users], ["Total generations", data?.total_generations],
    ["Failed generations", data?.failed_generations], ["API usage", data?.api_usage]];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      {cards.map(([label, value]) => (
        <div key={label} className="card p-4"><p className="text-xs text-muted">{label}</p>
          {loading ? <Skeleton className="mt-2 h-8 w-12" /> : <p className="mt-1 text-3xl font-semibold">{value ?? 0}</p>}</div>))}
    </div>
  );
}

function Users() {
  const { user: me } = useAuth();
  const { data, setData, loading, error, reload } = useAsync(() => api<AdminUser[]>("/api/admin/users"));
  const [err, setErr] = useState("");
  const setPlan = async (u: AdminUser, plan_id: string) => {      // manual grant for demos/support: not a payment
    setErr("");
    try { await api(`/api/admin/users/${u.id}/subscription`, { method: "PATCH", json: { plan_id, days: plan_id === "trailer" ? undefined : 30 } }); await reload(); }
    catch (e) { setErr(errorMessage(e)); }
  };
  const patch = async (u: AdminUser, body: { is_active?: boolean; role?: string }) => {
    setErr("");
    try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: body }); setData((data ?? []).map((x) => (x.id === u.id ? { ...x, ...body } as AdminUser : x))); }
    catch (e) { setErr(errorMessage(e)); }
  };
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return (
    <div className="space-y-3">
      {err && <Alert kind="error">{err}</Alert>}
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[64rem] text-left text-sm">
          <thead className="border-b border-border text-xs text-muted"><tr>{["User", "Role", "Plan", "Subscription", "Used today", "Payment", "Status", "Generations", "Requests", "Joined", ""].map((h) => <th key={h} scope="col" className="px-4 py-2.5 font-medium">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-border">{data!.map((u) => (
            <tr key={u.id}>
              <td className="px-4 py-3"><p className="font-medium">{u.name}</p><p className="text-xs text-muted">{u.email}</p></td>
              <td className="px-4 py-3">{u.role === "ADMIN" ? "Admin" : "User"}</td>
              <td className="px-4 py-3"><label className="sr-only" htmlFor={`plan-${u.id}`}>Plan for {u.email}</label>
                <select id={`plan-${u.id}`} className="field !w-36 !py-1 !text-xs" value={u.plan_id} onChange={(e) => void setPlan(u, e.target.value)}>
                  {PLAN_CHOICES.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></td>
              <td className="px-4 py-3"><StatusBadge status={u.subscription_status} />{u.subscription_expires_at && <p className="mt-1 text-xs text-muted">to {formatDate(u.subscription_expires_at)}</p>}</td>
              <td className="px-4 py-3">{u.used_today}</td>
              <td className="px-4 py-3 text-muted">{u.payment_status ? u.payment_status.toLowerCase() : "—"}</td>
              <td className="px-4 py-3"><StatusBadge status={u.is_active ? "ACTIVE" : "DISABLED"} /></td>
              <td className="px-4 py-3">{u.generations}</td><td className="px-4 py-3">{u.requests}</td>
              <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDate(u.created_at)}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right">
                <button className="btn-secondary !py-1 !text-xs" disabled={u.id === me?.id} onClick={() => patch(u, { is_active: !u.is_active })} aria-label={`${u.is_active ? "Disable" : "Enable"} ${u.email}`}>{u.is_active ? "Disable" : "Enable"}</button>
                <button className="btn-ghost ml-1 !py-1 !text-xs" disabled={u.id === me?.id} onClick={() => patch(u, { role: u.role === "ADMIN" ? "USER" : "ADMIN" })} aria-label={`${u.role === "ADMIN" ? "Remove admin from" : "Make admin"} ${u.email}`}>{u.role === "ADMIN" ? "Remove admin" : "Make admin"}</button>
              </td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}

function Limits() {
  const [plan, setPlan] = useState("trailer");
  const { data, loading, error, reload } = useAsync(() => api<{ plan: string; plans: { id: string; name: string }[]; items: { generator: string; label: string; emoji: string; limit: number }[] }>(`/api/admin/limits?plan=${plan}`), [plan]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  if (loading && !data) return <PageLoader />;
  if (error || !data) return <ErrorState message={error ?? "Could not load limits."} onRetry={reload} />;
  const save = async () => {
    const limits = Object.fromEntries(Object.entries(edits).map(([k, v]) => [k, Number(v)]));
    if (Object.values(limits).some((n) => !Number.isInteger(n) || n < 0)) { setMsg({ kind: "error", text: "Limits must be whole numbers, 0 or higher." }); return; }
    try { await api("/api/admin/limits", { method: "PUT", json: { plan, limits } }); setEdits({}); setMsg({ kind: "success", text: "Limits saved." }); void reload(); }
    catch (e) { setMsg({ kind: "error", text: errorMessage(e) }); }
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div><label htmlFor="limits-plan" className="mb-1 block text-sm font-medium">Plan</label>
          <select id="limits-plan" className="field !w-48" value={plan} onChange={(e) => { setPlan(e.target.value); setEdits({}); setMsg(null); }}>{data.plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
        <p className="pb-2 text-sm text-muted">Generations each user on this plan may start per usage period. Defaults live in <code>backend/app/plans.py</code>.</p>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{data.items.map((i) => (
        <li key={i.generator} className="card flex items-center justify-between gap-3 p-4">
          <label htmlFor={`lim-${i.generator}`} className="text-sm font-medium"><span aria-hidden>{i.emoji}</span> {i.label}</label>
          <input id={`lim-${i.generator}`} type="number" min={0} className="field !w-24 text-right" value={edits[i.generator] ?? String(i.limit)} onChange={(e) => setEdits({ ...edits, [i.generator]: e.target.value })} />
        </li>))}</ul>
      <button className="btn-primary" disabled={!Object.keys(edits).length} onClick={save}>Save limits</button>
    </div>
  );
}

function FailedJobs() {
  const { data, loading, error, reload } = useAsync(() => api<Job[]>("/api/admin/jobs?status=FAILED"));
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data?.length) return <EmptyState icon="✅" title="No failed jobs." hint="Failed generation jobs will be listed here." />;
  return <ul className="card divide-y divide-border">{data.map((j) => (
    <li key={j.id} className="px-4 py-3 text-sm"><div className="flex justify-between gap-3"><span className="font-medium">{j.type}</span><span className="text-xs text-muted">{timeAgo(j.created_at)}</span></div>
      <p className="mt-1 break-words text-danger">{j.error_message ?? "No error message."}</p></li>))}</ul>;
}

function Providers() {
  const { data, setData, loading, error, reload } = useAsync(() => api<{ providers: ProviderInfo[] }>("/api/admin/providers"));
  const [err, setErr] = useState("");
  const [caps, setCaps] = useState<Record<string, string>>({});
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const save = async (name: string, body: { enabled?: boolean; daily_cap?: number; clear_cap?: boolean }) => {
    setErr("");
    try { setData(await api<{ providers: ProviderInfo[] }>(`/api/admin/providers/${name}`, { method: "PUT", json: body })); setCaps((c) => { const n = { ...c }; delete n[name]; return n; }); }
    catch (e) { setErr(errorMessage(e)); }
  };
  return (
    <div className="space-y-3">
      {err && <Alert kind="error">{err}</Alert>}
      <p className="text-sm text-muted">Enable/disable providers and set an optional daily request cap. The provider's own remaining quota is unknown to DreamCast unless a provider reports it, so none is shown.</p>
      <ul className="grid gap-3">{data!.providers.map((p) => (
        <li key={p.name} className="card p-4 text-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="font-medium">{p.label} <span className="text-muted">· {p.name}</span>{p.simulated && <span className="ml-2 rounded bg-warn/15 px-1.5 py-0.5 text-xs text-warn">simulator</span>}</p>
              <p className="text-xs text-muted">{[p.generators.length ? `Runs: ${p.generators.join(", ")}` : "Used for prompt refinement", p.info.model ? `Model: ${p.info.model}` : "", p.info.durations ? `Clips up to ${Math.max(...p.info.durations)} s` : "", p.info.languages ? `Languages: ${p.info.languages.join(", ")}` : ""].filter(Boolean).join(" · ")}</p>
              <p className={`mt-1 flex items-center gap-1.5 text-xs ${p.configured ? "text-success" : "text-warn"}`}><span aria-hidden className={`h-2 w-2 rounded-full ${p.configured ? "bg-success" : "bg-warn"}`} />{p.configured ? "Configured" : `Not configured — ${p.problems.join(" · ")}`}<span className="text-muted"> · {p.enabled ? "Enabled" : "Disabled"}</span></p>
              <p className="text-xs text-muted">API key: {(p.info.key_configured ?? p.configured) ? "Configured" : "Not configured"} (never displayed){p.info.i2v_model ? ` · Image-to-video model: ${p.info.i2v_model}` : ""}{p.info.aspect_ratios ? ` · Formats: ${p.info.aspect_ratios.join(", ")}` : ""}</p></div>
            {p.generators.length > 0 && (
              <div className="flex flex-wrap items-end gap-3">
                <button className={p.enabled ? "btn-secondary" : "btn-primary"} onClick={() => save(p.name, { enabled: !p.enabled })} aria-label={`${p.enabled ? "Disable" : "Enable"} ${p.name}`}>{p.enabled ? "Disable" : "Enable"}</button>
                <div><label htmlFor={`cap-${p.name}`} className="mb-1 block text-xs text-muted">Daily cap {p.daily_cap == null ? "(none)" : ""}</label>
                  <div className="flex gap-1"><input id={`cap-${p.name}`} type="number" min={0} className="field !w-24" value={caps[p.name] ?? (p.daily_cap ?? "")} onChange={(e) => setCaps({ ...caps, [p.name]: e.target.value })} />
                    <button className="btn-secondary" disabled={caps[p.name] === undefined} onClick={() => (caps[p.name] === "" ? save(p.name, { clear_cap: true }) : save(p.name, { daily_cap: Number(caps[p.name]) }))}>Set</button></div>
                  <p className="mt-1 text-xs text-muted">Used today: {p.used_today ?? 0}</p></div>
              </div>)}
          </div>
        </li>))}</ul>
    </div>
  );
}

export default function Admin() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab")! : "overview";
  return (
    <div>
      <PageHeader title="Admin" subtitle="Manage users, limits and system health." />
      <Tabs label="Admin sections" tabs={TABS} active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })} />
      <TabPanel id={tab}>{tab === "overview" && <Overview />}{tab === "users" && <Users />}{tab === "limits" && <Limits />}{tab === "jobs" && <FailedJobs />}{tab === "providers" && <Providers />}</TabPanel>
    </div>
  );
}
