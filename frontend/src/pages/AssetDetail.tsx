import { ArrowLeft, Check, Copy, Download, GitCompare, Pencil, RefreshCw, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AuthImage } from "../components/ui/AuthImage";
import { AuthMedia } from "../components/ui/AuthMedia";
import { VideoPlayer } from "../components/VideoPlayer";
import { ConfirmDialog } from "../components/ui/Modal";
import { Menu } from "../components/ui/Menu";
import { Alert, ErrorState, PageLoader, StatusBadge } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import type { Reference } from "../lib/types";
import { api, downloadAsset, downloadFile, errorMessage } from "../lib/api";
import { ASSET_EMOJI, ASSET_LABEL, formatDuration, scenesOf, slug } from "../lib/assetText";
import { formatDate, timeAgo } from "../lib/format";
import type { AssetDetail as Detail } from "../lib/types";

function Document({ text, script }: { text: string; script: boolean }) {
  return <pre className={`max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-raised p-4 leading-relaxed sm:p-6 ${script ? "font-mono text-[13px]" : "font-sans text-[15px]"}`}>{text}</pre>;
}

/** The input files behind a generation (image-to-video source, or face-replacement source and face), shown small. */
function SourceThumbs({ projectId, ids, labels }: { projectId: string; ids: string[]; labels: string[] }) {
  const { data } = useAsync(() => api<Reference[]>(`/api/projects/${projectId}/references`), [projectId]);
  const items = ids.map((id, i) => ({ ref: data?.find((r) => r.id === id), label: labels[i] })).filter((x) => x.ref && x.ref.mime_type.startsWith("image/"));
  if (!items.length) return null;
  return <div className="mt-3 flex flex-wrap gap-3">{items.map(({ ref, label: l }) => (
    <figure key={ref!.id} className="w-24"><div className="aspect-square overflow-hidden rounded-lg bg-raised"><AuthImage src={ref!.url} alt={`${l}: ${ref!.name}`} className="h-full w-full object-cover" lazy /></div>
      <figcaption className="mt-1 text-xs text-muted">{l}</figcaption></figure>))}</div>;
}

/** Story -> Video: choose one section (never the whole story); Create opens with that context and waits for confirmation. */
function StoryVideo({ projectId, storyId }: { projectId: string; storyId: string }) {
  const { data } = useAsync(() => api<{ sections: { key: string }[] }>(`/api/assets/${storyId}/sections`), [storyId]);
  const [section, setSection] = useState("");
  if (!data?.sections.length) return null;
  const chosen = section || data.sections.find((s) => s.key.startsWith("ACT"))?.key || data.sections[0].key;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="story-section">Story section</label>
      <select id="story-section" className="field !w-auto !py-1.5" value={chosen} onChange={(e) => setSection(e.target.value)}>{data.sections.map((s) => <option key={s.key} value={s.key}>{s.key}</option>)}</select>
      <Link className="btn-secondary" to={`/create/video?project=${projectId}&story=${storyId}&section=${encodeURIComponent(chosen)}`}>Create Video</Link>
    </span>
  );
}

export default function AssetDetail() {
  const { projectId, assetId } = useParams();
  const nav = useNavigate();
  const { data: a, setData, loading, error, reload } = useAsync(() => api<Detail>(`/api/assets/${assetId}`), [assetId]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ kind: "success" | "error" | "info"; text: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [compareId, setCompareId] = useState("");
  const other = useAsync(() => (compareId ? api<Detail>(`/api/assets/${compareId}`) : Promise.resolve(null)), [compareId]);

  useEffect(() => { setEditing(false); setCompareId(""); setMsg(null); }, [assetId]);
  if (loading && !a) return <PageLoader />;
  if (error || !a) return <div className="space-y-4"><Link to={`/projects/${projectId}`} className="btn-ghost -ml-3"><ArrowLeft className="h-4 w-4" /> Project</Link><ErrorState message={error ?? "Asset not found."} onRetry={reload} /></div>;

  const isText = a.text_content !== null && !a.has_file;
  const label = ASSET_LABEL[a.type] ?? a.type;
  const scenes = a.type === "SCRIPT" ? scenesOf(a) : [];
  const opts = a.meta.options as Record<string, unknown> | undefined ?? {};
  const act = async (name: string, fn: () => Promise<void>) => { setBusy(name); setMsg(null); try { await fn(); } catch (e) { setMsg({ kind: "error", text: errorMessage(e) }); } finally { setBusy(""); } };

  const save = () => act("save", async () => { setData(await api<Detail>(`/api/assets/${a.id}`, { method: "PUT", json: { text_content: draft } })); setEditing(false); setMsg({ kind: "success", text: "Saved. No AI was used for your edits." }); });
  const copy = () => act("copy", async () => {
    try { await navigator.clipboard.writeText(a.text_content ?? ""); }
    catch { const t = document.createElement("textarea"); t.value = a.text_content ?? ""; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
    setMsg({ kind: "success", text: `${label} copied to the clipboard.` });
  });
  const regenerate = () => act("regen", async () => {
    const r = await api<{ job_id: string }>(`/api/assets/${a.id}/regenerate`, { method: "POST" });
    setMsg({ kind: "info", text: "Regeneration started. A new version will be added when it's done; this version is kept." }); void r;
  });
  const duplicate = () => act("dup", async () => { const c = await api<Detail>(`/api/assets/${a.id}/duplicate`, { method: "POST" }); nav(`/projects/${projectId}/assets/${c.id}`); });
  const remove = () => act("delete", async () => { await api(`/api/assets/${a.id}`, { method: "DELETE" }); nav(`/projects/${projectId}?tab=${({ VIDEO: "videos", IMAGE: "images" } as Record<string, string>)[a.type] ?? a.type.toLowerCase()}`, { replace: true }); });
  const download = (fmt?: string) => act("dl", async () => {
    if (a.type === "VIDEO" || a.type === "FACE") { await downloadAsset(a.id); return; }     // large files stream via a signed URL
    await downloadFile(`/api/assets/${a.id}/download${fmt ? `?format=${fmt}` : ""}`, `${slug(a.title)}-${a.type.toLowerCase()}-v${a.version}.${fmt ?? a.format}`);
  });

  const details: [string, string][] = [["Provider", a.provider ?? "—"], ["Language", a.language || "—"], ["Created", `${formatDate(a.created_at)} (${timeAgo(a.created_at)})`],
    ...(a.type === "MUSIC" ? [["Genre", String(opts.genre_custom ?? opts.genre ?? "—")], ["Mood", String(opts.mood ?? "—")], ["Duration", formatDuration(a.duration_seconds) || "—"]] as [string, string][] : []),
    ...(a.type === "VOICE" ? [["Gender", String(a.meta.gender ?? "—")], ["Accent", String(a.meta.accent ?? "—")], ["Emotion", String(a.meta.emotion ?? "—")], ["Voice", String(a.meta.voice ?? "—")]] as [string, string][] : []),
    ...(a.type === "VIDEO" ? [["Duration", formatDuration(a.duration_seconds) || "—"], ["Resolution", a.meta.width ? `${a.meta.width}×${a.meta.height}` : "—"],
      ["Aspect ratio", String(a.meta.aspect_ratio ?? opts.aspect_ratio ?? "—")], ["Method", String(a.meta.method ?? opts.method ?? "Text to Video")], ["Style", String(opts.style_custom ?? opts.style ?? "—")]] as [string, string][] : []),
    ...(a.type === "IMAGE" ? [["Resolution", a.meta.width ? `${a.meta.width}×${a.meta.height}` : "—"], ["Aspect ratio", String(a.meta.aspect_ratio ?? opts.aspect_ratio ?? "—")],
      ["Style", String(opts.style_custom ?? opts.style ?? "—")], ["Model", String(a.meta.model ?? "—")]] as [string, string][] : []),
    ...(a.type === "FACE" ? [["Kind", "Face replacement"]] as [string, string][] : []),
    ...(isText ? [["Words", String(a.meta.word_count ?? "—")]] as [string, string][] : [])];
  const sourceIds = [a.meta.source_asset_id, a.meta.face_asset_id].filter((x): x is string => typeof x === "string");
  const notes = (a.meta.notes as string[] | undefined) ?? [];

  return (
    <div className="mx-auto max-w-4xl">
      <Link to={`/projects/${projectId}`} className="btn-ghost -ml-3 mb-2"><ArrowLeft className="h-4 w-4" aria-hidden /> Project</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-accent"><span aria-hidden>{ASSET_EMOJI[a.type]}</span> {label}</p>
          <h1 className="mt-1 break-words text-2xl font-bold sm:text-3xl">{a.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
            <label className="flex items-center gap-1.5"><span className="sr-only">Version</span>
              <select className="field !w-auto !py-1" value={a.id} aria-label="Version" onChange={(e) => nav(`/projects/${projectId}/assets/${e.target.value}`)}>
                {a.versions.map((v) => <option key={v.id} value={v.id}>{label} v{v.version}</option>)}</select></label>
            {a.status === "SIMULATED" && <StatusBadge status="SIMULATED" />}{a.meta.edited === true && <span>edited</span>}
            {a.versions.length > 1 && isText && <button className="btn-ghost !py-1" aria-pressed={!!compareId} onClick={() => setCompareId(compareId ? "" : a.versions.find((v) => v.id !== a.id)!.id)}><GitCompare className="h-4 w-4" aria-hidden /> Compare</button>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {isText && !editing && <button className="btn-secondary" onClick={() => { setDraft(a.text_content ?? ""); setEditing(true); }}><Pencil className="h-4 w-4" aria-hidden /> Edit</button>}
          {isText && <button className="btn-secondary" onClick={copy}><Copy className="h-4 w-4" aria-hidden /> Copy</button>}
          {isText ? <Menu label="Download" triggerClass="btn-secondary" trigger={<span className="flex items-center gap-2 text-sm"><Download className="h-4 w-4" aria-hidden /> Download</span>} items={[{ label: "Plain text (.txt)", onSelect: () => download("txt") }, { label: "Markdown (.md)", onSelect: () => download("md") }]} />
            : a.has_file && <button className="btn-secondary" onClick={() => download()}><Download className="h-4 w-4" aria-hidden /> Download</button>}
          <button className="btn-secondary" onClick={regenerate} disabled={busy !== "" || a.status === "SIMULATED"}><RefreshCw className="h-4 w-4" aria-hidden /> Regenerate</button>
          <button className="btn-secondary" onClick={duplicate} disabled={busy !== ""}><Copy className="h-4 w-4" aria-hidden /> Duplicate</button>
          <button className="btn-secondary text-danger" onClick={() => setConfirmDelete(true)} disabled={busy !== ""}><Trash2 className="h-4 w-4" aria-hidden /> Delete</button>
        </div>
      </div>
      {msg && <div className="mb-4"><Alert kind={msg.kind}>{msg.text} {msg.kind === "info" && <Link className="underline" to="/history">View history</Link>}</Alert></div>}

      {/* next-step hand-offs: each opens Create with context pre-filled; nothing is generated until the user confirms there */}
      <div className="mb-5 flex flex-wrap gap-2">
        {a.type === "STORY" && <Link className="btn-primary" to={`/create/script?project=${projectId}&story=${a.id}`}>Generate Script from Story</Link>}
        {a.type === "STORY" && <StoryVideo projectId={projectId!} storyId={a.id} />}
        {a.type === "LYRICS" && <Link className="btn-primary" to={`/create/music?project=${projectId}&lyrics=${a.id}`}>Generate Music</Link>}
        {isText && a.type !== "SCRIPT" && <Link className="btn-secondary" to={`/create/voice?project=${projectId}&source=${a.id}`}>Generate Voice</Link>}
        {a.type === "SCRIPT" && <Link className="btn-primary" to={`/create/voice?project=${projectId}&source=${a.id}`}>Generate Voice</Link>}
      </div>

      {a.status === "SIMULATED" && <div className="mb-4"><Alert kind="info">This entry was created by the development simulator. No real content was generated.</Alert></div>}

      <div className={`grid gap-4 ${scenes.length ? "lg:grid-cols-[1fr_16rem]" : ""}`}>
        <div className="min-w-0 space-y-4">
          {a.has_file && a.type === "VIDEO" && <section className="card p-4" aria-label="Video player"><VideoPlayer assetId={a.id} thumbnail={a.thumbnail_url} title={a.title} />
            <p className="mt-2 text-xs text-muted">Use the player controls to play, pause, seek, change volume and go fullscreen. {a.format.toUpperCase()} · {a.mime_type}</p></section>}
          {a.has_file && a.type === "IMAGE" && <section className="card p-4" aria-label="Generated image"><AuthImage src={a.url} alt={`Generated image: ${a.title}`} className="mx-auto max-h-[70vh] w-full rounded-lg object-contain" /></section>}
          {a.has_file && a.type === "FACE" && <section className="card p-4" aria-label="Result image"><AuthImage src={a.url} alt={`Face replacement result: ${a.title}`} className="mx-auto max-h-[70vh] w-full rounded-lg object-contain" /></section>}
          {a.has_file && (a.type === "MUSIC" || a.type === "VOICE") && <section className="card p-4" aria-label="Audio player"><AuthMedia src={a.url!} kind="audio" label={`${label}: ${a.title}`} />
            <p className="mt-2 text-xs text-muted">Use the player to play, pause and seek. {a.format.toUpperCase()} · {a.mime_type}</p></section>}
          {isText && (editing ? (
            <section className="card p-4" aria-label="Editor">
              <label htmlFor="editor" className="sr-only">Edit {label.toLowerCase()} text</label>
              <textarea id="editor" value={draft} onChange={(e) => setDraft(e.target.value)} className={`field min-h-[24rem] resize-y leading-relaxed ${a.type === "SCRIPT" ? "font-mono text-[13px]" : ""}`} />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button className="btn-primary" onClick={save} disabled={busy !== "" || draft === a.text_content}><Save className="h-4 w-4" aria-hidden /> Save</button>
                <button className="btn-secondary" onClick={() => setEditing(false)}><X className="h-4 w-4" aria-hidden /> Cancel</button>
                <span className="text-xs text-muted">Editing never calls the AI.{draft !== a.text_content ? " You have unsaved changes." : ""}</span>
              </div>
            </section>
          ) : compareId ? (
            <div className="grid gap-4 md:grid-cols-2">
              {[a, other.data].map((v, i) => (
                <section key={i} className="card min-w-0 p-3" aria-label={v ? `Version ${v.version}` : "Loading version"}>
                  {i === 1 && <select className="field mb-2" aria-label="Compare with" value={compareId} onChange={(e) => setCompareId(e.target.value)}>
                    {a.versions.filter((x) => x.id !== a.id).map((x) => <option key={x.id} value={x.id}>{label} v{x.version}</option>)}</select>}
                  {i === 0 && <p className="mb-2 py-1.5 text-sm font-medium">{label} v{a.version} (this version)</p>}
                  {v ? <Document text={v.text_content ?? ""} script={a.type === "SCRIPT"} /> : <div className="skeleton h-48" />}
                </section>))}
            </div>
          ) : <section aria-label={`${label} text`}><Document text={a.text_content ?? ""} script={a.type === "SCRIPT"} /></section>)}

          <section className="card p-4" aria-label="Details"><h2 className="mb-3 text-lg font-semibold">Details</h2>
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">{details.map(([k, v]) => <div key={k}><dt className="text-muted">{k}</dt><dd>{v}</dd></div>)}</dl>
            {sourceIds.length > 0 && <SourceThumbs projectId={a.project_id} ids={sourceIds} labels={a.type === "FACE" ? ["Source", "Face"] : ["Source image"]} />}
            {notes.length > 0 && <div className="mt-3 space-y-1.5">{notes.map((n) => <Alert key={n} kind="info">{n}</Alert>)}</div>}
            <details className="mt-4 text-sm"><summary className="cursor-pointer text-muted">Prompts used</summary>
              <p className="mt-2 text-xs font-semibold uppercase tracking-wider text-muted">Original</p><p className="whitespace-pre-wrap">{String(a.meta.original_prompt ?? "—")}</p>
              <p className="mt-3 text-xs font-semibold uppercase tracking-wider text-muted">Sent to the provider</p><p className="whitespace-pre-wrap">{a.prompt}</p></details>
            {a.job_id && <p className="mt-3 text-sm"><Link className="text-accent hover:underline" to={`/history/${a.job_id}`}>View the generation job</Link></p>}
          </section>
        </div>

        {scenes.length > 0 && (
          <aside aria-label="Scenes" className="card h-fit p-4"><h2 className="mb-2 text-lg font-semibold">Scenes</h2>
            <ol className="space-y-2 text-sm">{scenes.map((s) => (
              <li key={s.number} className="rounded-lg border border-border p-2">
                <p className="font-medium">Scene {s.number}</p><p className="truncate text-xs text-muted" title={s.heading}>{s.heading}</p>
                <Link className="btn-primary mt-2 w-full !py-1 text-xs" to={`/create/video?project=${projectId}&script=${a.id}&scene=${s.number}`}>Generate Video</Link>
                <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                  <Link className="text-accent hover:underline" to={`/create/voice?project=${projectId}&source=${a.id}&scene=${s.number}`}>Voice</Link>
                  <Link className="text-accent hover:underline" to={`/create/voice?project=${projectId}&source=${a.id}&scene=${s.number}&part=dialogue`}>Dialogue</Link>
                  <Link className="text-accent hover:underline" to={`/create/music?project=${projectId}&script=${a.id}&scene=${s.number}`}>Music</Link></p>
              </li>))}</ol>
            <p className="mt-3 flex items-start gap-1.5 text-xs text-muted"><Check className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />Scenes are detected from the text, so they update when you edit.</p>
          </aside>)}
      </div>
      <ConfirmDialog open={confirmDelete} danger busy={busy === "delete"} title={`Delete this ${label.toLowerCase()}?`} confirmLabel="Delete" onClose={() => setConfirmDelete(false)} onConfirm={remove}
        message={`"${a.title}" (v${a.version}) will be permanently deleted. Other versions are kept.`} />
    </div>
  );
}
