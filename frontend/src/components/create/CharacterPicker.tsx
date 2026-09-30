import { useAsync } from "../../hooks/useAsync";
import { api } from "../../lib/api";
import type { Character } from "../../lib/types";

/** Choose which project characters are sent as context. null = all (the default). */
export function CharacterPicker({ projectId, selected, onChange }: { projectId: string; selected: string[] | null; onChange: (ids: string[] | null) => void }) {
  const { data } = useAsync(() => (projectId ? api<Character[]>(`/api/projects/${projectId}/characters`) : Promise.resolve([])), [projectId]);
  if (!projectId || !data?.length) return null;
  const isOn = (id: string) => selected === null || selected.includes(id);
  const toggle = (id: string) => {
    const cur = selected ?? data.map((c) => c.id);
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    onChange(next.length === data.length ? null : next);
  };
  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium">Characters to include</legend>
      <div className="flex flex-wrap gap-2">
        {data.map((c) => (
          <button key={c.id} type="button" aria-pressed={isOn(c.id)} onClick={() => toggle(c.id)}
            className={`rounded-full border px-3 py-1 text-sm ${isOn(c.id) ? "border-accent bg-accent/15" : "border-border text-muted"}`}>{c.name}</button>))}
      </div>
    </fieldset>
  );
}
