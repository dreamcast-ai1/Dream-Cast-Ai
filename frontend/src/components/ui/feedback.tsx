import { AlertCircle, CheckCircle2, Info, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function Spinner({ className = "" }: { className?: string }) {
  return <Loader2 aria-hidden className={`h-4 w-4 animate-spin ${className}`} />;
}

export function PageLoader({ label = "Loading…" }: { label?: string }) {
  return <div role="status" className="flex items-center justify-center gap-2 py-24 text-muted"><Spinner /> {label}</div>;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />;
}

const alertStyles = {
  error: { cls: "border-danger/40 bg-danger/10 text-danger", Icon: AlertCircle },
  success: { cls: "border-success/40 bg-success/10 text-success", Icon: CheckCircle2 },
  info: { cls: "border-border bg-raised text-muted", Icon: Info },
} as const;

export function Alert({ kind = "info", children, action }: { kind?: keyof typeof alertStyles; children: ReactNode; action?: ReactNode }) {
  const { cls, Icon } = alertStyles[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`fade-in flex items-start gap-3 rounded-lg border px-4 py-3 text-sm ${cls}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 break-words">{children}</div>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert kind="error" action={onRetry && <button className="btn-secondary !py-1" onClick={onRetry}>Retry</button>}>{message}</Alert>
  );
}

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="rise flex flex-col items-center rounded-xl border border-dashed border-border px-6 py-12 text-center">
      {icon && <div className="mb-3 text-3xl" aria-hidden>{icon}</div>}
      <p className="font-medium">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-sm text-muted">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const tones: Record<string, string> = {
  COMPLETED: "bg-success", READY: "bg-success", ACTIVE: "bg-success", SUCCEEDED: "bg-success",
  PROCESSING: "bg-warn", IN_PROGRESS: "bg-warn", QUEUED: "bg-muted",
  FAILED: "bg-danger", DISABLED: "bg-danger", CANCELLED: "bg-muted", DRAFT: "bg-muted", ARCHIVED: "bg-muted",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-raised px-2 py-0.5 text-xs font-medium">
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tones[status] ?? "bg-muted"}`} />
      {status.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}
    </span>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
