import { Film, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AuthImage } from "../components/ui/AuthImage";
import { EmptyState, ErrorState, PageHeader, PageLoader, Spinner, StatusBadge } from "../components/ui/feedback";
import { api, errorMessage } from "../lib/api";
import { ASSET_EMOJI, ASSET_LABEL, formatDuration } from "../lib/assetText";
import type { LibraryItem } from "../lib/types";

const PAGE = 24;
// [id, label, query string]. "Movies" are assembled movies (stored as videos); "Videos" hides them so scene clips and movies aren't mixed up.
const FILTERS = [["all", "All", ""], ["image", "Images", "type=IMAGE"], ["video", "Videos", "type=VIDEO&movies=false"], ["movie", "Movies", "type=VIDEO&movies=true"],
  ["story", "Story", "type=STORY"], ["script", "Script", "type=SCRIPT"], ["music", "Music", "type=MUSIC"], ["voice", "Voice", "type=VOICE"],
  ["other", "Lyrics & more", "type=LYRICS,FACE,AVATAR"]] as const;

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function LibraryCard({ a }: { a: LibraryItem }) {
  const preview = a.thumbnail_url ?? (a.type === "IMAGE" && a.has_file ? a.url : null);
  const label = a.is_movie ? "Movie" : ASSET_LABEL[a.type] ?? a.type;
  const fallback = <div className="flex h-full items-center justify-center text-3xl" aria-hidden>{a.is_movie ? "🎞️" : ASSET_EMOJI[a.type]}</div>;
  return (
    <li>
      <Link to={`/projects/${a.project_id}/assets/${a.id}`} aria-label={`Open ${label}: ${a.title}`} className="card flex h-full flex-col overflow-hidden transition-colors hover:border-accent/60">
        <div className="relative aspect-video bg-raised">
          {preview ? <AuthImage src={preview} alt={`Preview of ${a.title}`} className="h-full w-full object-cover" lazy fallback={fallback} /> : fallback}
          {a.duration_seconds ? <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">{formatDuration(a.duration_seconds)}</span> : null}
        </div>
        <div className="flex flex-1 flex-col p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{a.is_movie ? <Film className="mr-1 inline h-3 w-3" aria-hidden /> : <span aria-hidden>{ASSET_EMOJI[a.type]} </span>}{label} · v{a.version}</p>
              <h3 className="mt-0.5 truncate font-sans text-sm font-semibold">{a.title}</h3>
            </div>
            <StatusBadge status={a.status === "READY" ? "COMPLETED" : a.status} />
          </div>
          {(a.prompt || a.text_preview) && <p className="mt-2 line-clamp-2 text-sm text-muted">{a.type === "STORY" || a.type === "SCRIPT" || a.type === "LYRICS" ? a.text_preview : (a.meta.original_prompt as string | undefined) || a.prompt}</p>}
          <p className="mt-auto truncate pt-3 text-xs text-muted">{a.project_title ?? "Project"} · {when(a.created_at)}</p>
        </div>
      </Link>
    </li>
  );
}

/** Everything the user has generated, across all projects, newest first. Items come from the database, so they survive logout, refresh and redeploys of the frontend. */
export default function Library() {
  const [filter, setFilter] = useState<string>("all");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const query = FILTERS.find((f) => f[0] === filter)?.[2] ?? "";
  const url = useCallback((offset: number) => `/api/assets?limit=${PAGE}&offset=${offset}${query ? `&${query}` : ""}`, [query]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const r = await api<LibraryItem[]>(url(0)); setItems(r); setMore(r.length === PAGE); } catch (e) { setError(errorMessage(e)); } finally { setLoading(false); }
  }, [url]);
  useEffect(() => { void load(); }, [load]);
  const loadMore = async () => {
    setLoadingMore(true);
    try { const r = await api<LibraryItem[]>(url(items.length)); setItems((x) => [...x, ...r]); setMore(r.length === PAGE); } catch (e) { setError(errorMessage(e)); } finally { setLoadingMore(false); }
  };

  return (
    <div>
      <PageHeader title="Library" subtitle="Your images, videos, movies, stories and more, from every project."
        actions={<Link to="/create" className="btn-primary"><Sparkles className="h-4 w-4" aria-hidden /> Create</Link>} />
      <div role="group" aria-label="Filter library" className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map(([id, label]) => (
          <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}
            className={`rounded-full border px-3 py-1 text-sm font-medium ${filter === id ? "border-accent bg-accent/15" : "border-border text-muted hover:text-fg"}`}>{label}</button>))}
      </div>
      {loading ? <PageLoader /> : error ? <ErrorState message={error} onRetry={load} />
        : !items.length ? <EmptyState icon="🗂️" title={filter === "all" ? "Your library is empty" : "Nothing here yet"} hint="Anything you generate inside a project is saved here automatically. Generations that are still running appear in History."
            action={<Link to="/create" className="btn-primary">Start creating</Link>} />
        : <>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{items.map((a) => <LibraryCard key={a.id} a={a} />)}</ul>
          {more && <div className="mt-4 text-center"><button className="btn-secondary" onClick={loadMore} disabled={loadingMore}>{loadingMore ? <Spinner /> : "Load more"}</button></div>}
        </>}
    </div>
  );
}
