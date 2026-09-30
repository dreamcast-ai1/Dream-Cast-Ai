import { Pencil, Plus, Trash2, User } from "lucide-react";
import { useRef, useState } from "react";
import { AuthImage } from "../../components/ui/AuthImage";
import { TextArea, TextField } from "../../components/ui/Field";
import { ConfirmDialog, Modal } from "../../components/ui/Modal";
import { Alert, EmptyState, ErrorState, PageLoader } from "../../components/ui/feedback";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import type { Character } from "../../lib/types";

const blank = { name: "", age: "", description: "", appearance: "", personality: "", clothing: "" };

function CharacterForm({ projectId, character, onClose, onSaved }: { projectId: string; character: Character | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState(character ? { name: character.name, age: character.age, description: character.description, appearance: character.appearance, personality: character.personality, clothing: character.clothing } : blank);
  const [current, setCurrent] = useState<Character | null>(character);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) { setError("Character name is required."); return; }
    setBusy(true); setError("");
    try {
      const body = { ...form, name: form.name.trim() };
      if (current) await api(`/api/projects/${projectId}/characters/${current.id}`, { method: "PUT", json: body });
      else await api(`/api/projects/${projectId}/characters`, { method: "POST", json: body });
      onSaved();
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };

  const upload = async (file: File | undefined) => {
    if (!file || !current) return;
    setBusy(true); setError("");
    try {
      const fd = new FormData(); fd.append("file", file);
      setCurrent(await api<Character>(`/api/projects/${projectId}/characters/${current.id}/image`, { method: "POST", form: fd }));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const removeImage = async () => {
    if (!current) return;
    try { setCurrent(await api<Character>(`/api/projects/${projectId}/characters/${current.id}/image`, { method: "DELETE" })); } catch (err) { setError(errorMessage(err)); }
  };

  return (
    <Modal open title={character ? "Edit character" : "Add character"} onClose={() => { if (current !== character) onSaved(); else onClose(); }}>
      <form onSubmit={save} className="space-y-4">
        {error && <Alert kind="error">{error}</Alert>}
        <div className="grid gap-4 sm:grid-cols-[1fr_7rem]">
          <TextField label="Name" required maxLength={120} value={form.name} onChange={set("name")} />
          <TextField label="Age" maxLength={40} value={form.age} onChange={set("age")} />
        </div>
        <TextArea label="Description" value={form.description} onChange={set("description")} />
        <TextArea label="Appearance" value={form.appearance} onChange={set("appearance")} />
        <TextArea label="Personality" value={form.personality} onChange={set("personality")} />
        <TextArea label="Clothing" value={form.clothing} onChange={set("clothing")} />
        <div>
          <p className="mb-1.5 text-sm font-medium">Reference image</p>
          {current ? (
            <div className="flex items-center gap-3">
              <div className="h-16 w-16 overflow-hidden rounded-lg bg-raised">
                <AuthImage src={current.image_url} alt={`${current.name} reference`} className="h-full w-full object-cover" fallback={<div className="flex h-full items-center justify-center text-muted"><User className="h-6 w-6" aria-hidden /></div>} />
              </div>
              <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" id="char-img" onChange={(e) => upload(e.target.files?.[0])} />
              <label htmlFor="char-img" className="btn-secondary cursor-pointer">{current.image_url ? "Replace" : "Upload"}</label>
              {current.image_url && <button type="button" className="btn-ghost" onClick={removeImage}>Remove</button>}
            </div>
          ) : <p className="text-sm text-muted">Save the character first, then you can upload a reference image (max 10 MB).</p>}
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={() => (current !== character ? onSaved() : onClose())}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save character"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function Characters({ projectId, onChanged }: { projectId: string; onChanged: () => void }) {
  const { data, loading, error, reload } = useAsync(() => api<Character[]>(`/api/projects/${projectId}/characters`), [projectId]);
  const [editing, setEditing] = useState<Character | null | "new">(null);
  const [deleting, setDeleting] = useState<Character | null>(null);
  const [actionError, setActionError] = useState("");
  const done = () => { setEditing(null); void reload(); onChanged(); };

  const remove = async () => {
    if (!deleting) return;
    try { await api(`/api/projects/${projectId}/characters/${deleting.id}`, { method: "DELETE" }); setDeleting(null); done(); }
    catch (e) { setActionError(errorMessage(e)); setDeleting(null); }
  };

  if (loading) return <PageLoader />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  return (
    <div className="space-y-4">
      {actionError && <Alert kind="error">{actionError}</Alert>}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">Your character bible. Later phases will keep characters consistent across generations.</p>
        <button className="btn-primary shrink-0" onClick={() => setEditing("new")}><Plus className="h-4 w-4" aria-hidden /> Add character</button>
      </div>
      {!data?.length ? <EmptyState icon="🧑‍🎤" title="No characters yet." hint="Add characters with their appearance, personality and clothing." action={<button className="btn-primary" onClick={() => setEditing("new")}>Add character</button>} />
        : <ul className="grid gap-3 sm:grid-cols-2">{data.map((c) => (
          <li key={c.id} className="card flex gap-3 p-4">
            <div className="h-20 w-20 shrink-0 overflow-hidden rounded-lg bg-raised">
              <AuthImage src={c.image_url} alt={`${c.name} reference`} className="h-full w-full object-cover" fallback={<div className="flex h-full items-center justify-center text-muted"><User className="h-8 w-8" aria-hidden /></div>} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0"><h3 className="truncate font-sans font-semibold">{c.name}</h3>{c.age && <p className="text-xs text-muted">Age {c.age}</p>}</div>
                <div className="flex shrink-0"><button className="btn-ghost !p-1.5" aria-label={`Edit ${c.name}`} onClick={() => setEditing(c)}><Pencil className="h-4 w-4" /></button>
                  <button className="btn-ghost !p-1.5" aria-label={`Delete ${c.name}`} onClick={() => setDeleting(c)}><Trash2 className="h-4 w-4" /></button></div>
              </div>
              <p className="mt-1 line-clamp-3 text-sm text-muted">{c.description || c.appearance || "No description yet."}</p>
            </div>
          </li>))}</ul>}
      {editing && <CharacterForm key={editing === "new" ? "new" : editing.id} projectId={projectId} character={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={done} />}
      <ConfirmDialog open={!!deleting} danger title="Delete character?" confirmLabel="Delete" message={`Delete ${deleting?.name}? This cannot be undone.`} onClose={() => setDeleting(null)} onConfirm={remove} />
    </div>
  );
}
