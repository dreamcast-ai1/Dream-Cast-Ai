import { Check, Copy, Download, ExternalLink, Pencil, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadAsset, downloadFile, errorMessage } from "../lib/api";
import { LABEL } from "../lib/generatorMeta";
import { ACTIVE_STATUSES, TEXT_ASSET_TYPES, type AssetDetail, type Job } from "../lib/types";
import { VideoPlayer } from "./VideoPlayer";
import { AuthImage } from "./ui/AuthImage";
import { AuthMedia } from "./ui/AuthMedia";
import { Alert, Spinner } from "./ui/feedback";

/** Shown while a generation runs: a quiet skeleton shaped like the result plus a slim progress bar. Replaced in place by the result. */
export function GenerationPlaceholder({ type }: { type: string }) {
  const tall = type === "video" || type === "image" || type === "face_replacement" || type === "movie";
  return (
    <div className="fade-in" aria-hidden>
      <div className={`skeleton w-full ${tall ? "aspect-video" : "h-24"}`} />
      <div className="progress-bar mt-3" />
    </div>
  );
}

/** Editable text result (story, script, lyrics): loaded in full from the saved asset. */
function TextResult({ assetId, fallback }: { assetId: string | null; fallback: string }) {
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [draft, setDraft] = useState(fallback);
  const [saved, setSaved] = useState("");
  const [msg, setMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!assetId) return;
    let alive = true;
    api<AssetDetail>(`/api/assets/${assetId}`).then((a) => { if (alive) { setAsset(a); setDraft(a.text_content ?? fallback); setSaved(a.text_content ?? fallback); } }).catch(() => undefined);
    return () => { alive = false; };
  }, [assetId, fallback]);
  const save = async () => {
    if (!assetId) return;
    setBusy(true); setMsg(null);
    try { const a = await api<AssetDetail>(`/api/assets/${assetId}`, { method: "PUT", json: { text_content: draft } }); setAsset(a); setSaved(a.text_content ?? draft); setMsg({ kind: "success", text: "Saved. No AI was used for your edits." }); }
    catch (e) { setMsg({ kind: "error", text: errorMessage(e) }); } finally { setBusy(false); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(draft); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="space-y-2">
      <label htmlFor="result-text" className="sr-only">Generated text</label>
      <textarea id="result-text" className="field min-h-[14rem] resize-y font-sans leading-relaxed" value={draft} readOnly={!assetId} onChange={(e) => setDraft(e.target.value)} rows={12} />
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <div className="flex flex-wrap gap-2">
        {assetId && <button className="btn-primary" onClick={save} disabled={busy || draft === saved || !asset}>{busy ? <Spinner /> : <Save className="h-4 w-4" aria-hidden />} Save edits</button>}
        <button className="btn-secondary" onClick={copy}>{copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}{copied ? "Copied" : "Copy"}</button>
      </div>
    </div>
  );
}

/** The finished result of a generation, shown in place with the relevant controls. Used by Create and by the generation page, so every
 *  generator shows its result the same way without anyone having to open another page. */
export function GenerationResult({ job, onRegenerate, regenerating }: { job: Job; onRegenerate?: () => void; regenerating?: boolean }) {
  if (ACTIVE_STATUSES.includes(job.status) || job.status !== "COMPLETED") return null;
  const label = LABEL[job.type] ?? job.type;
  const file = job.assets.find((a) => a.url);
  const textAsset = job.assets.find((a) => a.text_preview || TEXT_ASSET_TYPES.includes(a.type));
  const loose = (job.output.text as string | undefined) ?? "";           // text of a generation that wasn't saved to a project
  const asset = file ?? textAsset ?? job.assets[0];
  const isVideo = job.type === "video" || job.type === "movie";
  const isAudio = job.type === "music" || job.type === "voice";
  const isText = !file && (!!textAsset || !!loose);
  return (
    <section aria-label="Generated result" className="reveal">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{label} ready</h2>
        {job.simulated && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-xs text-warn">Simulated</span>}
      </div>
      {job.simulated && <div className="mb-3"><Alert kind="info">This is simulated output from the development simulator — no real content was generated.</Alert></div>}
      {file && isVideo && <VideoPlayer assetId={file.id} thumbnail={file.thumbnail_url} title={`Generated ${label.toLowerCase()}`} eager />}
      {file && isAudio && <div className="rounded-lg bg-raised p-3"><AuthMedia src={file.url!} kind="audio" label={`Generated ${label.toLowerCase()}`} /></div>}
      {file && !isVideo && !isAudio && <AuthImage src={file.url} alt={`Generated ${label.toLowerCase()}`} className="mx-auto max-h-[70vh] w-full rounded-lg object-contain" />}
      {isText && <TextResult assetId={textAsset?.id ?? null} fallback={textAsset?.text_preview ?? loose} />}
      {!file && !isText && <Alert kind="info">The generation finished but has no result to show. Open it from your project or History.</Alert>}
      <div className="mt-4 flex flex-wrap gap-2">
        {file && <button className="btn-secondary" onClick={() => (isVideo || job.type === "face_replacement" ? downloadAsset(file.id) : downloadFile(`/api/assets/${file.id}/download`, `${job.type}-${job.id.slice(0, 8)}`))}><Download className="h-4 w-4" aria-hidden /> Download</button>}
        {onRegenerate && <button className="btn-secondary" onClick={onRegenerate} disabled={regenerating}>{regenerating ? <Spinner /> : <RefreshCw className="h-4 w-4" aria-hidden />} Regenerate</button>}
        <Link className="btn-secondary" to={`/create/${job.type}?from=${job.id}`}><Pencil className="h-4 w-4" aria-hidden /> Refine</Link>
        {job.project_id && asset && <Link className="btn-secondary" to={`/projects/${job.project_id}/assets/${asset.id}`}><ExternalLink className="h-4 w-4" aria-hidden /> Open in project</Link>}
      </div>
    </section>
  );
}
