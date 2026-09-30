import { BarChart3, Clapperboard, History as HistoryIcon, FolderKanban, LayoutDashboard, Menu as MenuIcon, Settings, Shield, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { NotificationBell } from "./NotificationBell";
import { UserMenu } from "./UserMenu";

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/create", label: "Create", icon: Sparkles },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/history", label: "History", icon: HistoryIcon },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Logo() {
  return (
    <Link to="/dashboard" className="flex items-center gap-2 rounded-md font-display text-lg font-bold tracking-tight">
      <Clapperboard className="h-5 w-5 text-accent" aria-hidden /> DreamCast<span className="text-accent">AI</span>
    </Link>
  );
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const items = user?.role === "ADMIN" ? [...nav, { to: "/admin", label: "Admin", icon: Shield }] : nav;
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {items.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} onClick={onNavigate}
          className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${isActive ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"}`}>
          <Icon className="h-4 w-4" aria-hidden /> {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col gap-6 border-r border-border bg-surface p-4 lg:flex">
        <Logo />
        <Link to="/projects?new=1" className="btn-primary w-full"><Sparkles className="h-4 w-4" aria-hidden /> New project</Link>
        <SidebarNav />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} aria-hidden />
          <aside role="dialog" aria-modal="true" aria-label="Navigation" className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col gap-6 bg-surface p-4 shadow-2xl">
            <div className="flex items-center justify-between"><Logo />
              <button className="btn-ghost !p-1.5" onClick={() => setOpen(false)} aria-label="Close navigation"><X className="h-5 w-5" /></button></div>
            <Link to="/projects?new=1" className="btn-primary w-full"><Sparkles className="h-4 w-4" aria-hidden /> New project</Link>
            <SidebarNav onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border bg-bg px-4">
          <button className="btn-ghost !p-1.5 lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation" aria-expanded={open}><MenuIcon className="h-5 w-5" /></button>
          <div className="lg:hidden"><Logo /></div>
          <div className="ml-auto flex items-center gap-1"><NotificationBell /><UserMenu /></div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
          <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8"><Outlet /></div>
        </main>
      </div>
    </div>
  );
}
