import { useEffect, useRef, useState, type ReactNode } from "react";

export interface MenuItem { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean }

/** Accessible dropdown: button + menu, closes on outside click / Escape, arrow-key navigation. */
export function Menu({ trigger, label, items, align = "right", triggerClass = "btn-ghost !p-1.5" }: { trigger: ReactNode; label: string; items: MenuItem[]; align?: "left" | "right"; triggerClass?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); box.current?.querySelector<HTMLElement>("button")?.focus(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const els = [...(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
        const i = els.indexOf(document.activeElement as HTMLElement);
        els[(i + (e.key === "ArrowDown" ? 1 : -1) + els.length) % els.length]?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <div ref={box} className="relative">
      <button className={triggerClass} aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{trigger}</button>
      {open && (
        <div role="menu" aria-label={label} className={`card absolute z-40 mt-1 min-w-44 p-1 shadow-xl ${align === "right" ? "right-0" : "left-0"}`}>
          {items.map((it) => (
            <button key={it.label} role="menuitem" className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-raised ${it.danger ? "text-danger" : ""}`}
              onClick={() => { setOpen(false); it.onSelect(); }}>{it.icon}{it.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}
