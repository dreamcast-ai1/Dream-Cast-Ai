import { Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AssetCard } from "../../components/AssetCard";
import { EmptyState, ErrorState, PageLoader } from "../../components/ui/feedback";
import { useAsync } from "../../hooks/useAsync";
import { usePolling } from "../../hooks/usePolling";
import { api } from "../../lib/api";
import type { Asset, Job } from "../../lib/types";
import { ACTIVE_STATUSES } from "../../lib/types";

export interface SectionDef { id: string; label: string; assetType?: string; generator: string; empty: string; cta: string; emoji: string }

/** Generated assets for one section, with every version listed. Refreshes quietly while a generation for it is still running. */
export function AssetSection({ projectId, section }: { projectId: string; section: SectionDef }) {
  const { data, setData, loading, error, reload } = useAsync(() => api<Asset[]>(`/api/projects/${projectId}/assets?type=${section.assetType}`), [projectId, section.assetType]);
  const [running, setRunning] = useState(false);
  usePolling(async () => {
    const jobs = await api<Job[]>(`/api/jobs?project_id=${projectId}&type=${section.generator}&limit=10`).catch(() => [] as Job[]);
    const active = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    if (running && !active) setData(await api<Asset[]>(`/api/projects/${projectId}/assets?type=${section.assetType}`));
    setRunning(active);
  }, true, 6000);
  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data?.length) {
    return <EmptyState icon={section.emoji} title={section.empty}
      hint={running ? "A generation is running in the background. It will appear here when it finishes." : "Create one and it will be saved here as a new version each time."}
      action={<Link className="btn-primary" to={`/create/${section.generator}?project=${projectId}`}>{section.cta}</Link>} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">{data.length} {data.length === 1 ? "item" : "items"}, newest first{running ? " · generating…" : ""}</p>
        <Link className="btn-primary" to={`/create/${section.generator}?project=${projectId}`}><Plus className="h-4 w-4" aria-hidden /> {section.cta}</Link>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2">{data.map((a) => <AssetCard key={a.id} a={a} />)}</ul>
    </div>
  );
}

const FILTERS = [["ALL", "All", ""], ["VIDEO", "Video", "VIDEO"], ["FACE", "Face", "FACE"], ["STORY", "Story", "STORY"], ["SCRIPT", "Script", "SCRIPT"], ["LYRICS", "Lyrics", "LYRICS"], ["MUSIC", "Music", "MUSIC"], ["VOICE", "Voice", "VOICE"]] as const;

/** Every asset in the project with basic type filtering. */
export function AllAssets({ projectId }: { projectId: string }) {
  const [filter, setFilter] = useState<string>("ALL");
  const type = FILTERS.find((f) => f[0] === filter)?.[2] ?? "";
  const { data, loading, error, reload } = useAsync(() => api<Asset[]>(`/api/projects/${projectId}/assets${type ? `?type=${type}` : ""}`), [projectId, type]);
  return (
    <div className="space-y-4">
      <div role="group" aria-label="Filter assets" className="flex flex-wrap gap-1.5">
        {FILTERS.map(([id, label]) => (
          <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}
            className={`rounded-full border px-3 py-1 text-sm font-medium ${filter === id ? "border-accent bg-accent/15" : "border-border text-muted hover:text-fg"}`}>{label}</button>))}
      </div>
      {loading ? <PageLoader /> : error ? <ErrorState message={error} onRetry={reload} />
        : !data?.length ? <EmptyState icon="🗂️" title={filter === "ALL" ? "No assets yet." : `No ${filter.toLowerCase()} assets yet.`} hint="Everything you generate for this project is collected here."
            action={<Link className="btn-primary" to={`/create?project=${projectId}`}>Create something</Link>} />
        : <ul className="grid gap-3 sm:grid-cols-2">{data.map((a) => <AssetCard key={a.id} a={a} />)}</ul>}
    </div>
  );
}
