import { ArrowLeft, LifeBuoy, Send, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation } from "react-router-dom";
import { Spinner } from "../components/ui/feedback";
import { api, errorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import { clearUiError, lastUiError, pageContext, STATUS_LABEL, type SupportOptions, type SupportReply, type SupportTicket } from "./types";

interface Msg { from: "user" | "bot"; text: string; steps?: string[]; followUp?: string | null; buttons?: SupportReply["buttons"]; category?: string }
type View = "chat" | "confirm" | "sent" | "tickets" | "ticket";

const STATUS_STYLE: Record<SupportTicket["status"], string> = { open: "bg-warn/15 text-warn", in_progress: "bg-accent/15 text-accent", resolved: "bg-success/15 text-success", closed: "bg-raised text-muted" };

function TicketBadge({ status }: { status: SupportTicket["status"] }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status]}`}>{STATUS_LABEL[status]}</span>;
}

/** The free in-app Support assistant. It talks only to /api/support/*: a local rule-based troubleshooter and the ticket system. No AI is involved,
 *  and nothing secret is ever sent: only the page name, the user's own words, a visible error message and a few safe browser details. */
export function SupportWidget() {
  return <SupportPanel pathname={useLocation().pathname} />;
}

/** Opens the panel from anywhere (for example the crash screen's "Contact Support" button). */
export const OPEN_SUPPORT_EVENT = "dc-support-open";

/** The panel itself. It needs no router or app context (only a path string), so it also works on the crash screen outside the app tree. */
export function SupportPanel({ pathname, crashed = false }: { pathname: string; crashed?: boolean }) {
  const ctx = pageContext(pathname);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>("chat");
  const [opts, setOpts] = useState<SupportOptions | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [category, setCategory] = useState("GENERAL");
  const [issue, setIssue] = useState("");
  const [sent, setSent] = useState<SupportTicket | null>(null);
  const [tickets, setTickets] = useState<SupportTicket[] | null>(null);
  const [current, setCurrent] = useState<SupportTicket | null>(null);
  const [more, setMore] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const uiError = open ? lastUiError() : "";
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_SUPPORT_EVENT, show);
    return () => window.removeEventListener(OPEN_SUPPORT_EVENT, show);
  }, []);

  useEffect(() => {
    if (!open || opts) return;
    api<SupportOptions>("/api/support/options").then((o) => { setOpts(o); 
      const menu = o.quick_actions.map((q) => ({ id: `quick:${q.category}`, label: q.label }));
      if (crashed) { setIssue("The page stopped working with an error."); setMsgs([{ from: "bot", text: `${o.greeting}\nIt looks like part of the app hit an error. You can send it to the admin team now; the error message is attached.`, buttons: [{ id: "escalate", label: "Send to Admin" }, ...menu] }]); }
      else setMsgs([{ from: "bot", text: o.greeting, buttons: menu }]); })
      .catch((e) => setErr(errorMessage(e)));
  }, [open, opts, crashed]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [msgs, view, busy]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const push = (m: Msg) => setMsgs((x) => [...x, m]);
  const label = (id: string) => opts?.categories.find((c) => c.id === id)?.label ?? id;

  const ask = async (body: { message?: string; action?: string; category?: string }, userText?: string) => {
    setErr(""); setBusy(true);
    if (userText) { push({ from: "user", text: userText }); setIssue(userText); }
    try {
      const r = await api<SupportReply>("/api/support/chat", { method: "POST", json: { ...body, page: ctx.page.toLowerCase() } });
      if (r.category && r.matched) setCategory(r.category);
      push({ from: "bot", text: r.reply, steps: r.steps, followUp: r.follow_up, buttons: r.buttons, category: r.category });
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  const onButton = (b: { id: string; label: string }, msgCategory?: string) => {
    if (b.id === "escalate") { setView("confirm"); return; }
    push({ from: "user", text: b.label });
    if (b.id.startsWith("quick:")) { const c = b.id.slice(6); setCategory(c); void ask({ action: "quick", category: c }); }
    else void ask({ action: b.id, category: msgCategory ?? category });
  };

  const submitText = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    void ask({ message: t }, t);
  };

  const send = async () => {
    setErr(""); setBusy(true);
    try {
      const t = await api<SupportTicket>("/api/support/tickets", { method: "POST", json: {
        category, subject: issue.slice(0, 80) || `${label(category)} problem`, description: issue || `Reported from the ${ctx.page} page.`, page: ctx.page, feature: label(category),
        error_message: uiError || undefined,
        context: { path: pathname.replace(/\/[A-Za-z0-9]{20,}/g, "/…"), timestamp: new Date().toISOString(), browser: navigator.userAgent.slice(0, 160), project_id: ctx.project_id } } });
      clearUiError(); setSent(t); setTickets(null); setView("sent");
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  const loadTickets = async () => {
    setView("tickets"); setErr("");
    try { setTickets((await api<{ tickets: SupportTicket[] }>("/api/support/tickets")).tickets); } catch (e) { setErr(errorMessage(e)); }
  };
  const openTicket = async (id: string) => {
    setErr("");
    try { setCurrent(await api<SupportTicket>(`/api/support/tickets/${id}`)); setView("ticket"); } catch (e) { setErr(errorMessage(e)); }
  };
  const addInfo = async (e: FormEvent) => {
    e.preventDefault();
    if (!current || !more.trim()) return;
    setBusy(true); setErr("");
    try { setCurrent(await api<SupportTicket>(`/api/support/tickets/${current.id}/messages`, { method: "POST", json: { message: more.trim() } })); setMore(""); }
    catch (e2) { setErr(errorMessage(e2)); } finally { setBusy(false); }
  };

  const back = view === "chat" ? null : () => setView(view === "ticket" ? "tickets" : "chat");

  return (
    <>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-label={open ? "Close support" : "Open support"} aria-expanded={open}
        className="btn-primary fixed bottom-4 right-4 z-40 !rounded-full !p-3 shadow-xl sm:!px-4 sm:!py-2.5">
        {open ? <X className="h-5 w-5" aria-hidden /> : <LifeBuoy className="h-5 w-5" aria-hidden />}<span className="hidden sm:inline">{open ? "Close" : "Support"}</span>
      </button>
      {open && (
        <section role="dialog" aria-label="DreamCast Support" className="card fixed bottom-20 right-3 z-40 flex text-left h-[min(36rem,calc(100dvh-6.5rem))] w-[calc(100vw-1.5rem)] flex-col overflow-hidden shadow-2xl sm:right-4 sm:w-96">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2.5">
            {back && <button className="btn-ghost !p-1.5" onClick={back} aria-label="Back"><ArrowLeft className="h-4 w-4" aria-hidden /></button>}
            <div className="min-w-0 flex-1"><p className="text-sm font-semibold">DreamCast Support</p><p className="truncate text-xs text-muted">Automated troubleshooting · no AI credits used</p></div>
            {view === "chat" && <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => void loadTickets()}>My tickets</button>}
          </header>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 text-sm" aria-live="polite">
            {view === "chat" && msgs.map((m, i) => (
              <div key={i} className={m.from === "user" ? "flex justify-end" : ""}>
                <div className={`max-w-[92%] rounded-2xl px-3 py-2 ${m.from === "user" ? "bg-accent text-accent-fg" : "bg-raised"}`}>
                  <p className="whitespace-pre-line">{m.text}</p>
                  {!!m.steps?.length && <ol className="mt-2 list-decimal space-y-1 pl-5">{m.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
                  {m.followUp && <p className="mt-2 font-medium">{m.followUp}</p>}
                  {i === msgs.length - 1 && !!m.buttons?.length && (
                    <div className="mt-2 flex flex-wrap gap-1.5">{m.buttons.map((b) => <button key={b.id} className="btn-secondary !px-2.5 !py-1 !text-xs" disabled={busy} onClick={() => onButton(b, m.category)}>{b.label}</button>)}</div>)}
                </div>
              </div>))}
            {view === "chat" && busy && <p className="flex items-center gap-2 text-xs text-muted"><Spinner className="!h-3 !w-3" /> One moment…</p>}

            {view === "confirm" && (
              <div className="space-y-3">
                <h2 className="font-semibold">Send this issue to the DreamCast admin team?</h2>
                <dl className="space-y-1.5 rounded-lg border border-border p-3 text-xs">
                  <div><dt className="text-muted">Category</dt><dd className="font-medium">{label(category)}</dd></div>
                  <div><dt className="text-muted">Issue</dt><dd className="break-words font-medium">{issue || "(no description yet)"}</dd></div>
                  <div><dt className="text-muted">Current page</dt><dd className="font-medium">{ctx.page}</dd></div>
                  {uiError && <div><dt className="text-muted">Error message</dt><dd className="break-words font-medium">{uiError}</dd></div>}
                </dl>
                <p className="text-xs text-muted">Your account is attached. No passwords, keys or tokens are ever sent.</p>
                <div className="flex gap-2"><button className="btn-primary" disabled={busy} onClick={() => void send()}>{busy ? <Spinner className="!h-4 !w-4" /> : null}Send Issue</button>
                  <button className="btn-secondary" disabled={busy} onClick={() => setView("chat")}>Cancel</button></div>
              </div>)}

            {view === "sent" && sent && (
              <div className="space-y-2">
                <p className="font-semibold">Your issue has been sent to the DreamCast admin team.</p>
                <p>Support ticket <span className="font-mono font-semibold">#{sent.id}</span></p>
                <p className="text-xs text-muted">You can follow it, and read the team's reply, under My tickets.</p>
                <div className="flex gap-2"><button className="btn-secondary" onClick={() => void openTicket(sent.id)}>View ticket</button><button className="btn-ghost" onClick={() => setView("chat")}>Back to chat</button></div>
              </div>)}

            {view === "tickets" && (tickets === null && !err ? <p className="flex items-center gap-2 text-muted"><Spinner className="!h-4 !w-4" /> Loading…</p>
              : tickets?.length === 0 ? <p className="text-muted">You haven't sent any support tickets yet.</p>
              : <ul className="space-y-2">{tickets?.map((t) => (
                  <li key={t.id}><button className="card w-full p-3 text-left hover:border-accent" onClick={() => void openTicket(t.id)}>
                    <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs font-semibold">#{t.id}</span><TicketBadge status={t.status} /></div>
                    <p className="mt-1 line-clamp-2 break-words">{t.subject}</p>
                    <p className="mt-1 text-xs text-muted">Created {formatDate(t.created_at)} · Updated {formatDate(t.updated_at)}{t.admin_response ? " · Reply from the team" : ""}</p></button></li>))}</ul>)}

            {view === "ticket" && current && (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2"><span className="font-mono font-semibold">#{current.id}</span><TicketBadge status={current.status} /></div>
                <div><p className="text-xs text-muted">Issue · {current.category_label}</p><p className="break-words font-medium">{current.subject}</p>{current.description !== current.subject && <p className="mt-1 whitespace-pre-line break-words text-muted">{current.description}</p>}</div>
                <p className="text-xs text-muted">Created {formatDate(current.created_at)} · Updated {formatDate(current.updated_at)}</p>
                {current.admin_response && <div className="rounded-lg border border-accent/40 bg-accent/5 p-3"><p className="text-xs font-semibold text-accent">Reply from the DreamCast team</p><p className="mt-1 whitespace-pre-line break-words">{current.admin_response}</p></div>}
                {current.messages.length > 1 && <ul className="space-y-1.5 text-xs">{current.messages.map((m, i) => <li key={i} className={m.author === "admin" ? "text-accent" : "text-muted"}><span className="font-semibold">{m.author === "admin" ? "Team" : "You"}:</span> <span className="whitespace-pre-line break-words">{m.body}</span></li>)}</ul>}
                {(current.status === "open" || current.status === "in_progress")
                  ? <form onSubmit={addInfo} className="space-y-2"><label htmlFor="sup-more" className="text-xs font-medium">Add more information</label>
                      <textarea id="sup-more" className="field" rows={3} maxLength={4000} value={more} onChange={(e) => setMore(e.target.value)} />
                      <button className="btn-secondary" disabled={busy || !more.trim()}>{busy ? <Spinner className="!h-4 !w-4" /> : null}Add to ticket</button></form>
                  : <p className="text-xs text-muted">This ticket is {STATUS_LABEL[current.status].toLowerCase()}. If the problem is back, send a new issue from the chat.</p>}
              </div>)}
            {err && <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{err}</p>}
            <div ref={end} />
          </div>

          {view === "chat" && (
            <form onSubmit={submitText} className="flex items-center gap-2 border-t border-border p-2.5">
              <label htmlFor="sup-input" className="sr-only">Describe the problem</label>
              <input id="sup-input" className="field !py-2" placeholder="Describe the problem…" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} autoComplete="off" />
              <button className="btn-primary !p-2.5" aria-label="Send message" disabled={busy || !text.trim()}><Send className="h-4 w-4" aria-hidden /></button>
            </form>)}
        </section>
      )}
    </>
  );
}
