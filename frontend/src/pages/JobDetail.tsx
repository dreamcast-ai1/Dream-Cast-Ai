import { ArrowLeft, Download, Pencil, RefreshCw, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { JobStatus } from "../components/JobStatus";
import { AuthImage } from "../components/ui/AuthImage";
import { AuthMedia } from "../components/ui/AuthMedia";
import { VideoPlayer } from "../components/VideoPlayer";
import { ConfirmDialog } from "../components/ui/Modal";
import { Alert, ErrorState, PageLoader, StatusBadge } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import { usePolling } from "../hooks/usePolling";
import { api, downloadAsset, downloadFile, errorMessage } from "../lib/api";
import { formatDate, timeAgo, titleCase } from "../lib/format";
import { EMOJI, LABEL } from "../lib/generatorMeta";
import { ACTIVE_STATUSES, type Job } from "../lib/types";

const when = (iso: string | null) => (iso ? `${formatDate(iso)} · ${new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "—");

export default function JobDetail() {
  const { jobId } = useParams();
  const nav = useNavigate();
  const { data: job, setData, loading, error, reload } = useAsync(() => api<Job>(`/api/jobs/${jobId}`), [jobId]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const active = !!job && ACTIVE_STATUSES.includes(job.status);
  usePolling(async () => { try { setData(await api<Job>(`/api/jobs/${jobId}`)); } catch { /* keep showing the last state */ } }, active, 4000);

  if (loading && !job) return <PageLoader />;
  if (error || !job) return <div className="space-y-4"><Link to="/history" className="btn-ghost -ml-3"><ArrowLeft className="h-4 w-4" /> History</Link><ErrorState message={error ?? "Generation not found."} onRetry={reload} /></div>;

  const act = async (name: string, fn: () => Promise<void>) => { setBusy(name); setErr(""); try { await fn(); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(""); } };
  const regenerate = () => act("regen", async () => { const r = await api<{ job_id: string }>(`/api/jobs/${job.id}/regenerate`, { method: "POST" }); nav(`/history/${r.job_id}`); });
  const cancel = () => act("cancel", async () => setData(await api<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" })));
  const remove = () => act("delete", async () => { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); nav("/history", { replace: true }); });
  const optionRows = Object.entries(job.options).filter(([k]) => k !== "character_ids" && !k.endsWith("_custom")).map(([k, v]) => [titleCase(k), k === "duration_seconds" ? `${v} seconds` : String(v)]);
  const file = job.assets.find((a) => a.url);
  const text = (job.output.text as string | undefined) ?? job.assets.find((a) => a.text_preview)?.text_preview;

  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/history" className="btn-ghost -ml-3 mb-2"><ArrowLeft className="h-4 w-4" aria-hidden /> History</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-accent"><span aria-hidden>{EMOJI[job.type]}</span> {LABEL[job.type] ?? job.type} generation</p>
          <h1 className="mt-1 break-words text-2xl font-bold sm:text-3xl">“{job.original_prompt || job.refined_prompt.slice(0, 80)}”</h1>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted"><StatusBadge status={job.status} />{timeAgo(job.created_at)}{job.simulated && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-xs text-warn">Simulated</span>}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          {active && <button className="btn-secondary" onClick={cancel} disabled={busy !== "" || job.cancel_requested}><XCircle className="h-4 w-4" aria-hidden />{job.cancel_requested ? "Cancelling…" : "Cancel"}</button>}
          {!active && <button className="btn-primary" onClick={regenerate} disabled={busy !== ""}><RefreshCw className="h-4 w-4" aria-hidden />{job.status === "FAILED" ? "Retry" : "Regenerate"}</button>}
          <Link className="btn-secondary" to={`/create/${job.type}?from=${job.id}`}><Pencil className="h-4 w-4" aria-hidden /> Edit prompt</Link>
          {!active && <button className="btn-secondary text-danger" onClick={() => setConfirmDelete(true)} disabled={busy !== ""}><Trash2 className="h-4 w-4" aria-hidden /> Delete</button>}
        </div>
      </div>
      {err && <div className="mb-4"><Alert kind="error">{err}</Alert></div>}
      {job.status === "CANCELLED" && job.output.cancellation === "stopped_locally" && <div className="mb-4"><Alert kind="info">Stopped on DreamCast's side. This provider can't be told to cancel, so it may still finish the request remotely.</Alert></div>}
      {job.cancel_requested && active && <div className="mb-4"><Alert kind="info">Cancellation requested. The worker will stop this generation shortly.</Alert></div>}

      <div className="grid gap-4">
        <section className="card p-5" aria-label="Status"><JobStatus job={job} title="Progress" type={job.type} />
          {job.status === "COMPLETED" && <p className="mt-3 text-sm text-success">Your {LABEL[job.type]?.toLowerCase()} generation is complete.</p>}</section>

        {(text || file) && (
          <section className="card p-5" aria-label="Result"><h2 className="mb-3 text-lg font-semibold">Result</h2>
            {job.simulated && <div className="mb-3"><Alert kind="info">This is simulated output from the development simulator — no real content was generated.</Alert></div>}
            {file && (job.type === "video" ? <VideoPlayer assetId={file.id} thumbnail={file.thumbnail_url} title="Generated video" /> : job.type === "music" || job.type === "voice" ? <AuthMedia src={file.url!} kind="audio" label="Generated audio" /> : <AuthImage src={file.url} alt="Generated result" className="w-full rounded-lg" />)}
            {text && <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-raised p-3 font-sans text-sm">{text}</pre>}
            <div className="mt-3 flex flex-wrap gap-2">
              {file && <button className="btn-secondary" onClick={() => (job.type === "video" || job.type === "face_replacement" ? downloadAsset(file.id) : downloadFile(`/api/assets/${file.id}/download`, `${job.type}-${job.id.slice(0, 8)}`))}><Download className="h-4 w-4" aria-hidden /> Download</button>}
              {job.project_id && job.assets[0] && <Link className="btn-primary" to={`/projects/${job.project_id}/assets/${job.assets[0].id}`}>Open {LABEL[job.type]?.toLowerCase()}{job.type !== "video" ? " (view, edit, download)" : ""}</Link>}
              {job.project_id && <Link className="btn-secondary" to={`/projects/${job.project_id}`}>Open project</Link>}
            </div></section>)}

        <section className="card p-5" aria-label="Prompts"><h2 className="mb-3 text-lg font-semibold">Prompts</h2>
          <h3 className="font-sans text-xs font-semibold uppercase tracking-wider text-muted">Original</h3><p className="mb-4 mt-1 whitespace-pre-wrap text-sm">{job.original_prompt || "—"}</p>
          <h3 className="font-sans text-xs font-semibold uppercase tracking-wider text-muted">Refined (sent to the provider)</h3><p className="mt-1 whitespace-pre-wrap text-sm">{job.refined_prompt}</p></section>

        <section className="card p-5" aria-label="Details"><h2 className="mb-3 text-lg font-semibold">Details</h2>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <div><dt className="text-muted">Generator</dt><dd>{LABEL[job.type] ?? job.type}</dd></div>
            <div><dt className="text-muted">Project</dt><dd>{job.project_id ? <Link className="text-accent hover:underline" to={`/projects/${job.project_id}`}>{job.project_title ?? "Open project"}</Link> : "None"}</dd></div>
            <div><dt className="text-muted">Provider</dt><dd>{job.provider ?? "Not assigned yet"}</dd></div>
            <div><dt className="text-muted">Attempts</dt><dd>{job.attempts}</dd></div>
            <div><dt className="text-muted">Created</dt><dd>{when(job.created_at)}</dd></div>
            <div><dt className="text-muted">Completed</dt><dd>{when(job.completed_at)}</dd></div>
            {optionRows.map(([k, v]) => <div key={k}><dt className="text-muted">{k}</dt><dd>{v}</dd></div>)}
            {job.parent_id && <div><dt className="text-muted">Regenerated from</dt><dd><Link className="text-accent hover:underline" to={`/history/${job.parent_id}`}>Previous generation</Link></dd></div>}
          </dl>
          {job.error_code && <div className="mt-4"><Alert kind="error"><strong>{titleCase(job.error_code)}</strong>{job.error_message ? ` — ${job.error_message}` : ""}</Alert></div>}
        </section>
      </div>
      <ConfirmDialog open={confirmDelete} danger busy={busy === "delete"} title="Delete generation?" confirmLabel="Delete" onClose={() => setConfirmDelete(false)} onConfirm={remove}
        message="This removes it from your history. Any result already saved in a project stays there." />
    </div>
  );
}
