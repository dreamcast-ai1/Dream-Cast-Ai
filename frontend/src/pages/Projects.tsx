import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ProjectFormModal } from "../components/ProjectFormModal";
import { ProjectCard, ProjectGrid } from "../components/ProjectCard";
import { ConfirmDialog } from "../components/ui/Modal";
import { Alert, EmptyState, ErrorState, PageHeader, Skeleton } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import type { Project } from "../lib/types";

export default function Projects() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const { data, loading, error, reload } = useAsync(() => api<Project[]>("/api/projects"));
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    if (params.get("new")) { setEditing(null); setFormOpen(true); setParams({}, { replace: true }); }
  }, [params, setParams]);

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try { await api(`/api/projects/${deleting.id}`, { method: "DELETE" }); setDeleting(null); void reload(); }
    catch (e) { setDeleteError(errorMessage(e)); setDeleting(null); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader title="Projects" subtitle="Your stories, scripts, music and characters — organised as productions."
        actions={<button className="btn-primary" onClick={() => { setEditing(null); setFormOpen(true); }}><Plus className="h-4 w-4" aria-hidden /> New project</button>} />
      {deleteError && <div className="mb-4"><Alert kind="error">{deleteError}</Alert></div>}
      {loading ? <ProjectGrid>{[0, 1, 2].map((i) => <Skeleton key={i} className="h-52" />)}</ProjectGrid>
        : error ? <ErrorState message={error} onRetry={reload} />
        : !data?.length ? <EmptyState icon="🎬" title="No projects yet" hint="Create a project to start organising your production."
            action={<button className="btn-primary" onClick={() => setFormOpen(true)}>Create project</button>} />
        : <ProjectGrid>{data.map((p) => <ProjectCard key={p.id} p={p} onRename={(x) => { setEditing(x); setFormOpen(true); }} onDelete={setDeleting} />)}</ProjectGrid>}
      <ProjectFormModal open={formOpen} project={editing} onClose={() => setFormOpen(false)}
        onSaved={(p) => { setFormOpen(false); if (editing) void reload(); else nav(`/projects/${p.id}`); }} />
      <ConfirmDialog open={!!deleting} danger busy={busy} title="Delete project?" confirmLabel="Delete project" onClose={() => setDeleting(null)} onConfirm={remove}
        message={`"${deleting?.title}" and everything in it (characters, references, generated assets) will be permanently deleted.`} />
    </div>
  );
}
