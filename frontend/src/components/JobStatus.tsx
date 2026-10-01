import { Check, Circle, Loader2, X } from "lucide-react";
import type { Job } from "../lib/types";

type Step = { label: string; stages: string[] };

const WORDS: Step[] = [{ label: "Queued", stages: ["QUEUED", "RETRYING"] }, { label: "Preparing", stages: ["PREPARING"] },
  { label: "Generating", stages: ["GENERATING"] }, { label: "Saving result", stages: ["STORING", "PROCESSING"] }];
const REMOTE: Step[] = [{ label: "Queued", stages: ["QUEUED", "RETRYING"] }, { label: "Preparing", stages: ["PREPARING"] },
  { label: "Submitting", stages: ["SUBMITTING"] }, { label: "Provider processing", stages: ["GENERATING"] },
  { label: "Downloading", stages: ["DOWNLOADING"] }, { label: "Storing", stages: ["STORING", "PROCESSING"] }];
const MOVIE: Step[] = [WORDS[0], WORDS[1], { label: "Assembling", stages: ["ASSEMBLING"] }, { label: "Finalizing", stages: ["FINALIZING", "STORING", "PROCESSING"] }];
const ONE_CALL: Step[] = [WORDS[0], WORDS[1], { label: "Provider processing", stages: ["GENERATING"] }, WORDS[3]];

/** Which stages a generator really goes through (matches what the worker reports). */
function flowFor(type?: string): Step[] {
  if (type === "movie") return MOVIE;
  if (type === "video" || type === "face_replacement") return REMOTE;
  if (type === "music" || type === "voice") return ONE_CALL;
  return WORDS;
}

/** Stage checklist. Only real information is shown: stages come from the worker/provider, and a percentage appears only if the provider reports one. */
export function JobStatus({ job, title, type }: { job: Pick<Job, "status" | "stage" | "progress" | "error_message" | "attempts">; title?: string; type?: string }) {
  const steps = flowFor(type);
  const found = steps.findIndex((s) => s.stages.includes(job.stage));
  const current = found === -1 ? 0 : found;
  const failed = job.status === "FAILED", cancelled = job.status === "CANCELLED", done = job.status === "COMPLETED";
  const row = (label: string, state: "done" | "active" | "todo" | "failed" | "stopped") => (
    <li key={label} className={`flex items-center gap-2.5 text-sm ${state === "todo" ? "text-muted" : ""} ${state === "failed" ? "text-danger" : ""}`}>
      {state === "done" && <Check className="h-4 w-4 text-success" aria-hidden />}
      {state === "active" && <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden />}
      {state === "todo" && <Circle className="h-4 w-4" aria-hidden />}
      {(state === "failed" || state === "stopped") && <X className="h-4 w-4" aria-hidden />}
      <span>{label}{state === "active" && job.progress != null ? ` — ${job.progress}%` : ""}</span>
    </li>
  );
  return (
    <div>
      {title && <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-muted">{title}</p>}
      <ol aria-label="Generation progress" className="space-y-2">
        {type !== "movie" && row("Prompt refined", "done")}
        {row(type === "movie" ? "Assembly requested" : "Job submitted", "done")}
        {failed || cancelled ? row(failed ? "Failed" : "Cancelled", failed ? "failed" : "stopped")
          : steps.map((s, i) => row(i === 0 && job.status === "RETRYING" ? "Retrying" : s.label, done || i < current ? "done" : i === current ? "active" : "todo"))}
        {!failed && !cancelled && row("Completed", done ? "done" : "todo")}
      </ol>
      {job.status === "RETRYING" && <p className="mt-2 text-xs text-muted">A temporary problem occurred; retrying automatically (attempt {job.attempts + 1}).</p>}
      {failed && job.error_message && <p className="mt-2 text-sm text-danger">{job.error_message}</p>}
      {job.status === "PROCESSING" && job.progress == null && <p className="mt-2 text-xs text-muted">The provider doesn't report a percentage, so only the current stage is shown.</p>}
    </div>
  );
}
