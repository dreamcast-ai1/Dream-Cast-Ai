import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeChoice = "dark" | "light" | "system";
const Ctx = createContext<{ theme: ThemeChoice; setTheme: (t: ThemeChoice) => void } | null>(null);

function apply(t: ThemeChoice) {
  const resolved = t === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : t;
  document.documentElement.setAttribute("data-theme", resolved);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(() => {
    try { return (localStorage.getItem("dc-theme") as ThemeChoice) || "dark"; } catch { return "dark"; }
  });
  useEffect(() => {
    apply(theme);
    if (theme !== "system") return;
    const mq = matchMedia("(prefers-color-scheme: light)");
    const on = () => apply("system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [theme]);
  const setTheme = (t: ThemeChoice) => { setThemeState(t); try { localStorage.setItem("dc-theme", t); } catch { /* ignore */ } };
  return <Ctx.Provider value={{ theme, setTheme }}>{children}</Ctx.Provider>;
}

export function useTheme() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
