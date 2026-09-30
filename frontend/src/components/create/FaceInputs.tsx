import { Film, ImagePlus } from "lucide-react";
import { useRef, useState } from "react";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import { formatDuration } from "../../lib/assetText";
import { formatBytes } from "../../lib/format";
import type { Reference } from "../../lib/types";
import { AuthImage } from "../ui/AuthImage";
import { Alert, Spinner } from "../ui/feedback";

const isVideo = (r: Reference) => r.mime_type.startsWith("video/");
const describe = (r: Reference) => [isVideo(r) ? "Video" : "Image", r.mime_type.split("/")[1]?.toUpperCase(), r.width && r.height ? `${r.width}×${r.height}` : "",
  r.duration_seconds ? formatDuration(r.duration_seconds) : "", formatBytes(r.size_bytes)].filter(Boolean).join(" · ");

function Slot({ title, projectId, kind, value, onChange, maxImageMb, maxVideoMb }: { title: string; projectId: string; kind: "source" | "face"; value: string;
  onChange: (id: string) => void; maxImageMb: number; maxVideoMb: number }) {
  const { data, reload } = useAsync(() => (projectId ? api<Reference[]>(`/api/projects/${projectId}/references`) : Promise.resolve([])), [projectId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const options = (data ?? []).filter((r) => kind === "source" ? r.type !== "FACE" || !isVideo(r) : !isVideo(r));
  const selected = options.find((r) => r.id === value);

  const upload = async (file?: File) => {
    if (!file) return;
    const video = file.type.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(file.name);
    if (video && kind === "face") { setError("The face must be an image."); return; }
    const limit = (video ? maxVideoMb : maxImageMb) * 1024 * 1024;
    if (file.size > limit) { setError(`That file is larger than ${video ? maxVideoMb : maxImageMb} MB.`); return; }
    setBusy(true); setError("");
    try {
      const fd = new FormData(); fd.append("file", file); fd.append("type", kind === "face" ? "FACE" : "SOURCE");
      const ref = await api<Reference>(`/api/projects/${projectId}/references${video ? "/video" : ""}`, { method: "POST", form: fd });
      await reload(); onChange(ref.id);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const id = `face-${kind}`;
  return (
    <div>
      <p className="mb-1.5 text-sm font-medium">{title} <span className="text-danger">*</span></p>
      <ul className="flex flex-wrap gap-2">
        {options.map((r) => (
          <li key={r.id}>
            <button type="button" onClick={() => onChange(value === r.id ? "" : r.id)} aria-pressed={value === r.id} aria-label={`${value === r.id ? "Deselect" : "Select"} ${r.name}`}
              className={`relative flex h-20 w-20 items-center justify-center overflow-hidden rounded-lg border-2 bg-raised ${value === r.id ? "border-accent" : "border-border"}`}>
              {isVideo(r) ? <Film className="h-7 w-7 text-muted" aria-hidden /> : <AuthImage src={r.url} alt={r.name} className="h-full w-full object-cover" lazy />}
            </button>
          </li>))}
        <li>
          <input ref={input} id={id} type="file" className="sr-only" accept={kind === "face" ? "image/png,image/jpeg,image/webp" : "image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm"} onChange={(e) => upload(e.target.files?.[0])} />
          <label htmlFor={id} className={`flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-xs text-muted hover:text-fg ${busy ? "pointer-events-none opacity-60" : ""}`}>
            {busy ? <Spinner /> : <ImagePlus className="h-5 w-5" aria-hidden />}Upload</label>
        </li>
      </ul>
      {error && <div className="mt-2"><Alert kind="error">{error}</Alert></div>}
      {selected ? <p className="mt-2 rounded-lg bg-raised px-3 py-2 text-sm"><strong>{selected.name}</strong><br /><span className="text-muted">{describe(selected)}</span></p>
        : <p className="mt-1 text-xs text-muted">{kind === "source" ? `An image (up to ${maxImageMb} MB) or a video (MP4, MOV or WebM, up to ${maxVideoMb} MB and 2 minutes).` : `A clear image of one face (at least 64×64 pixels, up to ${maxImageMb} MB).`}</p>}
    </div>
  );
}

/** Face replacement inputs: source, face, permission confirmation. Uploads are stored as project references (never public). */
export function FaceInputs({ projectId, sourceId, faceId, permission, onSource, onFace, onPermission, maxImageMb, maxVideoMb, videoSupported }: {
  projectId: string; sourceId: string; faceId: string; permission: boolean; onSource: (id: string) => void; onFace: (id: string) => void; onPermission: (v: boolean) => void;
  maxImageMb: number; maxVideoMb: number; videoSupported: boolean }) {
  if (!projectId) return <p className="rounded-lg border border-dashed border-border px-4 py-3 text-sm text-muted">Select a project above first. Your source and face files are stored privately in that project's references.</p>;
  return (
    <div className="space-y-4">
      <Slot title="1. Source image or video" projectId={projectId} kind="source" value={sourceId} onChange={onSource} maxImageMb={maxImageMb} maxVideoMb={maxVideoMb} />
      {!videoSupported && <p className="text-xs text-muted">The configured provider works on images only; a video source will be refused before anything is sent.</p>}
      <Slot title="2. Face to use" projectId={projectId} kind="face" value={faceId} onChange={onFace} maxImageMb={maxImageMb} maxVideoMb={maxVideoMb} />
      <label className="flex items-start gap-2.5 rounded-lg border border-border p-3 text-sm">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[rgb(var(--accent))]" checked={permission} onChange={(e) => onPermission(e.target.checked)} />
        <span>I confirm that I have permission to use the face/image uploaded for this generation.</span>
      </label>
    </div>
  );
}
