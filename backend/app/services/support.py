"""In-app Support: a deterministic troubleshooting assistant plus a database-backed ticket system.

No AI is used here and no provider is ever called: answers come from the static knowledge base in support_kb.py by keyword scoring, and
tickets are rows in our own database. Free-text that reaches a ticket is scrubbed of anything that looks like a secret before it is stored."""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError, NotFound
from ..models import Project, SupportMessage, SupportTicket, User
from ..plans import public_plans
from . import features, usage
from .notifications import notify
from .support_kb import CATEGORIES, DISABLED_TOPICS, ENTRIES, QUICK_ACTIONS, Entry

STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("low", "normal", "high", "critical")
FIRST_NUMBER = 1001
MAX_USER_MESSAGES = 20
GREETING = "Hi! I'm DreamCast Support. I can help troubleshoot common problems. I don't use AI credits. Tell me what went wrong."

# --------------------------------------------------------------------------- matching
_CONTRACTIONS = (("can't", "cant"), ("cannot", "cant"), ("can not", "cant"), ("won't", "wont"), ("doesn't", "doesnt"), ("isn't", "isnt"), ("didn't", "didnt"),
                 ("don't", "dont"), ("won’t", "wont"), ("can’t", "cant"), ("doesn’t", "doesnt"), ("isn’t", "isnt"))


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def normalize(text: str) -> str:
    t = (text or "").lower()
    for a, b in _CONTRACTIONS:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(_stem(w) for w in t.split())


_NORMALIZED = {e.id: [(normalize(k), len(k.split())) for k in e.keywords] for e in ENTRIES}


def score(entry: Entry, text: str, context_category: str | None = None) -> float:
    padded = f" {text} "
    total = 0.0
    for kw, words in _NORMALIZED[entry.id]:
        if kw and f" {kw} " in padded:
            total += words * words          # a matched phrase is much stronger evidence than a single common word
    if total and context_category == entry.category:
        total += 0.5                          # the page the user is on breaks ties
    return total


def best_entry(message: str, context_category: str | None = None) -> Entry | None:
    text = normalize(message)
    scored = [(score(e, text, context_category), -i, e) for i, e in enumerate(ENTRIES)]
    top = max(scored, key=lambda t: (t[0], t[1]))
    return top[2] if top[0] >= 1 else None


PAGE_CATEGORY = {"create": None, "write": "WRITE", "plans": "PAYMENT", "usage": "USAGE", "project": "PROJECT", "library": "PROJECT", "history": "VIDEO", "login": "AUTH"}


def _plans_text() -> str:
    """Built from the real plan configuration, so a price or allowance change needs no edit here."""
    parts = []
    for p in public_plans():
        price = "Free" if p.price_minor == 0 else f"₹{p.price_minor // 100}/month"
        parts.append(f"{p.name}: {price}, {p.limits['video']} videos/month")
    return "; ".join(parts)


def _usage_text(db: Session, user: User) -> str:
    try:
        item = next(i for i in usage.summary(db, user.id) if i["generator"] == "video")
        return f"You have used {item['used']} of {item['limit']} videos this month ({item['remaining']} left)."
    except Exception:       # never let a lookup break an answer
        return "Open Usage to see how many generations you have left this month."


# --------------------------------------------------------------------------- chat
def _button(id_: str, label: str) -> dict:
    return {"id": id_, "label": label}


def _menu_buttons() -> list[dict]:
    return [_button(f"quick:{cat}", label) for label, cat in QUICK_ACTIONS]


def _answer(db: Session, user: User, entry: Entry) -> dict:
    subs = {"plans": _plans_text(), "usage": _usage_text(db, user)}
    steps = [s.format(**subs) if "{" in s else s for s in entry.steps]
    feature_id = entry.feature
    if feature_id and not features.is_enabled(db, feature_id):
        return unavailable(entry.title)
    return {"matched": True, "category": entry.category, "entry": entry.id, "title": entry.title,
            "reply": f"I can help troubleshoot: {entry.title}.\nTry these first:", "steps": steps, "follow_up": entry.follow_up,
            "buttons": [_button("solved", "Yes, solved"), _button("not_working", "Still not working")] if entry.escalate else []}


def unavailable(topic: str) -> dict:
    return {"matched": True, "category": "GENERAL", "entry": "unavailable", "title": topic, "steps": [], "follow_up": None,
            "reply": "This feature is currently unavailable on DreamCast, so there is nothing to fix on your side. Is there something else I can help with?",
            "buttons": _menu_buttons()}


def no_match() -> dict:
    return {"matched": False, "category": "GENERAL", "entry": None, "title": "", "steps": [], "follow_up": None,
            "reply": ("I don't have a troubleshooting guide for that issue yet.\nYou can:\n• Describe what happened in more detail\n"
                      "• Try refreshing and signing in again\n• Send the issue directly to the DreamCast admin team"),
            "buttons": [_button("describe_more", "Describe More"), _button("escalate", "Send to Admin")]}


def chat(db: Session, user: User, message: str = "", action: str = "", category: str = "", page: str = "") -> dict:
    """One turn. Stateless: the client sends the user's text or the id of the button they pressed. Only this module's own data and the
    feature/plan configuration are read; no external call of any kind is made."""
    page_key = (page or "").strip().lower()
    context_category = PAGE_CATEGORY.get(page_key)
    if action == "solved":
        return {"matched": True, "category": category or "GENERAL", "entry": None, "title": "", "steps": [], "follow_up": None,
                "reply": "Glad that helped! If anything else comes up, I'm here.", "buttons": _menu_buttons()}
    if action == "not_working":
        return {"matched": True, "category": category or "GENERAL", "entry": None, "title": "", "steps": [], "follow_up": None,
                "reply": ("Sorry that didn't solve it. You can send this issue to the DreamCast admin team. Your account and the troubleshooting "
                          "information will be attached so the team can investigate."),
                "buttons": [_button("escalate", "Send to Admin"), _button("continue", "Continue Troubleshooting")]}
    if action in ("continue", "describe_more"):
        return {"matched": False, "category": category or "GENERAL", "entry": None, "title": "", "steps": [], "follow_up": None,
                "reply": "Okay. Tell me a bit more: what did you click, and what did you see (the exact message helps)?", "buttons": _menu_buttons()}
    if action == "quick":
        cat = category if category in CATEGORIES else "GENERAL"
        entry = next((e for e in ENTRIES if e.category == cat and e.intro), None)
        return _answer(db, user, entry) if entry else no_match()
    message = (message or "").strip()
    if not message:
        raise AppError("Please type a few words about the problem.", 422, "validation_error")
    text = normalize(message)
    for feature_id, words in DISABLED_TOPICS:           # asking about a switched-off feature: "unavailable", never a repair guide
        if any(f" {normalize(w)} " in f" {text} " for w in words) and not features.is_enabled(db, feature_id):
            return unavailable(feature_id.replace("_", " "))
    entry = best_entry(message, context_category)
    return _answer(db, user, entry) if entry else no_match()


def options(db: Session) -> dict:
    return {"greeting": GREETING, "categories": [{"id": k, "label": v} for k, v in CATEGORIES.items()],
            "quick_actions": [{"label": label, "category": cat} for label, cat in QUICK_ACTIONS],
            "ticket_limit_per_hour": get_settings().support_ticket_hourly_limit}


# --------------------------------------------------------------------------- scrubbing and safe context
_SECRET_PATTERNS = (
    re.compile(r"bearer\s+[A-Za-z0-9._\-~+/=]{8,}", re.I),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),            # JWTs / access tokens
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                                                  # Google API keys
    re.compile(r"\b(?:sk|gsk|rzp|pk|hf|fal)[_\-][A-Za-z0-9_\-]{10,}", re.I),               # provider keys (OpenAI, Groq, Razorpay, ...)
    re.compile(r"\b(?:api[_\- ]?key|secret|password|passwd|token|authorization)\b\s*[:=]\s*\S+", re.I),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),                                                     # long hex strings
    re.compile(r"\b[A-Za-z0-9+/_\-]{48,}={0,2}"),                                            # long opaque strings
    re.compile(r"(?:postgres(?:ql)?|mysql|redis|s3)(?:\+\w+)?://\S+", re.I),                 # connection strings
)
SAFE_CONTEXT_KEYS = {"app_version": 40, "browser": 160, "timestamp": 40, "request_id": 64, "project_id": 40, "path": 120}


def scrub(text: str | None, limit: int) -> str:
    out = " ".join((text or "").split()) if limit <= 300 else (text or "").strip()
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[redacted]", out)
    return out[:limit]


def safe_context(db: Session, user: User, raw: dict | None) -> dict:
    """Only allow-listed, short, scrubbed fields. A project id is kept only when it belongs to the user."""
    clean: dict = {}
    for key, limit in SAFE_CONTEXT_KEYS.items():
        v = (raw or {}).get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            clean[key] = scrub(str(v), limit)
    pid = clean.get("project_id")
    if pid:
        project = db.get(Project, pid)
        if not project or project.user_id != user.id:
            clean.pop("project_id")
    return clean


def priority_for(category: str, text: str) -> str:
    """Decided by the server from known situations; users cannot choose it."""
    t = normalize(text)
    if category == "PAYMENT" and any(w in t for w in ("charged", "deducted", "money", "paid but", "double", "twice", "debited")):
        return "critical"
    if category in ("PAYMENT", "AUTH") or any(w in t for w in ("lost my", "data loss", "deleted", "missing project", "cant access", "cant login")):
        return "high"
    return "normal"


# --------------------------------------------------------------------------- tickets
def reference(ticket: SupportTicket) -> str:
    return f"DC-{ticket.number}"


def parse_reference(value: str) -> int:
    m = re.fullmatch(r"(?:dc-?)?(\d{1,9})", (value or "").strip(), re.I)
    if not m:
        raise NotFound("Ticket not found.")
    return int(m.group(1))


def _next_number(db: Session) -> int:
    return (db.scalar(select(func.max(SupportTicket.number))) or FIRST_NUMBER - 1) + 1


def _require_room(db: Session, user: User) -> None:
    limit = get_settings().support_ticket_hourly_limit
    if limit <= 0:
        return
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    n = db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.user_id == user.id, SupportTicket.created_at >= since)) or 0
    if n >= limit:
        raise AppError(f"You've sent {limit} support requests in the last hour. Please wait a little before sending another; we have your earlier ones.", 429, "rate_limited")


def create_ticket(db: Session, user: User, *, category: str, subject: str, description: str, page: str | None, feature: str | None,
                  error_message: str | None, context: dict | None) -> SupportTicket:
    _require_room(db, user)
    category = category if category in CATEGORIES else "GENERAL"
    subject = scrub(subject, 200) or "Support request"
    description = scrub(description, 4000)
    error = scrub(error_message, 500) or None
    for attempt in range(3):                                      # the unique number can collide with a concurrent request: take the next one
        t = SupportTicket(number=_next_number(db), user_id=user.id, category=category, subject=subject, description=description, status="open",
                          priority=priority_for(category, f"{subject} {description} {error or ''}"), page=scrub(page, 80) or None,
                          feature=scrub(feature, 80) or None, error_message=error, diagnostic_context=safe_context(db, user, context))
        db.add(t)
        try:
            db.commit()
            return t
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise AppError("Couldn't save your request just now. Please try again.", 503, "unavailable")
    raise AssertionError("unreachable")


def owned_ticket(db: Session, user: User, ticket_ref: str) -> SupportTicket:
    """404 (not 403) for someone else's ticket, so references can't be probed."""
    t = db.scalars(select(SupportTicket).where(SupportTicket.number == parse_reference(ticket_ref))).first()
    if not t or t.user_id != user.id:
        raise NotFound("Ticket not found.")
    return t


def any_ticket(db: Session, ticket_ref: str) -> SupportTicket:
    t = db.scalars(select(SupportTicket).where(SupportTicket.number == parse_reference(ticket_ref))).first()
    if not t:
        raise NotFound("Ticket not found.")
    return t


def user_add_message(db: Session, user: User, ticket: SupportTicket, body: str) -> SupportTicket:
    if ticket.status in ("resolved", "closed"):
        raise AppError("This ticket is already resolved. Please send a new issue if the problem is back.", 409, "ticket_closed")
    if sum(1 for m in ticket.messages if m.author == "user") >= MAX_USER_MESSAGES:
        raise AppError("This ticket has reached its message limit. The team already has your details.", 429, "rate_limited")
    ticket.messages.append(SupportMessage(author="user", body=scrub(body, 4000)))
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ticket


def admin_reply(db: Session, ticket: SupportTicket, body: str) -> SupportTicket:
    body = body.strip()
    ticket.messages.append(SupportMessage(author="admin", body=body[:4000]))
    ticket.admin_response = body[:4000]
    if ticket.status == "open":
        ticket.status = "in_progress"
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    notify(db, ticket.user_id, f"Support replied to {reference(ticket)}", body[:200], type="support")      # the existing notification bell
    return ticket


def admin_update(db: Session, ticket: SupportTicket, *, status: str | None, priority: str | None) -> SupportTicket:
    if status is not None:
        if status not in STATUSES:
            raise AppError("Unknown status.", 422, "validation_error")
        changed = status != ticket.status
        ticket.status = status
        ticket.resolved_at = datetime.now(timezone.utc) if status in ("resolved", "closed") else None
        if changed and status in ("resolved", "closed"):
            notify(db, ticket.user_id, f"{reference(ticket)} is {status}", ticket.subject[:200], type="support")
    if priority is not None:
        if priority not in PRIORITIES:
            raise AppError("Unknown priority.", 422, "validation_error")
        ticket.priority = priority
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ticket


def ticket_out(t: SupportTicket, *, admin: bool = False, user: User | None = None) -> dict:
    out = {"id": reference(t), "number": t.number, "category": t.category, "category_label": CATEGORIES.get(t.category, t.category), "subject": t.subject,
           "description": t.description, "status": t.status, "priority": t.priority, "page": t.page, "feature": t.feature, "error_message": t.error_message,
           "admin_response": t.admin_response, "created_at": t.created_at, "updated_at": t.updated_at, "resolved_at": t.resolved_at,
           "messages": [{"author": m.author, "body": m.body, "created_at": m.created_at} for m in t.messages]}
    if admin:
        out["diagnostic_context"] = t.diagnostic_context
        out["user"] = {"id": t.user_id, "email": user.email if user else None, "name": user.name if user else None}
    return out
