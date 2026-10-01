import { Check, Clapperboard } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, ErrorState, PageHeader, PageLoader, Spinner } from "../components/ui/feedback";
import { useAuth } from "../context/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { api, errorMessage } from "../lib/api";
import { formatDate, formatPrice } from "../lib/format";
import type { CheckoutOrder, CurrentSubscription, PlanInfo, UsageItem } from "../lib/types";

// What each plan allows, in film terms. The numbers come from the server; nothing is hard-coded here.
const ROWS: [string, string][] = [["video", "🎥 Video generations"], ["image", "🖼️ Image generations"], ["music", "🎵 Music generations"], ["face_replacement", "🎭 Face generations"],
  ["story", "📖 Story generations"], ["script", "📝 Script generations"], ["voice", "🎤 Voice generations"]];

interface RazorpayResponse { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }
interface RazorpayCheckout { open: () => void; on: (event: string, cb: (r: unknown) => void) => void }
declare global { interface Window { Razorpay?: new (options: Record<string, unknown>) => RazorpayCheckout } }

/** Razorpay's official Checkout script, loaded only when someone actually upgrades. Only the PUBLIC key id ever reaches the browser. */
function loadCheckoutScript(): Promise<boolean> {
  if (window.Razorpay) return Promise.resolve(true);
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve(true);
    s.onerror = () => resolve(false);
    document.body.appendChild(s);
  });
}

function PlanCard({ plan, current, rank, currentRank, payments, busy, onUpgrade }: {
  plan: PlanInfo; current: boolean; rank: number; currentRank: number; payments: boolean; busy: string | null; onUpgrade: (p: PlanInfo) => void;
}) {
  const per = plan.usage_period === "month" ? "month" : "day";
  const price = formatPrice(plan.price_minor, plan.currency, plan.billing_period).split(" / ");
  const check = <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />;
  return (
    <li className={`card flex flex-col p-5 ${current ? "border-accent" : ""}`} aria-label={`${plan.name} plan`}>
      <div className="flex items-start justify-between gap-2">
        <div><h2 className="text-xl font-semibold uppercase tracking-wide">{plan.name}</h2><p className="text-sm text-muted">{plan.tagline}</p></div>
        {current && <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-fg">Current plan</span>}
      </div>
      <p className="mt-4 font-display text-3xl font-bold">{price[0]}{price[1] && <span className="ml-1 font-sans text-sm font-normal text-muted">/ {price[1]}</span>}</p>
      <p className="mt-1 text-xs text-muted">{plan.description}</p>
      <h3 className="mb-2 mt-5 font-sans text-xs font-semibold uppercase tracking-wider text-muted">Every {per}</h3>
      <ul className="space-y-1.5 text-sm">{ROWS.map(([g, label]) => <li key={g} className="flex justify-between gap-2"><span>{label}</span><span className="font-medium">{plan.limits[g]}</span></li>)}</ul>
      <h3 className="mb-2 mt-5 font-sans text-xs font-semibold uppercase tracking-wider text-muted">Includes</h3>
      <ul className="space-y-1.5 text-sm">
        <li className="flex gap-2">{check}Scene clips up to {plan.features.max_video_seconds} seconds</li>
        <li className="flex gap-2">{check}Assemble scenes into one movie</li>
        {plan.features.image_to_video && <li className="flex gap-2">{check}Start a scene from an image</li>}
        {plan.features.face_replacement && <li className="flex gap-2">{check}Face replacement</li>}
      </ul>
      <div className="mt-auto pt-6">
        {current ? <button className="btn-secondary w-full" disabled>Your current plan</button>
          : rank > currentRank ? <button className="btn-primary w-full" disabled={!payments || busy !== null} onClick={() => onUpgrade(plan)}>
            {busy === plan.id ? <Spinner className="!h-4 !w-4" /> : null}{payments ? "Upgrade" : "Upgrades unavailable"}</button>
          : <button className="btn-secondary w-full" disabled>Included in your plan</button>}
      </div>
    </li>
  );
}

export default function Plans() {
  const { user } = useAuth();
  const plans = useAsync(() => api<{ plans: PlanInfo[]; payments_enabled: boolean }>("/api/subscription/plans"));
  const sub = useAsync(() => api<CurrentSubscription>("/api/subscription/current"));
  const use = useAsync(() => api<{ period: string; items: UsageItem[] }>("/api/subscription/usage"));
  const [notice, setNotice] = useState<{ kind: "info" | "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const cancel = (orderId: string, reason: "cancelled" | "failed") => api("/api/subscription/checkout/cancel", { method: "POST", json: { order_id: orderId, reason } }).catch(() => undefined);

  const upgrade = async (plan: PlanInfo) => {
    setNotice(null); setBusy(plan.id);
    let order: CheckoutOrder;
    try { order = await api<CheckoutOrder>("/api/subscription/checkout", { method: "POST", json: { plan_id: plan.id } }); }
    catch (e) { setNotice({ kind: "error", text: errorMessage(e) }); setBusy(null); return; }
    if (!(await loadCheckoutScript()) || !window.Razorpay) {
      await cancel(order.order_id, "cancelled");
      setNotice({ kind: "error", text: "The payment window couldn't load. Check your connection and try again." }); setBusy(null); return;
    }
    let settled = false;
    const finish = (n: { kind: "info" | "success" | "error"; text: string }) => { settled = true; setNotice(n); setBusy(null); };
    const checkout = new window.Razorpay({
      key: order.key_id, amount: order.amount, currency: order.currency, order_id: order.order_id, name: "DreamCast AI",
      description: `${order.plan.name} plan`, prefill: { name: order.user_name || user?.name, email: order.user_email }, theme: { color: "#e11d48" },
      // The server decides whether this payment is genuine (signature check); the plan only changes after that.
      handler: async (r: RazorpayResponse) => {
        try {
          await api("/api/subscription/verify", { method: "POST", json: r });
          await Promise.all([sub.reload(), plans.reload(), use.reload()]);
          finish({ kind: "success", text: `You're on the ${order.plan.name} plan. Your new allowance is active now.` });
        } catch (e) { finish({ kind: "error", text: `${errorMessage(e)} If money was taken, it will be matched automatically. Contact support if your plan doesn't update.` }); }
      },
      modal: { ondismiss: () => { if (!settled) { void cancel(order.order_id, "cancelled"); finish({ kind: "info", text: "Checkout cancelled. You haven't been charged." }); } } },
    });
    checkout.on("payment.failed", () => { void cancel(order.order_id, "failed"); setNotice({ kind: "error", text: "The payment didn't go through. You haven't been charged; you can try again." }); });
    checkout.open();
  };

  if (plans.loading || sub.loading) return <PageLoader />;
  if (plans.error || sub.error) return <ErrorState message={(plans.error || sub.error)!} onRetry={() => { void plans.reload(); void sub.reload(); }} />;
  const list = plans.data!.plans, cur = sub.data!;
  const rank = (id: string) => list.findIndex((p) => p.id === id);
  return (
    <div>
      <PageHeader title="Plans" subtitle="Pick the production level that fits your story. Every plan lets you assemble scenes into a movie." />
      <section className="card mb-4 flex flex-wrap items-center gap-3 p-4" aria-label="Current plan">
        <Clapperboard className="h-5 w-5 text-accent" aria-hidden />
        <div className="text-sm"><p className="text-xs font-semibold uppercase tracking-widest text-muted">Current plan</p>
          <p className="font-semibold">{cur.plan.name} · {formatPrice(cur.plan.price_minor, cur.plan.currency, cur.plan.billing_period)}</p></div>
        {cur.subscription.expires_at && <p className="ml-auto text-sm text-muted">{cur.subscription.status === "ACTIVE" ? "Renews or ends" : "Ended"} {formatDate(cur.subscription.expires_at)}</p>}
      </section>
      {use.data && (
        <section className="card mb-4 p-4" aria-label="Usage">
          <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold">Used {use.data.period === "month" ? "this month" : "today"}</h2><Link to="/usage" className="text-sm text-accent hover:underline">All usage</Link></div>
          <ul className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">{ROWS.map(([g, label]) => { const u = use.data!.items.find((i) => i.generator === g); return u ? <li key={g} className="flex justify-between gap-2"><span className="truncate">{label}</span><span className="font-medium">{u.used}/{u.limit}</span></li> : null; })}</ul>
        </section>)}
      {!plans.data!.payments_enabled && <div className="mb-4"><Alert kind="info">Upgrades are temporarily unavailable. Nothing will be charged, and everything in your current plan still works.</Alert></div>}
      {notice && <div className="mb-4" role="status"><Alert kind={notice.kind}>{notice.text}</Alert></div>}
      <ul className="grid gap-4 md:grid-cols-3">
        {list.map((p) => <PlanCard key={p.id} plan={p} current={p.id === cur.subscription.effective_plan_id} rank={rank(p.id)} currentRank={rank(cur.subscription.effective_plan_id)}
          payments={plans.data!.payments_enabled} busy={busy} onUpgrade={upgrade} />)}
      </ul>
    </div>
  );
}
