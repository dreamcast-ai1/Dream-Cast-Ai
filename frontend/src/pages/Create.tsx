import { Info, Wand2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { CharacterPicker } from "../components/create/CharacterPicker";
import { OptionsForm } from "../components/create/OptionsForm";
import { RefinedPanel } from "../components/create/RefinedPanel";
import { ReferencePicker } from "../components/create/ReferencePicker";
import { FaceInputs } from "../components/create/FaceInputs";
import { TextSourcePicker } from "../components/create/TextSourcePicker";
import { VideoContextPicker } from "../components/create/VideoContextPicker";
import { JobStatus } from "../components/JobStatus";
import { SelectField } from "../components/ui/Field";
import { Alert, ErrorState, PageHeader, PageLoader, Spinner } from "../components/ui/feedback";
import { useAsync } from "../hooks/useAsync";
import { usePolling } from "../hooks/usePolling";
import { api, errorMessage } from "../lib/api";
import { dialogueOf, scenesOf, sceneText } from "../lib/assetText";
import { ACTIVE_STATUSES, type AssetDetail, type GeneratorSchema, type Job, type Project, type RefineResult, type UsageItem } from "../lib/types";

type Options = Record<string, unknown>;

function defaultsFor(s?: GeneratorSchema): Options {
  const o: Options = {};
  s?.fields.forEach((f) => { if (f.default != null) o[f.key] = f.default; });
  return o;
}

export default function Create() {
  const { generator: routeGen } = useParams();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const schema = useAsync(() => api<{ generators: GeneratorSchema[]; max_upload_mb: number; max_video_upload_mb: number }>("/api/generate/schema"));
  const projects = useAsync(() => api<Project[]>("/api/projects"));
  const usage = useAsync(() => api<{ items: UsageItem[] }>("/api/usage"));

  const [projectId, setProjectId] = useState(params.get("project") ?? "");
  const [options, setOptions] = useState<Options>({});
  const [prompt, setPrompt] = useState("");
  const [charIds, setCharIds] = useState<string[] | null>(null);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [faceId, setFaceId] = useState("");
  const [permission, setPermission] = useState(false);
  const [refined, setRefined] = useState<RefineResult | null>(null);
  const [refinedText, setRefinedText] = useState("");
  const [parentId, setParentId] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "refine" | "generate">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [started, setStarted] = useState<Job | null>(null);

  const generators = schema.data?.generators ?? [];
  const selected = generators.find((g) => g.id === routeGen);
  const remaining = useMemo(() => usage.data?.items.find((u) => u.generator === selected?.id), [usage.data, selected]);

  // Reset when the generator changes, then apply hand-offs from other screens (e.g. "Generate Script" on a story).
  const fromId = params.get("from");
  useEffect(() => {
    if (fromId || !selected) return;
    const o = defaultsFor(selected);
    const story = params.get("story"), lyrics = params.get("lyrics"), script = params.get("script"), scene = params.get("scene"), source = params.get("source");
    if (story && selected.id === "script") o.story_asset_id = story;
    if (lyrics && selected.id === "music") o.lyrics_asset_id = lyrics;
    if (script && scene && selected.id === "music") { o.script_asset_id = script; o.scene_number = Number(scene); }
    setOptions(o); setRefined(null); setRefIds([]); setCharIds(selected.id === "video" ? [] : null); setError(""); setNotice("");
    setSourceId(""); setFaceId(""); setPermission(false);
    if (selected.id === "video" && script && scene) {
      api<{ video_prompt: string; character_ids: string[] }>(`/api/assets/${script}/scenes/${scene}`).then((d) => {
        setOptions({ ...o, script_asset_id: script, scene_number: Number(scene) }); setPrompt(d.video_prompt); setCharIds(d.character_ids);
        setNotice(`Scene ${scene} of your script was used to draft this prompt. Edit it if you like, then refine.`);
      }).catch((e) => setError(errorMessage(e)));
    } else if (selected.id === "video" && story && params.get("section")) {
      api<{ sections: { key: string; text: string }[] }>(`/api/assets/${story}/sections`).then((d) => {
        const sec = d.sections.find((x) => x.key === params.get("section"));
        if (sec) { setOptions({ ...o, story_asset_id: story, story_section: sec.key }); setPrompt(sec.text.slice(0, 300)); setNotice(`${sec.key} of your story is used as context.`); }
      }).catch((e) => setError(errorMessage(e)));
    } else if (source && selected.id === "voice") {
      api<AssetDetail>(`/api/assets/${source}`).then((d) => {
        let text = d.text_content ?? "";
        const s = scene ? scenesOf(d).find((x) => x.number === Number(scene)) : undefined;
        if (s) text = params.get("part") === "dialogue" ? dialogueOf(sceneText(text, s)) || sceneText(text, s) : sceneText(text, s);
        applyText(text, selected.prompt.max_length);
      }).catch((e) => setError(errorMessage(e)));
    } else setPrompt("");
  }, [selected?.id, fromId]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyText = (text: string, max: number, note = "") => {
    const cut = text.length > max;
    setPrompt(cut ? text.slice(0, max) : text); setRefined(null);
    setNotice(note || (cut ? `The text is longer than ${max} characters and was shortened. Choose a scene to narrow it down.` : ""));
  };

  // "Edit Prompt" from a generation's detail page: prefill everything and open the review step.
  useEffect(() => {
    if (!fromId || !schema.data) return;
    api<Job>(`/api/jobs/${fromId}`).then((j) => {
      if (routeGen !== j.type) { nav(`/create/${j.type}?from=${fromId}`, { replace: true }); return; }
      setPrompt(j.original_prompt); setOptions(j.options); setProjectId(j.project_id ?? ""); setRefIds(j.reference_assets);
      setCharIds((j.options.character_ids as string[] | undefined) ?? null);
      setRefined({ refined_prompt: j.refined_prompt, structured_prompt: {}, metadata: { method: "previous", provider: null, model: null, warnings: [], options: j.options, context_used: {} } });
      setRefinedText(j.refined_prompt); setParentId(j.id);
    }).catch((e) => setError(errorMessage(e)));
  }, [fromId, schema.data, routeGen]); // eslint-disable-line react-hooks/exhaustive-deps

  const face = selected?.ui === "face";
  const faceOptions = face ? { source_asset_id: sourceId, face_asset_id: faceId, permission_confirmed: permission } : {};
  const references = face ? [sourceId, faceId].filter(Boolean) : refIds;
  const body = () => ({ generator_type: selected!.id, prompt, project_id: projectId || null, reference_assets: references,
    options: { ...options, ...faceOptions, ...(charIds ? { character_ids: charIds } : {}) } });

  const refine = async () => {
    if (!selected) return;
    setBusy("refine"); setError("");
    try {
      const r = await api<RefineResult>("/api/generate/refine", { method: "POST", json: body() });
      setRefined(r); setRefinedText(r.refined_prompt); setOptions({ ...options, ...r.metadata.options });
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(""); }
  };

  const generate = async () => {
    if (!selected || !refined) return;
    setBusy("generate"); setError("");
    try {
      const b = body();
      const r = await api<{ job_id: string; status: string }>("/api/generations", { method: "POST", json: {
        generator_type: b.generator_type, original_prompt: prompt, refined_prompt: refinedText, options: { ...refined.metadata.options, ...faceOptions, ...(charIds ? { character_ids: charIds } : {}) },
        project_id: b.project_id, reference_assets: references, parent_id: parentId } });
      setStarted(await api<Job>(`/api/jobs/${r.job_id}`));
      void usage.reload();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(""); }
  };

  const refreshStarted = async () => { if (started) setStarted(await api<Job>(`/api/jobs/${started.id}`).catch(() => started)); };
  usePolling(refreshStarted, !!started && ACTIVE_STATUSES.includes(started.status), 4000);

  const reset = () => { setStarted(null); setRefined(null); setPrompt(""); setParentId(null); setRefIds([]); setOptions(defaultsFor(selected)); setError(""); setNotice(""); if (fromId || params.toString()) nav(`/create/${selected?.id}`, { replace: true }); };

  if (schema.loading) return <PageLoader />;
  if (schema.error) return <ErrorState message={schema.error} onRetry={schema.reload} />;

  const needsFace = face && (!sourceId || !faceId);
  const isImageVideo = selected?.id === "video" && options.method === "Image to Video";
  const needsImage = isImageVideo && refIds.length === 0;
  const promptTooShort = !!selected && selected.prompt.required && !isImageVideo && prompt.trim().length < 3;
  const musicEmpty = selected?.id === "music" && !prompt.trim() && !options.genre && !options.mood;
  const refineDisabled = busy !== "" || !selected || promptTooShort || musicEmpty || needsFace || needsImage;
  const blocked = face && !permission ? "Confirm that you have permission to use the uploaded face/image to continue." : !selected?.available ? selected?.unavailable_reason ?? "This generator isn't available." : remaining && remaining.used >= remaining.limit ? `You've used all ${remaining.limit} ${selected?.label} generations for today. The limit resets at midnight UTC.` : null;
  const sceneChip = options.scene_number && options.script_asset_id ? `Scene ${options.scene_number} of your script is used as context` : "";
  const isVoice = selected?.id === "voice";

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="What do you want to create?" subtitle="Pick a generator, describe your idea in your own words, and review the refined prompt before anything is generated." />

      <div role="radiogroup" aria-label="Generator" className="flex flex-wrap gap-2">
        {generators.map((g) => (
          <button key={g.id} role="radio" aria-checked={g.id === routeGen} disabled={busy !== ""} onClick={() => { setStarted(null); nav(`/create/${g.id}${projectId ? `?project=${projectId}` : ""}`, { replace: true }); }}
            className={`flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ${g.id === routeGen ? "border-accent bg-accent/15 text-fg" : "border-border text-muted hover:border-muted hover:text-fg"}`}>
            <span aria-hidden>{g.emoji}</span>{g.label}</button>))}
      </div>

      {!selected ? <p className="mt-8 text-center text-muted">Choose a generator above to begin.</p> : started ? (
        <section aria-live="polite" className="card mt-6 p-5">
          <Alert kind="success">Generation started. Your {selected.label.toLowerCase()} is being generated in the background — you can leave this page and you'll get a notification when it finishes.</Alert>
          <div className="mt-5"><JobStatus job={started} title={`${selected.label} generation`} type={started.type} /></div>
          <div className="mt-6 flex flex-wrap gap-2">
            {started.project_id && <Link className="btn-secondary" to={`/projects/${started.project_id}`}>View project</Link>}
            <Link className="btn-secondary" to={`/history/${started.id}`}>View generation</Link>
            <button className="btn-primary" onClick={reset}>Continue creating</button>
          </div>
        </section>
      ) : (
        <>
          <div className="card mt-6 space-y-5 p-4 sm:p-5">
            {selected.simulated && <Alert kind="info"><strong>Development simulator active.</strong> No real {selected.label.toLowerCase()} provider is connected yet, so a submitted job runs through the pipeline and produces a clearly-labelled simulated result — no real content.</Alert>}
            {!selected.available && <Alert kind="error">{selected.unavailable_reason}</Alert>}
            {selected.available && !selected.configured && <Alert kind="info"><strong>{selected.config_message}</strong> You can still set up your request, but generating will fail with this message until an administrator configures the provider (see the README).</Alert>}
            <div className="max-w-sm"><SelectField label="Project (optional)" value={projectId} onChange={(e) => { setProjectId(e.target.value); setRefIds([]); setCharIds(null); setRefined(null); setOptions((o) => { const c = { ...o }; ["story_asset_id", "lyrics_asset_id", "script_asset_id", "scene_number"].forEach((k) => delete c[k]); return c; }); }}>
              <option value="">No project</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</SelectField>
              <p className="mt-1 text-xs text-muted">The result is saved to the project as a new version, and only relevant project context is used. With no project it is saved in “Quick creations”.</p></div>
            <OptionsForm schema={selected} options={options} projectId={projectId} onChange={(o) => { setOptions(o); setRefined(null); }} />
            {sceneChip && <p className="flex items-center gap-2 text-sm"><span className="rounded-full border border-accent bg-accent/10 px-3 py-1">{sceneChip}</span>
              <button type="button" className="btn-ghost !p-1" aria-label="Remove scene context" onClick={() => { setOptions(({ script_asset_id: _s, scene_number: _n, ...rest }) => rest); setRefined(null); }}><X className="h-4 w-4" /></button></p>}
            {selected.uses_characters && <CharacterPicker projectId={projectId} selected={charIds} onChange={(c) => { setCharIds(c); setRefined(null); }} />}
            {selected.id === "video" && <VideoContextPicker projectId={projectId} options={options}
              onScene={(p) => { setOptions((o) => ({ ...o, script_asset_id: p.script_asset_id, scene_number: p.scene_number })); setPrompt(p.prompt); setCharIds(p.character_ids); setRefined(null); setNotice(`Scene ${p.scene_number} of your script drafted this prompt. Edit it if you like, then refine.`); }}
              onSection={(p) => { setOptions((o) => ({ ...o, story_asset_id: p.story_asset_id, story_section: p.story_section })); if (!prompt.trim()) setPrompt(p.prompt); setRefined(null); }}
              onClear={(keys) => { setOptions((o) => { const c = { ...o }; keys.forEach((k) => delete c[k]); return c; }); setRefined(null); }} />}
            {selected.reference && !face && <ReferencePicker key={`${projectId}-${isImageVideo}`} schema={selected} projectId={projectId} selected={refIds} maxMb={schema.data!.max_upload_mb} onChange={(ids) => { setRefIds(ids); setRefined(null); }}
              label={isImageVideo ? "Source image" : "Reference images (optional)"} required={isImageVideo} single={isImageVideo}
              hint={isImageVideo ? "This image is sent to the video provider as the first frame." : selected.id === "video" ? "Described in the prompt; not sent as images to the provider." : ""} />}
            {face && <FaceInputs projectId={projectId} sourceId={sourceId} faceId={faceId} permission={permission} maxImageMb={schema.data!.max_upload_mb} maxVideoMb={schema.data!.max_video_upload_mb}
              videoSupported={((selected.provider_info as { sources?: string[] }).sources ?? []).includes("video")}
              onSource={(id) => { setSourceId(id); setRefined(null); }} onFace={(id) => { setFaceId(id); setRefined(null); }} onPermission={setPermission} />}
            <div>
              <label htmlFor="prompt" className="mb-1.5 block text-sm font-medium">{selected.prompt.label}{selected.prompt.required && <span className="text-danger"> *</span>}</label>
              {isVoice && <div className="mb-3"><TextSourcePicker projectId={projectId} maxLength={selected.prompt.max_length} onPick={(t, n) => applyText(t, selected.prompt.max_length, n)} /></div>}
              <textarea id="prompt" value={prompt} onChange={(e) => { setPrompt(e.target.value); setRefined(null); }} rows={5} maxLength={selected.prompt.max_length}
                placeholder={selected.prompt.placeholder} className="field min-h-[8rem] resize-y text-base" />
              <p className="mt-1 text-right text-xs text-muted">{prompt.length} / {selected.prompt.max_length}</p>
              {notice && <div className="mt-2"><Alert kind="info">{notice}</Alert></div>}
            </div>
            {selected.note && <p className="flex items-start gap-2 text-sm text-muted"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{selected.note}</p>}
            {error && !refined && <Alert kind="error">{error}</Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-muted">{remaining ? `${remaining.used} / ${remaining.limit} ${selected.label} generations used today` : ""}</p>
              <button className="btn-primary w-full sm:w-auto" onClick={refine} disabled={refineDisabled}>{busy === "refine" ? <Spinner /> : <Wand2 className="h-4 w-4" aria-hidden />} {isVoice || face ? "Prepare request" : "Refine prompt"}</button>
            </div>
            {needsFace && <p className="text-xs text-muted">Choose a source and a face to continue.</p>}
            {needsImage && <p className="text-xs text-muted">Choose or upload a source image for image-to-video.</p>}
          </div>
          {refined && <>
            {error && <div className="mt-4"><Alert kind="error">{error}</Alert></div>}
            <RefinedPanel result={refined} text={refinedText} onText={setRefinedText} onRegenerate={refine} onGenerate={generate}
              regenerating={busy === "refine"} generating={busy === "generate"} blockedReason={blocked} generateLabel={`Generate ${selected.label}`} heading={isVoice ? "Text to be spoken" : face ? "Your request (saved with the generation)" : undefined} />
          </>}
        </>
      )}
    </div>
  );
}
