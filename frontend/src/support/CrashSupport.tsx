import { Component, useEffect, useState, type ReactNode } from "react";
import { api, tokenStore } from "../lib/api";
import { OPEN_SUPPORT_EVENT, SupportPanel } from "./SupportWidget";

/** If Support itself fails while the app is already down, show nothing rather than a second error. */
class Quiet extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? null : this.props.children; }
}

/** Support on the crash screen. ErrorBoundary replaces the whole app (including AppShell and its widget), so this mounts a separate copy here:
 *  the same panel and the same /api/support calls, never both at once. It appears only for a signed-in user while the Support switch is on. */
export function useCrashSupportAvailable(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!tokenStore.get()) return;
    api<{ support?: boolean }>("/api/features").then((f) => setOn(f.support !== false)).catch(() => setOn(false));
  }, []);
  return on;
}

export function CrashSupport() {
  const on = useCrashSupportAvailable();
  if (!on) return null;
  return <Quiet><SupportPanel pathname={window.location.pathname} crashed /></Quiet>;
}

export function ContactSupportButton() {
  const on = useCrashSupportAvailable();
  if (!on) return null;
  return <button className="btn-secondary" onClick={() => window.dispatchEvent(new Event(OPEN_SUPPORT_EVENT))}>Contact Support</button>;
}
