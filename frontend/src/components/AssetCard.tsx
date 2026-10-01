import { Music2 } from "lucide-react";
import { Link } from "react-router-dom";
import { ASSET_EMOJI, ASSET_LABEL, formatDuration } from "../lib/assetText";
import { timeAgo } from "../lib/format";
import type { Asset } from "../lib/types";
import { AuthImage } from "./ui/AuthImage";
import { StatusBadge } from "./ui/feedback";

/** Compact card for a generated asset in a project. Lists never load media: videos and face results show a small thumbnail only. */
export function AssetCard({ a }: { a: Asset }) {
  const audio = a.has_file && (a.type === "MUSIC" || a.type === "VOICE");
  const visual = a.has_file && (a.type === "VIDEO" || a.type === "FACE" || a.type === "IMAGE");
  return (
    <li>
      <Link to={`/projects/${a.project_id}/assets/${a.id}`} aria-label={`Open ${ASSET_LABEL[a.type] ?? a.type}: ${a.title}, version ${a.version}`}
        className="card flex h-full flex-col overflow-hidden transition-colors hover:border-accent/60">
        {visual && (
          <div className="relative aspect-video bg-raised">
            {a.thumbnail_url ? <AuthImage src={a.thumbnail_url} alt={`Thumbnail of ${a.title}`} className="h-full w-full object-cover" lazy
              fallback={<div className="flex h-full items-center justify-center text-3xl" aria-hidden>{ASSET_EMOJI[a.type]}</div>} />
              : <div className="flex h-full items-center justify-center text-3xl" aria-hidden>{ASSET_EMOJI[a.type]}</div>}
            {a.type === "VIDEO" && a.duration_seconds && <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">{formatDuration(a.duration_seconds)}</span>}
          </div>)}
        <div className="flex flex-1 flex-col p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted"><span aria-hidden>{ASSET_EMOJI[a.type]}</span> {ASSET_LABEL[a.type] ?? a.type} · v{a.version}</p>
              <h3 className="mt-0.5 truncate font-sans text-sm font-semibold">{a.title}</h3>
            </div>
            {a.status === "SIMULATED" && <StatusBadge status="SIMULATED" />}
          </div>
          {audio ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-muted"><Music2 className="h-4 w-4" aria-hidden />
              {[a.format.toUpperCase(), formatDuration(a.duration_seconds), a.language].filter(Boolean).join(" · ")}</p>
          ) : visual ? (
            <p className="mt-2 text-sm text-muted">{[a.meta.aspect_ratio as string | undefined, a.type === "VIDEO" ? (a.meta.method as string | undefined) : undefined, "Ready"].filter(Boolean).join(" · ")}</p>
          ) : a.text_preview ? <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm text-muted">{a.text_preview}</p> : null}
          <p className="mt-auto pt-3 text-xs text-muted">{timeAgo(a.created_at)}{a.provider ? ` · ${a.provider}` : ""}{a.meta.edited ? " · edited" : ""}</p>
        </div>
      </Link>
    </li>
  );
}
