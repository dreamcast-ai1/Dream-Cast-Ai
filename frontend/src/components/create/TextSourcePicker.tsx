import { useState } from "react";
import { useAsync } from "../../hooks/useAsync";
import { api, errorMessage } from "../../lib/api";
import { ASSET_LABEL, dialogueOf, scenesOf, sceneText } from "../../lib/assetText";
import type { Asset, AssetDetail } from "../../lib/types";

type Mode = "all" | "scene" | "dialogue";

/** Voice: optionally take the text to speak from a project story, script (whole, a scene, or a scene's dialogue) or lyrics.
 *  Nothing is generated from this; it only fills the text box, so the user reviews it before anything is sent. */
export function TextSourcePicker({ projectId, maxLength, onPick }: { projectId: string; maxLength: number; onPick: (text: string, note: string) => void }) {
  const { data } = useAsync(() => (projectId ? api<Asset[]>(`/api/projects/${projectId}/assets?type=STORY,SCRIPT,LYRICS`) : Promise.resolve([])), [projectId]);
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [mode, setMode] = useState<Mode>("all");
  const [scene, setScene] = useState(1);
  const [error, setError] = useState("");
  if (!projectId || !data?.length) return null;

  const choose = async (id: string) => {
    setError(""); setDetail(null);
    if (!id) return;
    try { const d = await api<AssetDetail>(`/api/assets/${id}`); setDetail(d); setMode("all"); setScene(scenesOf(d)[0]?.number ?? 1); }
    catch (e) { setError(errorMessage(e)); }
  };
  const scenes = detail ? scenesOf(detail) : [];
  const apply = () => {
    if (!detail?.text_content) return;
    const s = scenes.find((x) => x.number === scene);
    let text = mode !== "all" && s ? sceneText(detail.text_content, s) : detail.text_content;
    if (mode === "dialogue") text = dialogueOf(text) || text;
    const cut = text.length > maxLength;
    onPick(cut ? text.slice(0, maxLength) : text, cut ? `The selected text is longer than ${maxLength} characters and was shortened. Choose a scene to narrow it down.` : "");
  };

  return (
    <div className="rounded-lg border border-border bg-raised/40 p-3">
      <p className="mb-2 text-sm font-medium">Use text from your project <span className="font-normal text-muted">(optional)</span></p>
      {error && <p className="mb-2 text-sm text-danger">{error}</p>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 basis-48"><span className="sr-only">Source asset</span>
          <select className="field" defaultValue="" onChange={(e) => void choose(e.target.value)}>
            <option value="">Choose a story, script or lyrics…</option>
            {data.map((a) => <option key={a.id} value={a.id}>{ASSET_LABEL[a.type]}: {a.title} · v{a.version}</option>)}
          </select></label>
        {detail?.type === "SCRIPT" && scenes.length > 0 && (<>
          <label><span className="sr-only">Portion</span>
            <select className="field" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="all">Entire script</option><option value="scene">One scene</option><option value="dialogue">Scene dialogue only</option>
            </select></label>
          {mode !== "all" && <label><span className="sr-only">Scene</span>
            <select className="field" value={scene} onChange={(e) => setScene(Number(e.target.value))}>
              {scenes.map((s) => <option key={s.number} value={s.number}>Scene {s.number}: {s.heading.slice(0, 40)}</option>)}
            </select></label>}
        </>)}
        {detail && <button type="button" className="btn-secondary" onClick={apply}>Use this text</button>}
      </div>
    </div>
  );
}
