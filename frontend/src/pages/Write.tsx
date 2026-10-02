import { ArrowRight, Check, Copy, RotateCcw, Wand2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { SelectField, TextArea } from "../components/ui/Field";
import { Alert, EmptyState, ErrorState, PageHeader, PageLoader, Spinner } from "../components/ui/feedback";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import type { Project, SavedText, ScriptResult, ScriptScene, StoryResult, TextCharacter, TextOptions } from "../lib/types";

const TABS = [{ id: "story", label: "Story Generator" }, { id: "script", label: "Story to Script" }];
const UNAVAILABLE = "Text generation is currently unavailable: the text provider is not configured. Everything else keeps working; ask an administrator to add the text provider key.";
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Copy to the clipboard (with the same fallback the asset page uses for older browsers). */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); }
    catch { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
    setDone(true); setTimeout(() => setDone(false), 2000);
  };
  return <button type="button" className="btn-secondary" onClick={copy}>{done ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}{done ? "Copied" : label}</button>;
}

function ProjectPicker({ projects, value, onChange }: { projects: Project[]; value: string; onChange: (id: string) => void }) {
  return (
    <SelectField label="Save to a project (optional)" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Don't save (just show it here)</option>
      {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
    </SelectField>
  );
}

function SavedNote({ saved, tab }: { saved: SavedText | null; tab: string }) {
  if (!saved) return null;
  return <Alert kind="success">Saved to your project. <Link className="underline" to={`/projects/${saved.project_id}/assets/${saved.asset_id}`}>Open it</Link>{tab === "script" && <> · <Link className="underline" to={`/projects/${saved.project_id}?tab=movie`}>Create movie scenes from it</Link></>}</Alert>;
}

function Choice({ label, value, onChange, choices, blank }: { label: string; value: string; onChange: (v: string) => void; choices: string[]; blank: string }) {
  return <SelectField label={label} value={value} onChange={(e) => onChange(e.target.value)}><option value="">{blank}</option>{choices.map((c) => <option key={c} value={c}>{c}</option>)}</SelectField>;
}

function CharacterChips({ characters }: { characters: TextCharacter[] }) {
  if (!characters.length) return null;
  return <ul className="flex flex-wrap gap-2 text-xs">{characters.map((c) => <li key={c.name} className="rounded-full border border-border px-3 py-1"><span className="font-semibold">{c.name}</span>{c.description && <span className="text-muted"> · {c.description}</span>}</li>)}</ul>;
}

/** The text you can change freely before using it: it is what "Convert Story to Script" sends, and what Copy copies. */
function Reviewer({ label, hint, value, original, onChange, rows }: { label: string; hint: string; value: string; original: string; onChange: (v: string) => void; rows: number }) {
  return (
    <div className="space-y-2">
      <TextArea label={label} value={value} onChange={(e) => onChange(e.target.value)} rows={rows} hint={hint} />
      {value !== original && <button type="button" className="btn-ghost" onClick={() => onChange(original)}><RotateCcw className="h-4 w-4" aria-hidden /> Undo my edits</button>}
    </div>
  );
}

function StoryGenerator({ opts, projects, onUseStory }: { opts: TextOptions; projects: Project[]; onUseStory: (text: string) => void }) {
  const [prompt, setPrompt] = useState(""); const [genre, setGenre] = useState(""); const [tone, setTone] = useState("");
  const [length, setLength] = useState("Medium"); const [language, setLanguage] = useState("English"); const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [result, setResult] = useState<StoryResult | null>(null);
  const [draft, setDraft] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (prompt.trim().length < 5) { setError("Describe your story in at least a few words."); return; }
    setBusy(true); setError("");
    try {
      const r = await api<StoryResult>("/api/text/story", { method: "POST", json: { prompt: prompt.trim(), genre, tone, length, language, project_id: projectId || null } });
      setResult(r); setDraft(r.text);
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };
  const sections: [string, string][] = result ? [["Beginning", result.beginning], ["Middle", result.middle], ["Climax", result.climax], ["Ending", result.ending]] : [];
  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="card space-y-4 p-5">
        <TextArea label="What is your story about?" value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={opts.limits.prompt} rows={4}
          placeholder="e.g. A lighthouse keeper's daughter must relight the lantern before a storm closes the harbour" hint={`${prompt.length} / ${opts.limits.prompt}`} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Choice label="Genre (optional)" value={genre} onChange={setGenre} choices={opts.genres} blank="Any genre" />
          <Choice label="Tone (optional)" value={tone} onChange={setTone} choices={opts.tones} blank="Any tone" />
          <SelectField label="Length" value={length} onChange={(e) => setLength(e.target.value)}>{opts.lengths.map((l) => <option key={l}>{l}</option>)}</SelectField>
          <SelectField label="Language" value={language} onChange={(e) => setLanguage(e.target.value)}>{opts.languages.map((l) => <option key={l}>{l}</option>)}</SelectField>
        </div>
        <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
        {error && <Alert kind="error">{error}</Alert>}
        <button className="btn-primary" disabled={busy || !opts.configured}>{busy ? <><Spinner className="!h-4 !w-4" /> Writing your story…</> : <><Wand2 className="h-4 w-4" aria-hidden /> Generate Story</>}</button>
        {busy && <p className="text-xs text-muted" role="status">This can take up to a minute. Please keep this page open.</p>}
      </form>
      {result && (
        <article className="card space-y-5 p-5" aria-label="Generated story">
          <div><p className="text-xs font-semibold uppercase tracking-widest text-accent">Story</p><h2 className="mt-1 text-2xl font-semibold">{result.title}</h2>
            {result.logline && <p className="mt-1 text-sm italic text-muted">{result.logline}</p>}</div>
          {result.setting && <p className="text-sm"><span className="font-semibold">Setting: </span>{result.setting}</p>}
          <CharacterChips characters={result.characters} />
          <dl className="grid gap-3 sm:grid-cols-2">{sections.filter(([, v]) => v).map(([k, v]) => <div key={k} className="rounded-lg border border-border p-3"><dt className="text-xs font-semibold uppercase tracking-widest text-muted">{k}</dt><dd className="mt-1 text-sm">{v}</dd></div>)}</dl>
          <div className="space-y-3 text-sm leading-relaxed" aria-label="Full story">{result.full_story.split(/\n\s*\n/).map((p, i) => <p key={i}>{p}</p>)}</div>
          <p className="text-xs text-muted">{result.word_count} words · {result.language}{result.genre ? ` · ${result.genre}` : ""}{result.tone ? ` · ${result.tone}` : ""} · {result.remaining} stories left this period</p>
          <SavedNote saved={result.saved} tab="story" />
          <hr className="border-border" />
          <Reviewer label="Review and edit your story" original={result.text} value={draft} onChange={setDraft} rows={10}
            hint="Change anything you like. This is the text that gets converted to a script, and nothing is sent to image or video generation." />
          <div className="flex flex-wrap gap-2"><CopyButton text={draft} label="Copy story" />
            <button type="button" className="btn-primary" onClick={() => onUseStory(draft)} disabled={draft.trim().length < 40}>Convert Story to Script <ArrowRight className="h-4 w-4" aria-hidden /></button></div>
        </article>)}
    </div>
  );
}

function SceneCard({ s }: { s: ScriptScene }) {
  const detail: [string, string][] = [["Camera", s.camera], ["Sound / SFX", s.sound]];
  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">Scene {String(s.number).padStart(2, "0")}</p>
        <p className="text-xs text-muted">about {s.estimated_seconds} s</p>
      </div>
      <h3 className="mt-0.5 text-sm font-semibold">{s.heading || s.location}</h3>
      {s.action && <p className="mt-2 text-sm">{s.action}</p>}
      {s.narration && <p className="mt-2 text-sm italic text-muted">Narration: {s.narration}</p>}
      {s.dialogue.length > 0 && <ul className="mt-2 space-y-1 text-sm">{s.dialogue.map((d, i) => <li key={i}><span className="font-semibold">{d.speaker}:</span> “{d.line}”</li>)}</ul>}
      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs text-muted sm:grid-cols-2">{detail.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt className="inline font-medium text-fg">{k}: </dt><dd className="inline">{v}</dd></div>)}</dl>
      {s.transition && <p className="mt-2 text-right text-xs font-medium uppercase tracking-widest text-muted">{s.transition}</p>}
    </li>
  );
}

function ScriptGenerator({ opts, projects, story, setStory }: { opts: TextOptions; projects: Project[]; story: string; setStory: (s: string) => void }) {
  const [style, setStyle] = useState(""); const [format, setFormat] = useState(""); const [tone, setTone] = useState(""); const [duration, setDuration] = useState(""); const [language, setLanguage] = useState("English");
  const [instructions, setInstructions] = useState(""); const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [result, setResult] = useState<ScriptResult | null>(null); const [draft, setDraft] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (story.trim().length < 40) { setError("Paste or write the story first (at least a few sentences)."); return; }
    setBusy(true); setError("");
    try {
      const r = await api<ScriptResult>("/api/text/script", { method: "POST", json: { story: story.trim(), style, tone, script_format: format, duration_minutes: duration ? Number(duration) : null, language, instructions, project_id: projectId || null } });
      setResult(r); setDraft(r.text);
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="card space-y-4 p-5">
        <TextArea label="Your story" value={story} onChange={(e) => setStory(e.target.value)} maxLength={opts.limits.story} rows={9}
          placeholder="Paste or write your story here, or generate one in the Story Generator tab." hint={`${story.length} / ${opts.limits.story}. The script keeps your characters, events and ending.`} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Choice label="Script format (optional)" value={format} onChange={setFormat} choices={opts.script_formats.filter((f) => f !== "Screenplay")} blank="Screenplay (default)" />
          <Choice label="Script style (optional)" value={style} onChange={setStyle} choices={opts.script_styles} blank="Default" />
          <Choice label="Tone (optional)" value={tone} onChange={setTone} choices={opts.tones} blank="Match the story" />
          <SelectField label="Target duration (optional)" value={duration} onChange={(e) => setDuration(e.target.value)}><option value="">Automatic</option>{opts.durations.map((d) => <option key={d} value={d}>{d} minutes</option>)}</SelectField>
          <SelectField label="Language" value={language} onChange={(e) => setLanguage(e.target.value)}>{opts.languages.map((l) => <option key={l}>{l}</option>)}</SelectField>
        </div>
        <TextArea label="Notes for the writer (optional)" value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={opts.limits.instructions} rows={2}
          hint="The story isn't changed unless you ask for a change here." />
        <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
        {error && <Alert kind="error">{error}</Alert>}
        <button className="btn-primary" disabled={busy || !opts.configured}>{busy ? <><Spinner className="!h-4 !w-4" /> Writing your script…</> : <><Wand2 className="h-4 w-4" aria-hidden /> Convert Story to Script</>}</button>
        {busy && <p className="text-xs text-muted" role="status">This can take up to a minute. Please keep this page open.</p>}
      </form>
      {result && (
        <article className="card space-y-5 p-5" aria-label="Generated script">
          <div><p className="text-xs font-semibold uppercase tracking-widest text-accent">Script</p><h2 className="mt-1 text-2xl font-semibold">{result.title}</h2>
            {result.logline && <p className="mt-1 text-sm italic text-muted">{result.logline}</p>}</div>
          <CharacterChips characters={result.characters} />
          <ol className="space-y-3">{result.scenes.map((s) => <SceneCard key={s.number} s={s} />)}</ol>
          <p className="text-xs text-muted">{result.scene_count} scenes · about {mmss(result.estimated_total_seconds)} of screen time · {result.word_count} words · {result.language} · {result.remaining} scripts left this period</p>
          <SavedNote saved={result.saved} tab="script" />
          <hr className="border-border" />
          <Reviewer label="Review and edit your script" original={result.text} value={draft} onChange={setDraft} rows={14}
            hint="Change anything you like, then copy it. To keep edits with a project, open the saved script from the link above and edit it there." />
          <div className="flex flex-wrap gap-2"><CopyButton text={draft} label="Copy script" /></div>
        </article>)}
    </div>
  );
}

/** Two writing tools on the text provider: Prompt -> Story and Story -> Script, each with a review/edit step. The provider key stays on the server. */
export default function Write() {
  const { mode } = useParams();
  const nav = useNavigate();
  const opts = useAsync(() => api<TextOptions>("/api/text/options"));
  const projects = useAsync(() => api<Project[]>("/api/projects"));
  const on = (g: string) => opts.data?.generators?.[g] !== false;        // an administrator can switch Story and Script off separately
  const tab = mode === "script" ? (on("script") ? "script" : "story") : (on("story") ? "story" : "script");
  const [story, setStory] = useState("");
  if (opts.loading) return <PageLoader />;
  if (opts.error || !opts.data) return <ErrorState message={opts.error ?? "Couldn't load the writing tools."} onRetry={opts.reload} />;
  if (!on("story") && !on("script")) return <EmptyState icon="🚫" title="Writing tools are not available right now." hint="An administrator has switched them off. Your saved stories and scripts are still in your projects." />;
  const tabs = TABS.filter((t) => on(t.id));
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Write" subtitle="Turn an idea into a story, review it, then convert it to a script. Nothing is sent to image or video generation unless you choose to." />
      {!opts.data.configured && <div className="mb-4"><Alert kind="info">{UNAVAILABLE}</Alert></div>}
      <Tabs label="Writing tools" tabs={tabs} active={tab} onChange={(id) => nav(id === "script" ? "/write/script" : "/write", { replace: true })} />
      <TabPanel id={tab}>
        {tab === "story"
          ? <StoryGenerator opts={opts.data} projects={projects.data ?? []} onUseStory={(t) => { setStory(t); nav("/write/script"); }} />
          : <ScriptGenerator opts={opts.data} projects={projects.data ?? []} story={story} setStory={setStory} />}
      </TabPanel>
    </div>
  );
}
