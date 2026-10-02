import { Plus, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { GeneratorCard } from "../components/GeneratorCard";
import { ProjectCard, ProjectGrid } from "../components/ProjectCard";
import { UsageList } from "../components/UsageList";
import { JobList } from "../components/JobList";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/feedback";
import { useAuth } from "../context/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { useGenerators } from "../hooks/useGenerators";
import { api } from "../lib/api";
import type { Job, Project, UsageItem } from "../lib/types";

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const generators = useGenerators();
  const projects = useAsync(() => api<Project[]>("/api/projects?limit=6"));
  const jobs = useAsync(() => api<Job[]>("/api/jobs?limit=5"));
  const usage = useAsync(() => api<{ items: UsageItem[] }>("/api/usage"));

  return (
    <div className="space-y-10">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-accent">DreamCast AI</p>
          <h1 className="mt-2 text-3xl font-bold sm:text-4xl">Welcome back, {user?.name.split(" ")[0] || "creator"}.</h1>
          <p className="mt-1 text-muted">Create anything. Build your story.</p>
        </div>
        <button className="btn-primary" onClick={() => nav("/create")}><Sparkles className="h-4 w-4" aria-hidden /> Create</button>
      </section>

      <section aria-labelledby="quick">
        <h2 id="quick" className="mb-3 text-lg font-semibold">Quick create</h2>
        <div className="stagger grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">{generators.map((g) => <GeneratorCard key={g.id} g={g} />)}</div>
      </section>

      <section aria-labelledby="recent-projects">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="recent-projects" className="text-lg font-semibold">Recent projects</h2>
          <Link to="/projects" className="text-sm text-accent hover:underline">View all</Link>
        </div>
        {projects.loading ? <ProjectGrid>{[0, 1, 2].map((i) => <Skeleton key={i} className="h-52" />)}</ProjectGrid>
          : projects.error ? <ErrorState message={projects.error} onRetry={projects.reload} />
          : !projects.data?.length ? <EmptyState icon="🎬" title="No projects yet" hint="Projects hold your story, scripts, music, characters and references in one place."
              action={<Link to="/projects?new=1" className="btn-primary"><Plus className="h-4 w-4" aria-hidden /> Create your first project</Link>} />
          : <ProjectGrid>{projects.data.map((p) => <ProjectCard key={p.id} p={p} />)}</ProjectGrid>}
      </section>

      <div className="grid gap-8 lg:grid-cols-2">
        <section aria-labelledby="recent-gens">
          <div className="mb-3 flex items-center justify-between"><h2 id="recent-gens" className="text-lg font-semibold">Recent generations</h2><Link to="/history" className="text-sm text-accent hover:underline">View all</Link></div>
          {jobs.loading ? <Skeleton className="h-32" /> : jobs.error ? <ErrorState message={jobs.error} onRetry={jobs.reload} />
            : !jobs.data?.length ? <EmptyState icon="✨" title="Nothing generated yet" hint="Generations you start from Create appear here and keep running in the background." />
            : <JobList jobs={jobs.data} />}
        </section>
        <section aria-labelledby="usage-sum">
          <div className="mb-3 flex items-center justify-between"><h2 id="usage-sum" className="text-lg font-semibold">Today's usage</h2>
            <Link to="/usage" className="text-sm text-accent hover:underline">Details</Link></div>
          {usage.loading ? <Skeleton className="h-32" /> : usage.error ? <ErrorState message={usage.error} onRetry={usage.reload} />
            : <div className="card p-4"><UsageList compact items={usage.data!.items.slice(0, 5)} /></div>}
        </section>
      </div>
    </div>
  );
}
