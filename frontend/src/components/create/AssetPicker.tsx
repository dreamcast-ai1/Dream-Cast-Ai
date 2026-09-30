import { useId } from "react";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../lib/api";
import { ASSET_LABEL } from "../../lib/assetText";
import type { Asset, FieldDef } from "../../lib/types";

/** Pick an existing project asset (story / lyrics / script) as context. Only lists what is really in the project. */
export function AssetPicker({ field, projectId, value, onChange }: { field: FieldDef; projectId: string; value: string; onChange: (id: string) => void }) {
  const id = useId();
  const types = field.asset_types.join(",");
  const { data, loading } = useAsync(() => (projectId ? api<Asset[]>(`/api/projects/${projectId}/assets?type=${types}`) : Promise.resolve([])), [projectId, types]);
  const noun = field.asset_types.map((t) => ASSET_LABEL[t]?.toLowerCase()).join("/");
  return (
    <div className="sm:col-span-2">
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium">{field.label}</label>
      <select id={id} className="field" value={value} disabled={!projectId || loading} onChange={(e) => onChange(e.target.value)}>
        <option value="">{!projectId ? "Select a project first" : data?.length ? `No ${noun} (start from scratch)` : `No ${noun} in this project yet`}</option>
        {data?.map((a) => <option key={a.id} value={a.id}>{a.title} · v{a.version}</option>)}
      </select>
      {value && <p className="mt-1 text-xs text-accent">Selected. It is supplied to the generator automatically — no copy/paste needed.</p>}
      {field.help && !value && <p className="mt-1 text-xs text-muted">{field.help}</p>}
    </div>
  );
}
