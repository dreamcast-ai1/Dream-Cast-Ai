import { Film, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { timeAgo } from "../lib/format";
import type { Project } from "../lib/types";
import { AuthImage } from "./ui/AuthImage";
import { Menu } from "./ui/Menu";
import { StatusBadge } from "./ui/feedback";

/** Deterministic poster gradient so projects without an image still look distinct. */
function posterStyle(id: string) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return { background: `linear-gradient(160deg, hsl(${h} 35% 22%), hsl(${(h + 40) % 360} 40% 10%))` };
}

export function ProjectCard({ p, onRename, onDelete }: { p: Project; onRename?: (p: Project) => void; onDelete?: (p: Project) => void }) {
  return (
    <div className="card group relative overflow-hidden transition-colors hover:border-accent/60">
      <Link to={`/projects/${p.id}`} className="block rounded-xl" aria-label={`Open project ${p.title}`}>
        <div className="relative aspect-[16/10] overflow-hidden" style={posterStyle(p.id)}>
          <AuthImage src={p.thumbnail_url} alt={`Poster for ${p.title}`} className="h-full w-full object-cover"
            fallback={<div className="flex h-full items-center justify-center text-white/25"><Film className="h-10 w-10" aria-hidden /></div>} />
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-3 pt-10">
            <h3 className="truncate font-display text-lg font-semibold text-white">{p.title}</h3>
            {p.genre && <p className="truncate text-xs uppercase tracking-wider text-white/70">{p.genre}</p>}
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 px-3 py-2.5">
          <StatusBadge status={p.status} />
          <span className="text-xs text-muted">Updated {timeAgo(p.updated_at)}</span>
        </div>
      </Link>
      {(onRename || onDelete) && (
        <div className="absolute right-2 top-2 rounded-lg bg-black/50 backdrop-blur">
          <Menu label={`Actions for ${p.title}`} trigger={<MoreVertical className="h-4 w-4 text-white" />}
            items={[
              ...(onRename ? [{ label: "Rename", icon: <Pencil className="h-4 w-4" />, onSelect: () => onRename(p) }] : []),
              ...(onDelete ? [{ label: "Delete", icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: () => onDelete(p) }] : []),
            ]} />
        </div>
      )}
    </div>
  );
}

export function ProjectGrid({ children }: { children: React.ReactNode }) {
  return <div className="stagger grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>;
}
