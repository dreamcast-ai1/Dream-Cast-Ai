import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ProjectFormModal } from "../components/ProjectFormModal";
import { ConfirmDialog } from "../components/ui/Modal";
import { Alert, ErrorState, PageLoader, StatusBadge } from "../components/ui/feedback";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useFeatures } from "../context/FeaturesContext";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import { formatDate, timeAgo } from "../lib/format";
import type { ProjectDetail } from "../lib/types";
import { Characters } from "./project/Characters";
import { References } from "./project/References";
import { AllAssets, AssetSection, type SectionDef } from "./project/SectionEmpty";
import { Generations } from "./project/Generations";
import { Movie } from "./project/Movie";

const SECTIONS: SectionDef[] = [
  { id: "story", label: "Story", assetType: "STORY", generator: "story", empty: "No stories yet.", cta: "Create Story", emoji: "📖" },
  { id: "script", label: "Script", assetType: "SCRIPT", generator: "script", empty: "No scripts yet.", cta: "Create Script", emoji: "📝" },
  { id: "videos", label: "Videos", assetType: "VIDEO", generator: "video", empty: "No videos yet.", cta: "Create Video", emoji: "🎬" },
  { id: "images", label: "Images", assetType: "IMAGE", generator: "image", empty: "No images yet.", cta: "Create Image", emoji: "🖼️" },
  { id: "music", label: "Music", assetType: "MUSIC", generator: "music", empty: "No music yet.", cta: "Create Music", emoji: "🎵" },
  { id: "voice", label: "Voice", assetType: "VOICE", generator: "voice", empty: "No voice tracks yet.", cta: "Create Voice", emoji: "🎤" },
  { id: "lyrics", label: "Lyrics", assetType: "LYRICS", generator: "lyrics", empty: "No lyrics yet.", cta: "Create Lyrics", emoji: "✍️" },
  { id: "face", label: "Face", assetType: "FACE", generator: "face_replacement", empty: "No face replacements yet.", cta: "Create Face Replacement", emoji: "👤" },
  { id: "avatars", label: "Avatars", assetType: "AVATAR", generator: "ai_avatar", empty: "No avatars yet.", cta: "Create Avatar", emoji: "🧑" },
];
const TAB_ORDER = ["overview", "assets", "story", "script", "movie", "videos", "images", "music", "voice", "lyrics", "face", "characters", "avatars", "references", "generations"];
const COUNT_KEY: Record<string, string> = { movie: "scenes", story: "story", script: "script", videos: "video", images: "image", music: "music", voice: "voice", lyrics: "lyrics", characters: "characters", face: "face", avatars: "avatar", references: "references" };

export default function ProjectWorkspace() {
  const { projectId } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const { isAssetTypeEnabled } = useFeatures();
  const { data: project, setData, loading, error, reload } = useAsync(() => api<ProjectDetail>(`/api/projects/${projectId}`), [projectId]);
  // Tabs of features an administrator switched off are hidden, but never when the project already holds assets of that kind: existing work stays reachable.
  const visibleTabs = TAB_ORDER.filter((id) => { const s = SECTIONS.find((x) => x.id === id); return !s?.assetType || isAssetTypeEnabled(s.assetType) || (project?.counts[COUNT_KEY[id]] ?? 0) > 0; });
  const tab = visibleTabs.includes(params.get("tab") ?? "") ? params.get("tab")! : "overview";
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (loading && !project) return <PageLoader />;
  if (error || !project) return (
    <div className="space-y-4"><Link to="/projects" className="btn-ghost -ml-3"><ArrowLeft className="h-4 w-4" /> Projects</Link>
      <ErrorState message={error ?? "Project not found."} onRetry={reload} /></div>
  );

  const remove = async () => {
    setBusy(true);
    try { await api(`/api/projects/${project.id}`, { method: "DELETE" }); nav("/projects", { replace: true }); }
    catch (e) { setErr(errorMessage(e)); setDeleting(false); } finally { setBusy(false); }
  };
  const tabs = visibleTabs.map((id) => ({ id, label: id === "overview" ? "Overview" : id[0].toUpperCase() + id.slice(1), badge: project.counts[COUNT_KEY[id]] }));

  return (
    <div>
      <Link to="/projects" className="btn-ghost -ml-3 mb-2"><ArrowLeft className="h-4 w-4" aria-hidden /> Projects</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-accent">{project.genre || "Project"}</p>
          <h1 className="mt-1 break-words text-3xl font-bold uppercase tracking-wide sm:text-4xl">{project.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted"><StatusBadge status={project.status} /> Updated {timeAgo(project.updated_at)}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" aria-hidden /> Edit</button>
          <button className="btn-secondary text-danger" onClick={() => setDeleting(true)}><Trash2 className="h-4 w-4" aria-hidden /> Delete</button>
        </div>
      </div>
      {err && <div className="mb-4"><Alert kind="error">{err}</Alert></div>}
      <Tabs label="Project sections" tabs={tabs} active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })} />
      <TabPanel id={tab}>
        {tab === "overview" && (
          <div className="grid gap-4 lg:grid-cols-3">
            <section className="card p-5 lg:col-span-2"><h2 className="mb-2 text-lg font-semibold">About</h2>
              <p className="whitespace-pre-wrap text-sm text-muted">{project.description || "No description yet. Use Edit to add one."}</p>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                <div><dt className="text-muted">Created</dt><dd>{formatDate(project.created_at)}</dd></div>
                <div><dt className="text-muted">Status</dt><dd>{project.status.replace("_", " ").toLowerCase()}</dd></div>
                <div><dt className="text-muted">Genre</dt><dd>{project.genre || "—"}</dd></div>
              </dl></section>
            <section className="card p-5"><h2 className="mb-2 text-lg font-semibold">Contents</h2>
              <ul className="space-y-1.5 text-sm">{tabs.filter((t) => t.id !== "overview").map((t) => (
                <li key={t.id}><button className="flex w-full justify-between hover:text-accent" onClick={() => setParams({ tab: t.id })}><span>{t.label}</span><span className="text-muted">{t.badge ?? 0}</span></button></li>))}</ul></section>
          </div>)}
        {SECTIONS.filter((s) => s.id === tab).map((s) => <AssetSection key={s.id} projectId={project.id} section={s} />)}
        {tab === "movie" && <Movie projectId={project.id} onChanged={reload} />}
        {tab === "characters" && <Characters projectId={project.id} onChanged={reload} />}
        {tab === "assets" && <AllAssets projectId={project.id} />}
        {tab === "generations" && <Generations projectId={project.id} />}
        {tab === "references" && <References project={project} onChanged={reload} />}
      </TabPanel>
      <ProjectFormModal open={editing} project={project} onClose={() => setEditing(false)} onSaved={(p) => { setData(p); setEditing(false); }} />
      <ConfirmDialog open={deleting} danger busy={busy} title="Delete project?" confirmLabel="Delete project" onClose={() => setDeleting(false)} onConfirm={remove}
        message={`"${project.title}" and everything in it will be permanently deleted.`} />
    </div>
  );
}
