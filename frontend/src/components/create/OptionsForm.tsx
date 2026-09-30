import { useId } from "react";
import type { FieldDef, GeneratorSchema } from "../../lib/types";
import { AssetPicker } from "./AssetPicker";

type Options = Record<string, unknown>;

function FieldControl({ f, options, set, projectId }: { f: FieldDef; options: Options; set: (k: string, v: unknown) => void; projectId: string }) {
  const id = useId();
  const value = options[f.key];
  if (f.key === "method") {
    return (
      <div className="sm:col-span-2">
        <span id={`${id}-l`} className="mb-1.5 block text-sm font-medium">{f.label}</span>
        <div role="radiogroup" aria-labelledby={`${id}-l`} className="inline-flex rounded-lg border border-border bg-bg p-1">
          {f.choices.map((c) => (
            <button key={c} type="button" role="radio" aria-checked={(value ?? f.default) === c} onClick={() => set(f.key, c)}
              className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${(value ?? f.default) === c ? "bg-accent text-accent-fg" : "text-muted hover:text-fg"}`}>{c}</button>))}
        </div>
      </div>
    );
  }
  if (f.kind === "asset") return <AssetPicker field={f} projectId={projectId} value={String(value ?? "")} onChange={(v) => set(f.key, v || undefined)} />;
  const str = value === undefined || value === null ? "" : String(value);
  const label = <label htmlFor={id} className="mb-1.5 block text-sm font-medium">{f.label}</label>;
  if (f.kind === "textarea") return <div className="sm:col-span-2">{label}<textarea id={id} className="field min-h-[4.5rem]" maxLength={1000} value={str} onChange={(e) => set(f.key, e.target.value)} />{f.help && <p className="mt-1 text-xs text-muted">{f.help}</p>}</div>;
  if (f.kind === "text") return <div>{label}<input id={id} className="field" maxLength={120} value={str} onChange={(e) => set(f.key, e.target.value)} />{f.help && <p className="mt-1 text-xs text-muted">{f.help}</p>}</div>;
  const isDuration = f.kind === "duration";
  return (
    <div>
      {label}
      <select id={id} className="field" value={str} onChange={(e) => set(f.key, isDuration && e.target.value ? Number(e.target.value) : e.target.value)}>
        {f.default == null && <option value="">Not specified</option>}
        {f.choices.map((c) => <option key={c} value={c}>{isDuration ? `${c} seconds` : c}</option>)}
      </select>
      {f.allow_custom && value === "Custom" && (
        <input aria-label={`Custom ${f.label.toLowerCase()}`} className="field mt-2" maxLength={80} placeholder={`Describe your ${f.label.toLowerCase()}`}
          value={String(options[`${f.key}_custom`] ?? "")} onChange={(e) => set(`${f.key}_custom`, e.target.value)} />
      )}
      {f.help && <p className="mt-1 text-xs text-muted">{f.help}</p>}
    </div>
  );
}

/** Renders only the fields the selected generator defines (from the backend schema). Every field is optional. */
export function OptionsForm({ schema, options, onChange, projectId }: { schema: GeneratorSchema; options: Options; onChange: (o: Options) => void; projectId: string }) {
  if (!schema.fields.length) return null;
  const set = (k: string, v: unknown) => onChange({ ...options, [k]: v });
  return (
    <fieldset className="grid gap-4 sm:grid-cols-2">
      <legend className="sr-only">{schema.label} options</legend>
      {schema.fields.map((f) => <FieldControl key={f.key} f={f} options={options} set={set} projectId={projectId} />)}
    </fieldset>
  );
}
