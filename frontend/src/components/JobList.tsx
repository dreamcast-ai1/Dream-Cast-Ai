import { Link } from "react-router-dom";
import { EMOJI, LABEL } from "../lib/generatorMeta";
import { timeAgo } from "../lib/format";
import type { Job } from "../lib/types";
import { AuthImage } from "./ui/AuthImage";
import { StatusBadge } from "./ui/feedback";

const STATUS_LABEL: Record<string, string> = { PROCESSING: "PROCESSING", QUEUED: "QUEUED" };

export function JobRow({ job, showProject = true }: { job: Job; showProject?: boolean }) {
  const prompt = job.original_prompt || job.refined_prompt || "(no prompt)";
  return (
    <li>
      <Link to={`/history/${job.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-raised" aria-label={`${LABEL[job.type] ?? job.type} generation: ${prompt}`}>
        {job.assets[0]?.thumbnail_url ? <span className="h-9 w-14 shrink-0 overflow-hidden rounded-md bg-raised"><AuthImage src={job.assets[0].thumbnail_url} alt="" className="h-full w-full object-cover" lazy /></span>
          : <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-lg" aria-hidden>{EMOJI[job.type] ?? "✨"}</span>}
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-semibold uppercase tracking-wider text-muted">{LABEL[job.type] ?? job.type}{job.simulated && " · simulated"}</span>
          <span className="block truncate text-sm font-medium">“{prompt}”</span>
          <span className="block truncate text-xs text-muted">{timeAgo(job.created_at)}{showProject && job.project_title ? ` · ${job.project_title}` : ""}</span>
        </span>
        <StatusBadge status={STATUS_LABEL[job.status] ?? job.status} />
      </Link>
    </li>
  );
}

export function JobList({ jobs, showProject }: { jobs: Job[]; showProject?: boolean }) {
  return <ul className="card divide-y divide-border overflow-hidden">{jobs.map((j) => <JobRow key={j.id} job={j} showProject={showProject} />)}</ul>;
}
