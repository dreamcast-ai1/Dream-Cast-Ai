import { Check, ImagePlus } from "lucide-react";
import { useRef, useState } from "react";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import type { GeneratorSchema, Reference } from "../../lib/types";
import { AuthImage } from "../ui/AuthImage";
import { Alert, Spinner } from "../ui/feedback";

const MAX = 3;

/** Pick existing project references or upload a new one. Uploads reuse the project reference API (10 MB, image types only). */
export function ReferencePicker({ schema, projectId, selected, onChange, maxMb, label, required: requiredProp, single, hint }: { schema: GeneratorSchema; projectId: string; selected: string[]; onChange: (ids: string[]) => void; maxMb: number; label?: string; required?: boolean; single?: boolean; hint?: string }) {
  const { data, reload } = useAsync(() => (projectId ? api<Reference[]>(`/api/projects/${projectId}/references`) : Promise.resolve([])), [projectId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const required = requiredProp ?? schema.reference === "required";
  const one = single ?? required;

  const toggle = (id: string) => {
    if (selected.includes(id)) onChange(selected.filter((x) => x !== id));
    else onChange(one ? [id] : [...selected, id].slice(0, MAX));
  };
  const upload = async (file?: File) => {
    if (!file) return;
    if (file.size > maxMb * 1024 * 1024) { setError(`That image is larger than ${maxMb} MB.`); return; }
    setBusy(true); setError("");
    try {
      const fd = new FormData(); fd.append("file", file); fd.append("type", required ? "CHARACTER" : "OTHER");
      const ref = await api<Reference>(`/api/projects/${projectId}/references`, { method: "POST", form: fd });
      await reload(); if (!selected.includes(ref.id)) toggle(ref.id);      // re-uploading an identical file returns the existing reference
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); if (input.current) input.current.value = ""; }
  };

  return (
    <div>
      <p className="mb-1.5 text-sm font-medium">{label ?? (schema.reference_label || "Reference image")}{required && <span className="text-danger"> *</span>}</p>
      {!projectId ? <p className="rounded-lg border border-dashed border-border px-4 py-3 text-sm text-muted">Select a project above to {required ? "upload a face image" : "attach a reference image"}. Images are stored in the project's references.</p> : (
        <>
          {error && <div className="mb-2"><Alert kind="error">{error}</Alert></div>}
          <ul className="flex flex-wrap gap-2">
            {(data ?? []).filter((r) => r.mime_type.startsWith("image/")).map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => toggle(r.id)} aria-pressed={selected.includes(r.id)} aria-label={`${selected.includes(r.id) ? "Deselect" : "Select"} reference ${r.name}`}
                  className={`relative h-20 w-20 overflow-hidden rounded-lg border-2 ${selected.includes(r.id) ? "border-accent" : "border-border"}`}>
                  <AuthImage src={r.url} alt={r.name} className="h-full w-full object-cover" lazy />
                  <span className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-1 text-[10px] text-white">{r.type.toLowerCase()}</span>
                  {selected.includes(r.id) && <span className="absolute right-1 top-1 rounded-full bg-accent p-0.5 text-accent-fg"><Check className="h-3 w-3" aria-hidden /></span>}
                </button>
              </li>))}
            <li>
              <input ref={input} id="create-ref-upload" type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" onChange={(e) => upload(e.target.files?.[0])} />
              <label htmlFor="create-ref-upload" className={`flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-xs text-muted hover:text-fg ${busy ? "pointer-events-none opacity-60" : ""}`}>
                {busy ? <Spinner /> : <ImagePlus className="h-5 w-5" aria-hidden />}Upload
              </label>
            </li>
          </ul>
          <p className="mt-1 text-xs text-muted">PNG, JPEG, WebP or GIF up to {maxMb} MB. {one ? "Select one." : `Up to ${MAX}.`} Identical files are never stored twice.{hint ? ` ${hint}` : ""}</p>
        </>
      )}
    </div>
  );
}
