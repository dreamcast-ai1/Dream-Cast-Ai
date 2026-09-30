import { Component, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error("UI error:", error); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="flex min-h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <h1 className="text-2xl font-semibold">Something went wrong. Please try again.</h1>
        <button className="btn-primary" onClick={() => location.reload()}>Reload</button>
      </div>
    );
  }
}
