import { useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { Project, ProjectDetail } from "../lib/types";
import { Modal } from "./ui/Modal";
import { TextArea, TextField } from "./ui/Field";
import { Alert } from "./ui/feedback";

export function ProjectFormModal({ open, project, onClose, onSaved }: { open: boolean; project?: Project | null; onClose: () => void; onSaved: (p: ProjectDetail) => void }) {
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("");
  const [description, setDescription] = useState("");
  const [style, setStyle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) { setTitle(project?.title ?? ""); setGenre(project?.genre ?? ""); setDescription(project?.description ?? ""); setStyle(project?.style ?? ""); setError(""); }
  }, [open, project]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) { setError("Please give your project a title."); return; }
    setBusy(true); setError("");
    try {
      const body = { title: title.trim(), genre: genre.trim(), style: style.trim(), description };
      const saved = project
        ? await api<ProjectDetail>(`/api/projects/${project.id}`, { method: "PATCH", json: body })
        : await api<ProjectDetail>("/api/projects", { method: "POST", json: body });
      onSaved(saved);
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} title={project ? "Edit project" : "New project"} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert kind="error">{error}</Alert>}
        <TextField label="Title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="The Lost Kingdom" required />
        <TextField label="Genre" value={genre} maxLength={60} onChange={(e) => setGenre(e.target.value)} placeholder="Fantasy, Sci-Fi, Drama…" />
        <TextField label="Visual style (optional)" value={style} maxLength={80} onChange={(e) => setStyle(e.target.value)} placeholder="Cinematic realism"
          hint="Used as context for video prompts unless a generation picks its own style." />
        <TextArea label="Description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project about?" />
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : project ? "Save changes" : "Create project"}</button>
        </div>
      </form>
    </Modal>
  );
}
