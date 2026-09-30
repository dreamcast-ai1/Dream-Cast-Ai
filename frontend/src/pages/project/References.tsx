import { ImagePlus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { AuthImage } from "../../components/ui/AuthImage";
import { SelectField } from "../../components/ui/Field";
import { ConfirmDialog } from "../../components/ui/Modal";
import { Alert, EmptyState, ErrorState, PageLoader, Spinner } from "../../components/ui/feedback";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import { formatBytes, titleCase } from "../../lib/format";
import type { ProjectDetail, Reference } from "../../lib/types";

const TYPES = ["CHARACTER", "LOCATION", "OBJECT", "STYLE", "OTHER"];
const MAX_MB = 10;

export function References({ project, onChanged }: { project: ProjectDetail; onChanged: () => void }) {
  const pid = project.id;
  const { data, loading, error, reload } = useAsync(() => api<Reference[]>(`/api/projects/${pid}/references`), [pid]);
  const [type, setType] = useState("CHARACTER");
  const [filter, setFilter] = useState("ALL");
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [deleting, setDeleting] = useState<Reference | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true); setErr("");
    for (const file of Array.from(files)) {
      if (file.size > MAX_MB * 1024 * 1024) { setErr(`${file.name} is larger than ${MAX_MB} MB.`); continue; }
      try { const fd = new FormData(); fd.append("file", file); fd.append("type", type); await api(`/api/projects/${pid}/references`, { method: "POST", form: fd }); }
      catch (e) { setErr(errorMessage(e)); }
    }
    setUploading(false); if (input.current) input.current.value = "";
    void reload(); onChanged();
  };

  const remove = async () => {
    if (!deleting) return;
    try { await api(`/api/projects/${pid}/references/${deleting.id}`, { method: "DELETE" }); setDeleting(null); void reload(); onChanged(); }
    catch (e) { setErr(errorMessage(e)); setDeleting(null); }
  };
  const setPoster = async (r: Reference) => {
    try { await api(`/api/projects/${pid}/poster`, { method: "PUT", json: { reference_id: r.id } }); setNotice(`"${r.name}" is now the project poster.`); onChanged(); }
    catch (e) { setErr(errorMessage(e)); }
  };

  const shown = (data ?? []).filter((r) => filter === "ALL" || r.type === filter);
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-end gap-3 p-4">
        <div className="w-full sm:w-44"><SelectField label="Reference type" value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</SelectField></div>
        <input ref={input} id="ref-upload" type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" onChange={(e) => upload(e.target.files)} />
        <label htmlFor="ref-upload" className={`btn-primary cursor-pointer ${uploading ? "pointer-events-none opacity-60" : ""}`}>{uploading ? <Spinner /> : <ImagePlus className="h-4 w-4" aria-hidden />} Upload images</label>
        <p className="text-xs text-muted">PNG, JPEG, WebP or GIF · up to {MAX_MB} MB each</p>
      </div>
      {err && <Alert kind="error">{err}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter references">
        {["ALL", ...TYPES].map((t) => (
          <button key={t} aria-pressed={filter === t} onClick={() => setFilter(t)}
            className={`rounded-full border px-3 py-1 text-xs font-medium ${filter === t ? "border-accent bg-accent/15" : "border-border text-muted hover:text-fg"}`}>{titleCase(t)}</button>))}
      </div>
      {loading ? <PageLoader /> : error ? <ErrorState message={error} onRetry={reload} />
        : !shown.length ? <EmptyState icon="🖼️" title="No references yet." hint="Upload images of characters, locations, objects or styles to keep your project consistent." />
        : <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{shown.map((r) => (
          <li key={r.id} className="card overflow-hidden">
            <div className="aspect-square bg-raised"><AuthImage src={r.url} alt={r.name} className="h-full w-full object-cover" /></div>
            <div className="p-2.5">
              <p className="truncate text-sm font-medium" title={r.name}>{r.name}</p>
              <p className="text-xs text-muted">{titleCase(r.type)} · {formatBytes(r.size_bytes)}</p>
              <div className="mt-2 flex items-center justify-between">
                <button className="text-xs text-accent hover:underline disabled:text-muted disabled:no-underline" onClick={() => setPoster(r)}>Use as poster</button>
                <button className="btn-ghost !p-1" aria-label={`Delete reference ${r.name}`} onClick={() => setDeleting(r)}><Trash2 className="h-4 w-4" /></button>
              </div>
            </div>
          </li>))}</ul>}
      <ConfirmDialog open={!!deleting} danger title="Delete reference?" confirmLabel="Delete" message={`Delete "${deleting?.name}"? The image file will be removed.`} onClose={() => setDeleting(null)} onConfirm={remove} />
    </div>
  );
}
