import { X } from "lucide-react";
import { useState } from "react";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import { scenesOf } from "../../lib/assetText";
import type { Asset, AssetDetail } from "../../lib/types";

export interface ScenePatch { script_asset_id: string; scene_number: number; prompt: string; character_ids: string[] }
export interface SectionPatch { story_asset_id: string; story_section: string; prompt: string }

/** Optional video context: one scene of a project script, or one section of a project story. Selecting nothing is fine.
 *  Only the chosen scene/section is used; nothing else from the story or script is sent. */
export function VideoContextPicker({ projectId, options, onScene, onSection, onClear }: {
  projectId: string; options: Record<string, unknown>; onScene: (p: ScenePatch) => void; onSection: (p: SectionPatch) => void; onClear: (keys: string[]) => void;
}) {
  const scripts = useAsync(() => (projectId ? api<Asset[]>(`/api/projects/${projectId}/assets?type=SCRIPT`) : Promise.resolve([])), [projectId]);
  const stories = useAsync(() => (projectId ? api<Asset[]>(`/api/projects/${projectId}/assets?type=STORY`) : Promise.resolve([])), [projectId]);
  const [script, setScript] = useState<AssetDetail | null>(null);
  const [story, setStory] = useState<{ id: string; sections: { key: string; text: string }[] } | null>(null);
  const [scene, setScene] = useState(1);
  const [section, setSection] = useState("");
  const [error, setError] = useState("");
  if (!projectId || (!scripts.data?.length && !stories.data?.length)) return null;

  const pickScript = async (id: string) => {
    setError(""); setScript(null);
    if (!id) return;
    try { const d = await api<AssetDetail>(`/api/assets/${id}`); setScript(d); setScene(scenesOf(d)[0]?.number ?? 1); } catch (e) { setError(errorMessage(e)); }
  };
  const pickStory = async (id: string) => {
    setError(""); setStory(null);
    if (!id) return;
    try { const r = await api<{ sections: { key: string; text: string }[] }>(`/api/assets/${id}/sections`); setStory({ id, sections: r.sections }); setSection(r.sections.find((s) => s.key.startsWith("ACT"))?.key ?? r.sections[0]?.key ?? ""); }
    catch (e) { setError(errorMessage(e)); }
  };
  const useScene = async () => {
    if (!script) return;
    try {
      const d = await api<{ video_prompt: string; character_ids: string[] }>(`/api/assets/${script.id}/scenes/${scene}`);
      onScene({ script_asset_id: script.id, scene_number: scene, prompt: d.video_prompt, character_ids: d.character_ids });
    } catch (e) { setError(errorMessage(e)); }
  };
  const useSection = () => {
    const s = story?.sections.find((x) => x.key === section);
    if (story && s) onSection({ story_asset_id: story.id, story_section: s.key, prompt: s.text.slice(0, 300) });
  };
  const activeScene = options.script_asset_id && options.scene_number, activeSection = options.story_section;

  return (
    <details className="rounded-lg border border-border bg-raised/40 p-3" open={!!(activeScene || activeSection)}>
      <summary className="cursor-pointer text-sm font-medium">Use project context <span className="font-normal text-muted">(optional: a script scene or a story section)</span></summary>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      <div className="mt-3 space-y-3">
        {!!scripts.data?.length && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-0 flex-1 basis-44"><span className="mb-1 block text-xs text-muted">Script</span>
              <select className="field" defaultValue="" onChange={(e) => void pickScript(e.target.value)}><option value="">Choose a script…</option>
                {scripts.data.map((a) => <option key={a.id} value={a.id}>{a.title} · v{a.version}</option>)}</select></label>
            {script && <label><span className="mb-1 block text-xs text-muted">Scene</span>
              <select className="field" value={scene} onChange={(e) => setScene(Number(e.target.value))}>
                {scenesOf(script).map((s) => <option key={s.number} value={s.number}>Scene {s.number}: {s.heading.slice(0, 36)}</option>)}</select></label>}
            {script && <button type="button" className="btn-secondary" onClick={useScene}>Use this scene</button>}
          </div>)}
        {!!stories.data?.length && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-0 flex-1 basis-44"><span className="mb-1 block text-xs text-muted">Story</span>
              <select className="field" defaultValue="" onChange={(e) => void pickStory(e.target.value)}><option value="">Choose a story…</option>
                {stories.data.map((a) => <option key={a.id} value={a.id}>{a.title} · v{a.version}</option>)}</select></label>
            {story && <label><span className="mb-1 block text-xs text-muted">Section</span>
              <select className="field" value={section} onChange={(e) => setSection(e.target.value)}>{story.sections.map((s) => <option key={s.key} value={s.key}>{s.key}</option>)}</select></label>}
            {story && <button type="button" className="btn-secondary" onClick={useSection}>Use this section</button>}
          </div>)}
        <div className="flex flex-wrap gap-2">
          {!!activeScene && <Chip onRemove={() => onClear(["script_asset_id", "scene_number"])}>Scene {String(options.scene_number)} of your script</Chip>}
          {!!activeSection && <Chip onRemove={() => onClear(["story_asset_id", "story_section"])}>Story: {String(options.story_section)}</Chip>}
        </div>
        <p className="text-xs text-muted">Each video is a separate generation. Only what you choose here is used as context, and you still confirm the refined prompt before anything is generated.</p>
      </div>
    </details>
  );
}

const Chip = ({ children, onRemove }: { children: React.ReactNode; onRemove: () => void }) => (
  <span className="inline-flex items-center gap-1 rounded-full border border-accent bg-accent/10 py-0.5 pl-3 pr-1 text-sm">{children}
    <button type="button" className="btn-ghost !p-1" aria-label="Remove" onClick={onRemove}><X className="h-3.5 w-3.5" /></button></span>
);
