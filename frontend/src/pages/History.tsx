import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { JobList } from "../components/JobList";
import { EmptyState, ErrorState, PageHeader, PageLoader, Spinner } from "../components/ui/feedback";
import { useFeatures } from "../context/FeaturesContext";
import { usePolling } from "../hooks/usePolling";
import { api, errorMessage } from "../lib/api";
import { GEN_FILTERS } from "../lib/generatorMeta";
import { ACTIVE_STATUSES, type Job } from "../lib/types";

const PAGE = 20;

export default function History() {
  const { isGeneratorEnabled } = useFeatures();
  const filters = GEN_FILTERS.filter((f) => !f.types || f.types.split(",").some(isGeneratorEnabled));      // no filter for a feature that is unavailable
  const [filter, setFilter] = useState("all");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const types = GEN_FILTERS.find((f) => f.id === filter)?.types ?? "";
  const url = useCallback((offset: number, limit: number) => `/api/jobs?limit=${limit}&offset=${offset}${types ? `&type=${types}` : ""}`, [types]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const r = await api<Job[]>(url(0, PAGE)); setJobs(r); setMore(r.length === PAGE); } catch (e) { setError(errorMessage(e)); } finally { setLoading(false); }
  }, [url]);
  useEffect(() => { void load(); }, [load]);

  const loadMore = async () => {
    setLoadingMore(true);
    try { const r = await api<Job[]>(url(jobs.length, PAGE)); setJobs((j) => [...j, ...r]); setMore(r.length === PAGE); } catch (e) { setError(errorMessage(e)); } finally { setLoadingMore(false); }
  };
  // Quiet refresh of the loaded window while something is running (every 6 s, only when the tab is visible).
  const refreshQuietly = async () => { try { const r = await api<Job[]>(url(0, Math.max(jobs.length, PAGE))); setJobs(r); } catch { /* keep the current list */ } };
  usePolling(refreshQuietly, jobs.some((j) => ACTIVE_STATUSES.includes(j.status)), 6000);

  return (
    <div>
      <PageHeader title="Recent generations" subtitle="Everything you've created, newest first."
        actions={<Link to="/create" className="btn-primary"><Sparkles className="h-4 w-4" aria-hidden /> Create</Link>} />
      <div role="group" aria-label="Filter by type" className="mb-4 flex flex-wrap gap-1.5">
        {filters.map((f) => (
          <button key={f.id} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
            className={`rounded-full border px-3 py-1 text-sm font-medium ${filter === f.id ? "border-accent bg-accent/15" : "border-border text-muted hover:text-fg"}`}>{f.label}</button>))}
      </div>
      {loading ? <PageLoader /> : error ? <ErrorState message={error} onRetry={load} />
        : !jobs.length ? <EmptyState icon="✨" title={filter === "all" ? "No generations yet" : "Nothing here yet"} hint="Generations you submit from Create will show up here, even if you leave the page while they run."
            action={<Link to="/create" className="btn-primary">Start creating</Link>} />
        : <>
          <JobList jobs={jobs} />
          {more && <div className="mt-4 text-center"><button className="btn-secondary" onClick={loadMore} disabled={loadingMore}>{loadingMore ? <Spinner /> : "Load more"}</button></div>}
        </>}
    </div>
  );
}
