import { Bell } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { timeAgo } from "../../lib/format";
import type { Notification } from "../../lib/types";

export function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const nav = useNavigate();

  const load = useCallback(async () => {
    try {
      const d = await api<{ unread: number; items: Notification[] }>("/api/notifications");
      setItems(d.items); setUnread(d.unread); setFailed(false);
    } catch { setFailed(true); }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && void load(), 15000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", down); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [open]);

  const openItem = async (n: Notification) => {
    if (!n.is_read) { await api(`/api/notifications/${n.id}/read`, { method: "POST" }).catch(() => undefined); void load(); }
    setOpen(false);
    if (n.asset_id && n.project_id) nav(`/projects/${n.project_id}/assets/${n.asset_id}`);
    else if (n.job_id) nav(`/history/${n.job_id}`);
    else if (n.project_id) nav(`/projects/${n.project_id}`);
  };

  return (
    <div ref={box} className="relative">
      <button className="btn-ghost relative !p-1.5" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} aria-expanded={open} aria-haspopup="true"
        onClick={() => { setOpen((o) => !o); void load(); }}>
        <Bell className="h-5 w-5" />
        {unread > 0 && <span aria-hidden className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-accent-fg">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="card absolute right-0 z-40 mt-1 w-[min(22rem,calc(100vw-2rem))] shadow-xl">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <h2 className="font-sans text-sm font-semibold">Notifications</h2>
            {unread > 0 && <button className="text-xs text-accent hover:underline" onClick={async () => { await api("/api/notifications/read-all", { method: "POST" }); void load(); }}>Mark all read</button>}
          </div>
          <ul className="max-h-80 overflow-y-auto">
            {failed && <li className="px-4 py-6 text-center text-sm text-danger">Could not load notifications.</li>}
            {!failed && items.length === 0 && <li className="px-4 py-8 text-center text-sm text-muted">You're all caught up. Finished generations will show up here.</li>}
            {items.map((n) => (
              <li key={n.id}>
                <button onClick={() => openItem(n)} className="flex w-full gap-3 px-4 py-3 text-left hover:bg-raised">
                  <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${n.is_read ? "bg-transparent" : "bg-accent"}`} />
                  <span className="min-w-0"><span className="block text-sm font-medium">{n.title}</span>
                    {n.message && <span className="block truncate text-xs text-muted">{n.message}</span>}
                    <span className="text-xs text-muted">{timeAgo(n.created_at)}</span></span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
