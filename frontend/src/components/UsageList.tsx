import type { UsageItem } from "../lib/types";

export function UsageList({ items, compact }: { items: UsageItem[]; compact?: boolean }) {
  return (
    <ul className={`grid gap-3 ${compact ? "" : "sm:grid-cols-2"}`}>
      {items.map((u) => {
        const pct = u.limit > 0 ? Math.min(100, (u.used / u.limit) * 100) : 100;
        const full = u.used >= u.limit;
        return (
          <li key={u.generator} className={compact ? "" : "card p-4"}>
            <div className="mb-1.5 flex items-center justify-between text-sm">
              <span className="font-medium"><span aria-hidden>{u.emoji}</span> {u.label}</span>
              <span className={full ? "text-danger" : "text-muted"}>{u.used} / {u.limit}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-raised" role="progressbar" aria-label={`${u.label} usage today`} aria-valuemin={0} aria-valuemax={u.limit} aria-valuenow={u.used}>
              <div className={`h-full rounded-full ${full ? "bg-danger" : "bg-accent"}`} style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
