import { useEffect, useRef } from "react";

/** Calls `fn` every `ms` while `active` and the tab is visible. Kept slow on purpose: jobs take minutes, not milliseconds. */
export function usePolling(fn: () => void | Promise<void>, active: boolean, ms = 5000) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => document.visibilityState === "visible" && void ref.current(), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}
