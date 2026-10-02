import { useEffect, useRef } from "react";

/** Calls `fn` every `ms` while `active` and the tab is visible. Kept slow on purpose: jobs take minutes, not milliseconds.
 *  When the tab becomes visible again it checks immediately, so a generation that finished in the background shows up at once. */
export function usePolling(fn: () => void | Promise<void>, active: boolean, ms = 5000) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const tick = () => document.visibilityState === "visible" && void ref.current();
    const t = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", tick); };
  }, [active, ms]);
}
