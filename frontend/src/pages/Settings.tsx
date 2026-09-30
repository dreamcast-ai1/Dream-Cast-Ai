import { Monitor, Moon, Sun } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { UsageList } from "../components/UsageList";
import { Alert, ErrorState, PageHeader, PageLoader } from "../components/ui/feedback";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useTheme, type ThemeChoice } from "../context/ThemeContext";
import { useAsync } from "../hooks/useAsync";
import { api } from "../lib/api";
import { formatBytes, titleCase } from "../lib/format";
import type { ProviderInfo, UsageItem } from "../lib/types";
import { AccountPanel } from "./Account";

const TABS = [{ id: "account", label: "Account" }, { id: "appearance", label: "Appearance" }, { id: "api", label: "API Configuration" },
  { id: "usage", label: "Usage" }, { id: "storage", label: "Storage" }, { id: "about", label: "About" }];

function Appearance() {
  const { theme, setTheme } = useTheme();
  const opts: { id: ThemeChoice; label: string; icon: typeof Sun }[] = [{ id: "dark", label: "Dark", icon: Moon }, { id: "light", label: "Light", icon: Sun }, { id: "system", label: "System", icon: Monitor }];
  return (
    <div className="card p-5">
      <h2 className="mb-1 text-lg font-semibold">Theme</h2><p className="mb-4 text-sm text-muted">Dark is the default.</p>
      <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-3">
        {opts.map(({ id, label, icon: Icon }) => (
          <button key={id} role="radio" aria-checked={theme === id} onClick={() => setTheme(id)}
            className={`flex items-center gap-3 rounded-lg border px-4 py-3 text-sm font-medium ${theme === id ? "border-accent bg-accent/10" : "border-border text-muted hover:text-fg"}`}><Icon className="h-4 w-4" aria-hidden />{label}</button>))}
      </div>
    </div>
  );
}

function ApiConfig() {
  const { data, loading, error, reload } = useAsync(() => api<{ providers: ProviderInfo[] }>("/api/settings/providers"));
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return (
    <div className="space-y-4">
      <Alert kind="info">Provider API keys live in the server's environment variables (see <code>.env.example</code>). They are never sent to or stored in the browser, and their values are never shown here.</Alert>
      <ul className="card divide-y divide-border">{data!.providers.map((p) => (
        <li key={p.name} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 text-sm">
          <span><span className="font-medium">{p.simulated ? "Development simulator" : p.label}</span> <span className="text-muted">· {p.name}</span>
            <span className="block text-xs text-muted">{p.simulated ? "Avatars only — produces no real content"
              : [p.info.model, p.generators.length ? `Runs: ${p.generators.join(", ")}` : "Improves your prompts before generation", p.info.durations ? `Up to ${Math.max(...p.info.durations)} s clips` : "", p.info.aspect_ratios ? p.info.aspect_ratios.join(" / ") : "", p.info.image_to_video === false ? "no image-to-video" : "", p.info.languages ? p.info.languages.join(", ") : ""].filter(Boolean).join(" · ")}</span>
            {!p.configured && p.problems.length > 0 && <span className="block text-xs text-muted">{p.problems.join(" · ")}</span>}</span>
          <span className={`flex items-center gap-1.5 ${p.configured && p.enabled ? "text-success" : "text-muted"}`}><span aria-hidden className={`h-2 w-2 rounded-full ${p.configured && p.enabled ? "bg-success" : "bg-muted"}`} />{!p.enabled ? "Disabled by admin" : p.configured ? "Configured" : "Not configured"}</span>
        </li>))}</ul>
      <p className="text-xs text-muted">Status only reflects whether the server has the settings it needs; nothing is called to check. Keys are set in the server environment, not here. Remaining provider quota is not shown because providers don't report it.</p>
    </div>
  );
}

function UsageTab() {
  const { data, loading, error, reload } = useAsync(() => api<{ items: UsageItem[] }>("/api/usage"));
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return <UsageList items={data!.items} />;
}

function StorageTab() {
  const { data, loading, error, reload } = useAsync(() => api<{ backend: string; max_upload_mb: number; used_bytes: number }>("/api/settings/storage"));
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return (
    <dl className="card grid gap-4 p-5 text-sm sm:grid-cols-3">
      <div><dt className="text-muted">Backend</dt><dd className="mt-0.5 font-medium">{titleCase(data!.backend)} filesystem</dd></div>
      <div><dt className="text-muted">Your uploads</dt><dd className="mt-0.5 font-medium">{formatBytes(data!.used_bytes)}</dd></div>
      <div><dt className="text-muted">Max image size</dt><dd className="mt-0.5 font-medium">{data!.max_upload_mb} MB</dd></div>
    </dl>
  );
}

const About = () => (
  <div className="card space-y-2 p-5 text-sm">
    <p className="font-display text-lg font-semibold">DreamCast AI</p>
    <p className="text-muted">Version 0.1.0 — Phase 1 foundation: accounts, projects, characters, references, usage, jobs and admin. AI generators arrive in later phases.</p>
  </div>
);

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab")! : "account";
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" />
      <Tabs label="Settings sections" tabs={TABS} active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })} />
      <TabPanel id={tab}>
        {tab === "account" && <AccountPanel />}{tab === "appearance" && <Appearance />}{tab === "api" && <ApiConfig />}
        {tab === "usage" && <UsageTab />}{tab === "storage" && <StorageTab />}{tab === "about" && <About />}
      </TabPanel>
    </div>
  );
}
