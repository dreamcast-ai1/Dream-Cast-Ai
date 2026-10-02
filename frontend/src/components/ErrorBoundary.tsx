import { Component, type ReactNode } from "react";
import { ContactSupportButton, CrashSupport } from "../support/CrashSupport";

/** First line of defence: obvious secrets never even reach sessionStorage (the server scrubs again before anything is stored in a ticket). */
const redact = (t: string) => t.replace(/bearer\s+\S+/gi, "[redacted]").replace(/\b(password|passwd|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+/gi, "[redacted]");

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) {
    console.error("UI error:", error);
    try { sessionStorage.setItem("dc-last-ui-error", redact(String((error as Error)?.message ?? error)).slice(0, 300)); } catch { /* storage unavailable */ }   // lets Support mention it after a reload
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="flex min-h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <h1 className="text-2xl font-semibold">Something went wrong. Please try again.</h1>
        <div className="flex flex-wrap justify-center gap-2">
          <button className="btn-primary" onClick={() => location.reload()}>Reload</button>
          <ContactSupportButton />
        </div>
        <CrashSupport />
      </div>
    );
  }
}
