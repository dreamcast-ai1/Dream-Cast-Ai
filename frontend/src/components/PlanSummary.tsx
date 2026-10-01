import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { api } from "../lib/api";
import { formatDate, formatPrice, titleCase } from "../lib/format";
import type { CurrentSubscription, UsageItem } from "../lib/types";
import { UsageList } from "./UsageList";
import { ErrorState, PageLoader, StatusBadge } from "./ui/feedback";

/** Current plan, subscription status, renewal/expiry and what's left today. Used in Settings -> Plan. */
export function PlanSummary() {
  const sub = useAsync(() => api<CurrentSubscription>("/api/subscription/current"));
  const use = useAsync(() => api<{ period: string; resets_at: string; items: UsageItem[] }>("/api/subscription/usage"));
  if (sub.loading || use.loading) return <PageLoader />;
  if (sub.error || use.error) return <ErrorState message={(sub.error || use.error)!} onRetry={() => { void sub.reload(); void use.reload(); }} />;
  const { plan, subscription: s } = sub.data!;
  const downgraded = s.effective_plan_id !== s.plan_id;
  const period = use.data!.period === "month" ? "month" : "day";
  return (
    <div className="space-y-5">
      <section className="card p-5" aria-label="Current plan">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><p className="text-xs font-semibold uppercase tracking-widest text-accent">Your plan</p>
            <h2 className="mt-1 text-2xl font-semibold">{plan.name}</h2><p className="text-sm text-muted">{plan.tagline} · {formatPrice(plan.price_minor, plan.currency, plan.billing_period)}</p></div>
          <Link to="/plans" className="btn-secondary">Compare plans</Link>
        </div>
        <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
          <div><dt className="text-muted">Status</dt><dd className="mt-1"><StatusBadge status={downgraded ? "EXPIRED" : s.status} /></dd></div>
          <div><dt className="text-muted">{plan.price_minor === 0 ? "Renewal" : "Renews / expires"}</dt><dd className="mt-0.5 font-medium">{s.expires_at ? formatDate(s.expires_at) : plan.price_minor === 0 ? "Free — no expiry" : "No end date"}</dd></div>
          <div><dt className="text-muted">Allowance resets</dt><dd className="mt-0.5 font-medium">{period === "day" ? "Daily, midnight UTC" : `Monthly, ${formatDate(use.data!.resets_at)}`}</dd></div>
        </dl>
        {downgraded && <p className="mt-3 text-sm text-muted">Your {titleCase(s.plan_id)} subscription is {s.status === "ACTIVE" ? "past its end date" : s.status.toLowerCase()}, so the free Trailer allowance applies.</p>}
        {s.payment_provider === "admin" && <p className="mt-3 text-xs text-muted">This plan was granted by an administrator (no payment).</p>}
      </section>
      <section aria-label="Usage"><h2 className="mb-3 text-lg font-semibold">Usage and remaining generations</h2><UsageList items={use.data!.items} /></section>
    </div>
  );
}
