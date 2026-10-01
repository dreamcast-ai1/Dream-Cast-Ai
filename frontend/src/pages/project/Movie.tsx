import { ChevronDown, ChevronUp, Clapperboard, Download, FileText, Film, Pencil, Plus, Trash2, Video } from "lucide-react";
import { useState } from "react";
import { VideoPlayer } from "../../components/VideoPlayer";
import { SelectField, TextArea, TextField } from "../../components/ui/Field";
import { ConfirmDialog, Modal } from "../../components/ui/Modal";
import { Alert, EmptyState, ErrorState, PageLoader, Spinner, StatusBadge } from "../../components/ui/feedback";
import { useAsync } from "../../hooks/useAsync";
import { usePolling } from "../../hooks/usePolling";
import { api, downloadAsset, errorMessage } from "../../lib/api";
import type { Asset, Character, MovieState, Scene } from "../../lib/types";

const pad = (n: number) => String(n).padStart(2, "0");
const STAGE_WORDS: Record<string, string> = { QUEUED: "Queued", PREPARING: "Preparing", ASSEMBLING: "Assembling", FINALIZING: "Finalizing", STORING: "Finalizing", PROCESSING: "Preparing" };
const STATUS_LABEL: Record<string, string> = { DRAFT: "Not generated", GENERATING: "Generating", READY: "Ready", FAILED: "Failed" };

interface Form { title: string; description: string; script: string; visual_prompt: string; duration_seconds: string; character_ids: string[] }
const blank: Form = { title: "", description: "", script: "", visual_prompt: "", duration_seconds: "10", character_ids: [] };

function SceneForm({ projectId, scene, onClose, onSaved }: { projectId: string; scene: Scene | null; onClose: () => void; onSaved: (notes: string[]) => void }) {
  const [f, setF] = useState<Form>(scene ? { title: scene.title, description: scene.description, script: scene.script, visual_prompt: scene.visual_prompt, duration_seconds: String(scene.duration_seconds), character_ids: scene.character_ids } : blank);
  const chars = useAsync(() => api<Character[]>(`/api/projects/${projectId}/characters`), [projectId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const toggle = (id: string) => setF({ ...f, character_ids: f.character_ids.includes(id) ? f.character_ids.filter((c) => c !== id) : [...f.character_ids, id] });
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const body = { ...f, duration_seconds: Number(f.duration_seconds) };
      const res = scene ? await api<Scene>(`/api/projects/${projectId}/scenes/${scene.id}`, { method: "PATCH", json: body }) : await api<Scene>(`/api/projects/${projectId}/scenes`, { method: "POST", json: body });
      onSaved(res.notes ?? []);
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };
  return (
    <Modal open title={scene ? `Edit Scene ${pad(scene.number)}` : "Add a scene"} onClose={onClose}
      footer={<><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button form="scene-form" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save scene"}</button></>}>
      <form id="scene-form" onSubmit={save} className="space-y-3">
        {error && <Alert kind="error">{error}</Alert>}
        <TextField label="Title" value={f.title} onChange={set("title")} maxLength={200} placeholder="e.g. The warrior reaches the gate" />
        <TextArea label="Description" value={f.description} onChange={set("description")} maxLength={4000} />
        <TextArea label="Visual prompt" value={f.visual_prompt} onChange={set("visual_prompt")} maxLength={4000} hint="What the camera should show. This is what the video is made from." />
        <TextArea label="Script" value={f.script} onChange={set("script")} maxLength={20000} />
        <SelectField label="Clip length" value={f.duration_seconds} onChange={set("duration_seconds")}>
          {["10", "20", "30"].map((d) => <option key={d} value={d}>{d} seconds</option>)}</SelectField>
        <p className="-mt-2 text-xs text-muted">A single scene clip is at most 30 seconds. Your finished movie can be much longer.</p>
        {chars.data && chars.data.length > 0 && (
          <fieldset><legend className="mb-1.5 text-sm font-medium">Characters in this scene</legend>
            <div className="flex flex-wrap gap-2">{chars.data.map((c) => (
              <label key={c.id} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-sm">
                <input type="checkbox" checked={f.character_ids.includes(c.id)} onChange={() => toggle(c.id)} />{c.name}</label>))}</div></fieldset>)}
      </form>
    </Modal>
  );
}

function ImportScript({ projectId, hasScenes, onClose, onDone }: { projectId: string; hasScenes: boolean; onClose: () => void; onDone: () => void }) {
  const scripts = useAsync(() => api<Asset[]>(`/api/projects/${projectId}/assets?type=SCRIPT`), [projectId]);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const go = async () => {
    setBusy(true); setError("");
    try { await api(`/api/projects/${projectId}/scenes/from-script`, { method: "POST", json: { script_asset_id: pick || scripts.data![0].id, replace: hasScenes } }); onDone(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <Modal open title="Create scenes from a script" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !scripts.data?.length} onClick={go}>{busy ? "Creating…" : "Create scenes"}</button></>}>
      {error && <div className="mb-3"><Alert kind="error">{error}</Alert></div>}
      {scripts.loading ? <PageLoader /> : !scripts.data?.length ? <p className="text-sm text-muted">This project has no script yet. Create one in the Script tab first.</p> : (
        <div className="space-y-3">
          <SelectField label="Script" value={pick || scripts.data[0].id} onChange={(e) => setPick(e.target.value)}>{scripts.data.map((s) => <option key={s.id} value={s.id}>{s.title} (v{s.version})</option>)}</SelectField>
          <p className="text-sm text-muted">Each scene in the script becomes a scene here. This is free: no video is generated until you press Generate Video.</p>
          {hasScenes && <Alert kind="info">Your current scenes will be replaced. Videos you already generated stay in the Videos tab.</Alert>}
        </div>)}
    </Modal>
  );
}

function SceneCard({ scene, busy, first, last, onMove, onEdit, onDelete, onGenerate }: { scene: Scene; busy: boolean; first: boolean; last: boolean; onMove: (delta: number) => void; onEdit: () => void; onDelete: () => void; onGenerate: () => void }) {
  const working = scene.status === "GENERATING";
  return (
    <li className="card p-4" aria-label={`Scene ${pad(scene.number)}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-accent">Scene {pad(scene.number)}</p>
          <h3 className="mt-0.5 break-words text-base font-semibold">{scene.title || "Untitled scene"}</h3>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-ghost !p-1.5" onClick={() => onMove(-1)} disabled={first || busy} aria-label={`Move scene ${pad(scene.number)} up`}><ChevronUp className="h-4 w-4" aria-hidden /></button>
          <button className="btn-ghost !p-1.5" onClick={() => onMove(1)} disabled={last || busy} aria-label={`Move scene ${pad(scene.number)} down`}><ChevronDown className="h-4 w-4" aria-hidden /></button>
          <StatusBadge status={scene.status} /><span className="text-xs text-muted">{STATUS_LABEL[scene.status]} · {scene.duration_seconds}s</span></div>
      </div>
      {scene.description && <p className="mt-2 line-clamp-3 text-sm text-muted">{scene.description}</p>}
      {scene.characters.length > 0 && <p className="mt-1 text-xs text-muted">Characters: {scene.characters.join(", ")}</p>}
      {scene.status === "FAILED" && scene.job?.error_message && <p className="mt-2 text-sm text-danger">{scene.job.error_message}</p>}
      {scene.video && <div className="mt-3 max-w-md"><VideoPlayer assetId={scene.video.asset_id} thumbnail={scene.video.thumbnail_url} title={`Scene ${pad(scene.number)}`} /></div>}
      {working && <p className="mt-3 flex items-center gap-2 text-sm text-muted"><Spinner className="!h-4 !w-4" />Creating this scene… {STAGE_WORDS[scene.job?.stage ?? ""] ?? ""}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-primary" disabled={working || busy} onClick={onGenerate}><Video className="h-4 w-4" aria-hidden />{scene.video ? "Generate again" : "Generate Video"}</button>
        <button className="btn-secondary" onClick={onEdit} disabled={working}><Pencil className="h-4 w-4" aria-hidden />Edit</button>
        <button className="btn-ghost text-danger" onClick={onDelete} disabled={working} aria-label={`Delete scene ${pad(scene.number)}`}><Trash2 className="h-4 w-4" aria-hidden /></button>
      </div>
    </li>
  );
}

/** The project's movie: scenes in order, a Generate Video button per scene, and Assemble Movie once every scene has a clip. */
export function Movie({ projectId, onChanged }: { projectId: string; onChanged: () => void }) {
  const scenes = useAsync(() => api<{ items: Scene[] }>(`/api/projects/${projectId}/scenes`), [projectId]);
  const movie = useAsync(() => api<MovieState>(`/api/projects/${projectId}/movie`), [projectId]);
  const [editing, setEditing] = useState<Scene | "new" | null>(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState<Scene | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "success" | "error" | "info"; text: string } | null>(null);
  const [wasAssembling, setWasAssembling] = useState(false);

  const items = scenes.data?.items ?? [];
  const m = movie.data;
  const working = items.some((s) => s.status === "GENERATING") || !!m?.active_job;
  const refresh = async () => {
    await Promise.all([scenes.reload(), movie.reload()]);
    onChanged();
  };
  // Reload quietly while something is running, so the page keeps up even if you stay here.
  usePolling(async () => {
    const [s, st] = await Promise.all([api<{ items: Scene[] }>(`/api/projects/${projectId}/scenes`), api<MovieState>(`/api/projects/${projectId}/movie`)]);
    scenes.setData(s); movie.setData(st);
    if (wasAssembling && !st.active_job) { setWasAssembling(false); setMsg(st.movie ? { kind: "success", text: "Your movie is ready." } : { kind: "error", text: "Your movie couldn't be assembled. Check your notifications for the reason." }); }
  }, working, 4000);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg({ kind: "info", text: ok }); await refresh(); } catch (e) { setMsg({ kind: "error", text: errorMessage(e) }); await refresh().catch(() => undefined); } finally { setBusy(false); }
  };

  if ((scenes.loading || movie.loading) && !scenes.data) return <PageLoader />;
  if (scenes.error || movie.error) return <ErrorState message={(scenes.error || movie.error)!} onRetry={() => void refresh()} />;
  const stage = m?.active_job ? STAGE_WORDS[m.active_job.stage] ?? "Working" : "";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="flex items-center gap-2 text-lg font-semibold uppercase tracking-wide"><Clapperboard className="h-5 w-5 text-accent" aria-hidden />Movie</h2>
          <p className="text-sm text-muted">Story → Script → Scenes → a video for each scene → your movie.{items.length > 0 && m && m.total_seconds > 0 ? ` Scenes ready so far: ${Math.round(m.total_seconds)} seconds.` : ""}</p></div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => setImporting(true)}><FileText className="h-4 w-4" aria-hidden />From script</button>
          <button className="btn-secondary" onClick={() => setEditing("new")}><Plus className="h-4 w-4" aria-hidden />Add scene</button>
        </div>
      </div>
      {msg && <div role="status"><Alert kind={msg.kind}>{msg.text}</Alert></div>}

      {items.length === 0 ? <EmptyState icon={<Film className="h-6 w-6" />} title="No scenes yet." hint="Add scenes one by one, or create them from a script. Writing scenes is free." /> : (
        <ol className="space-y-3">{items.map((s, i) => (
          <SceneCard key={s.id} scene={s} busy={busy} first={i === 0} last={i === items.length - 1}
            onMove={(d) => act(() => api(`/api/projects/${projectId}/scenes/${s.id}`, { method: "PATCH", json: { number: s.number + d } }))} onEdit={() => setEditing(s)} onDelete={() => setDeleting(s)}
            onGenerate={() => act(() => api(`/api/projects/${projectId}/scenes/${s.id}/generate-video`, { method: "POST", json: {} }), `Making Scene ${pad(s.number)}. This uses one video generation.`)} />))}</ol>)}

      {items.length > 0 && m && (
        <section className="card p-5" aria-label="Assemble movie">
          <h3 className="text-base font-semibold">Assemble Movie</h3>
          {!m.can_assemble && <ul className="mt-2 space-y-1 text-sm text-muted">{m.missing.map((t) => <li key={t}>{t}</li>)}</ul>}
          {m.active_job ? <p className="mt-3 flex items-center gap-2 text-sm"><Spinner className="!h-4 !w-4" />{stage}… You can leave this page; we'll tell you when your movie is ready.</p> : (
            <div className="mt-3"><button className="btn-primary" disabled={!m.can_assemble || busy}
              onClick={() => act(async () => { await api(`/api/projects/${projectId}/movie/assemble`, { method: "POST" }); setWasAssembling(true); }, "Assembling your movie. This doesn't use any video generations.")}>
              <Film className="h-4 w-4" aria-hidden />{m.movie ? "Assemble again" : "Assemble Movie"}</button></div>)}
        </section>)}

      {m?.movie && (
        <section className="card p-5" aria-label="Final movie">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="text-base font-semibold">Your movie is ready</h3>
            <button className="btn-secondary" onClick={() => void downloadAsset(m.movie!.id).catch((e) => setMsg({ kind: "error", text: errorMessage(e) }))}><Download className="h-4 w-4" aria-hidden />Download</button></div>
          <div className="max-w-3xl"><VideoPlayer assetId={m.movie.id} thumbnail={m.movie.thumbnail_url} title={m.movie.title} /></div>
          {m.movie.duration_seconds != null && <p className="mt-2 text-xs text-muted">{Math.round(m.movie.duration_seconds)} seconds · version {m.movie.version}</p>}
        </section>)}

      {editing && <SceneForm projectId={projectId} scene={editing === "new" ? null : editing} onClose={() => setEditing(null)}
        onSaved={(notes) => { setEditing(null); setMsg(notes.length ? { kind: "info", text: notes.join(" ") } : null); void refresh(); }} />}
      {importing && <ImportScript projectId={projectId} hasScenes={items.length > 0} onClose={() => setImporting(false)} onDone={() => { setImporting(false); void refresh(); }} />}
      <ConfirmDialog open={!!deleting} danger busy={busy} title="Delete scene?" confirmLabel="Delete scene" onClose={() => setDeleting(null)}
        message={deleting ? `Scene ${pad(deleting.number)} will be removed from your movie. Videos already generated stay in the Videos tab.` : ""}
        onConfirm={() => { const s = deleting!; setDeleting(null); void act(() => api(`/api/projects/${projectId}/scenes/${s.id}`, { method: "DELETE" })); }} />
    </div>
  );
}
