import type { ReactNode } from "react";

export interface TabDef { id: string; label: string; badge?: number; icon?: ReactNode }

/** Horizontally scrollable tab list (works at 320px wide). */
export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string }) {
  const onKey = (e: React.KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.id === active);
    if (e.key === "ArrowRight") onChange(tabs[(i + 1) % tabs.length].id);
    if (e.key === "ArrowLeft") onChange(tabs[(i - 1 + tabs.length) % tabs.length].id);
  };
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKey} className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 sm:mx-0 sm:px-0">
      {tabs.map((t) => (
        <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={active === t.id} aria-controls={`panel-${t.id}`} tabIndex={active === t.id ? 0 : -1}
          onClick={() => onChange(t.id)}
          className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${active === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}>
          {t.icon}{t.label}
          {!!t.badge && <span className="rounded-full bg-raised px-1.5 text-xs text-muted">{t.badge}</span>}
        </button>
      ))}
    </div>
  );
}

export const TabPanel = ({ id, children }: { id: string; children: ReactNode }) => (
  <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} className="pt-6">{children}</div>
);
