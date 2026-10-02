import { Info, Pencil, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import type { RefineResult } from "../../lib/types";
import { Alert, Spinner } from "../ui/feedback";

export function RefinedPanel({ result, text, onText, onRegenerate, onGenerate, regenerating, generating, blockedReason, generateLabel = "Generate", heading, maxLen = 8000 }:
  { result: RefineResult; text: string; onText: (t: string) => void; onRegenerate: () => void; onGenerate: () => void; regenerating: boolean; generating: boolean; blockedReason: string | null; generateLabel?: string; heading?: string; maxLen?: number }) {
  const [editing, setEditing] = useState(false);
  const m = result.metadata;
  // Honest labels: "AI refined" only when the AI really rewrote it; the built-in structured template is always "Basic refinement".
  const source = m.method === "llm" ? `AI refined${m.model ? ` · ${m.model}` : ""}` : m.method === "previous" ? "Loaded from a previous generation"
    : m.method === "none" ? "Not refined · your prompt as written" : "Basic refinement · built-in, no AI";
  return (
    <section aria-labelledby="refined-heading" className="card rise mt-6 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="refined-heading" className="font-sans text-xs font-semibold uppercase tracking-widest text-accent">{heading ?? "Your refined prompt"}</h2>
        <span className="text-xs text-muted">{source}</span>
      </div>
      {m.warnings.length > 0 && <div className="mt-3 space-y-2">{m.warnings.map((w) => <Alert key={w} kind="info">{w}</Alert>)}</div>}
      <label htmlFor="refined-text" className="sr-only">Refined prompt</label>
      <textarea id="refined-text" value={text} readOnly={!editing} onChange={(e) => onText(e.target.value)} maxLength={maxLen} rows={Math.min(14, Math.max(5, text.split("\n").length + 1))}
        className={`field mt-3 resize-y leading-relaxed ${editing ? "" : "cursor-default bg-raised"}`} />
      <p className="mt-1 text-xs text-muted">{editing ? "Your edits become the exact prompt that is submitted." : "Review it. Nothing is generated until you click Generate."}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn-secondary" onClick={() => setEditing((e) => !e)} aria-pressed={editing}><Pencil className="h-4 w-4" aria-hidden />{editing ? "Done editing" : "Edit"}</button>
        <button className="btn-secondary" onClick={onRegenerate} disabled={regenerating || generating}>{regenerating ? <Spinner /> : <RefreshCw className="h-4 w-4" aria-hidden />} Regenerate refinement</button>
        <button className="btn-primary ml-auto" onClick={onGenerate} disabled={generating || !!blockedReason || !text.trim()} aria-describedby={blockedReason ? "blocked" : undefined}>
          {generating ? <Spinner /> : <Sparkles className="h-4 w-4" aria-hidden />} {generating ? "Generating…" : generateLabel}</button>
      </div>
      {blockedReason && <p id="blocked" className="mt-3 flex items-start gap-2 text-sm text-warn"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{blockedReason}</p>}
    </section>
  );
}
