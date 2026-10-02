"""Support assistant and tickets. Everything here is local: no AI or provider is ever called. Users reach only their own tickets;
the admin routes sit behind admin_user. The switch 'support_chatbot' (Admin -> Features) turns the user side off; admins keep access."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, current_user
from ..errors import AppError
from ..models import SupportTicket, User
from ..services import features, support

router = APIRouter(prefix="/api/support", tags=["support"])
admin_router = APIRouter(prefix="/api/admin/support", tags=["admin"], dependencies=[Depends(admin_user)])


def support_enabled(db: Session = Depends(get_db)) -> None:
    if not features.is_enabled(db, "support_chatbot"):
        raise AppError("Support isn't available right now.", 403, "feature_disabled")


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=1000)
    action: str = Field(default="", max_length=20)
    category: str = Field(default="", max_length=20)
    page: str = Field(default="", max_length=40)


class TicketIn(BaseModel):
    category: str = Field(default="GENERAL", max_length=20)
    subject: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    page: str | None = Field(default=None, max_length=80)
    feature: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=500)
    context: dict | None = None                    # reduced to an allow-list of safe fields by the service


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class TicketPatch(BaseModel):
    status: str | None = Field(default=None, max_length=15)
    priority: str | None = Field(default=None, max_length=10)


# ------------------------------------------------------------------ user
@router.get("/options", dependencies=[Depends(support_enabled)])
def options(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return support.options(db)


@router.post("/chat", dependencies=[Depends(support_enabled)])
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return support.chat(db, user, message=body.message, action=body.action, category=body.category, page=body.page)


@router.post("/tickets", status_code=201, dependencies=[Depends(support_enabled)])
def create_ticket(body: TicketIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if len((body.description or "").strip()) + len((body.subject or "").strip()) < 3:
        raise AppError("Please describe the problem.", 422, "validation_error")
    t = support.create_ticket(db, user, category=body.category, subject=body.subject or (body.description or "")[:80], description=body.description,
                              page=body.page, feature=body.feature, error_message=body.error_message, context=body.context)
    return support.ticket_out(t)


@router.get("/tickets", dependencies=[Depends(support_enabled)])
def my_tickets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.created_at.desc()).limit(100)).all()
    return {"tickets": [support.ticket_out(t) for t in rows]}


@router.get("/tickets/{ticket_id}", dependencies=[Depends(support_enabled)])
def my_ticket(ticket_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return support.ticket_out(support.owned_ticket(db, user, ticket_id))


@router.post("/tickets/{ticket_id}/messages", status_code=201, dependencies=[Depends(support_enabled)])
def add_message(ticket_id: str, body: MessageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = support.owned_ticket(db, user, ticket_id)
    return support.ticket_out(support.user_add_message(db, user, t, body.message))


# ------------------------------------------------------------------ admin
@admin_router.get("/tickets")
def admin_tickets(status: str | None = None, category: str | None = None, priority: str | None = None, days: int | None = None, limit: int = 100,
                  db: Session = Depends(get_db)):
    q = select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(min(max(limit, 1), 300))
    if status:
        q = q.where(SupportTicket.status == status)
    if category:
        q = q.where(SupportTicket.category == category.upper())
    if priority:
        q = q.where(SupportTicket.priority == priority)
    if days:
        q = q.where(SupportTicket.created_at >= datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), 3650)))
    rows = db.scalars(q).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({t.user_id for t in rows}))).all()} if rows else {}
    counts = {s: len(db.scalars(select(SupportTicket.id).where(SupportTicket.status == s)).all()) for s in support.STATUSES}
    return {"tickets": [support.ticket_out(t, admin=True, user=users.get(t.user_id)) for t in rows], "counts": counts}


@admin_router.get("/tickets/{ticket_id}")
def admin_ticket(ticket_id: str, db: Session = Depends(get_db)):
    t = support.any_ticket(db, ticket_id)
    return support.ticket_out(t, admin=True, user=db.get(User, t.user_id))


@admin_router.patch("/tickets/{ticket_id}")
def admin_patch(ticket_id: str, body: TicketPatch, db: Session = Depends(get_db)):
    t = support.admin_update(db, support.any_ticket(db, ticket_id), status=body.status, priority=body.priority)
    return support.ticket_out(t, admin=True, user=db.get(User, t.user_id))


@admin_router.post("/tickets/{ticket_id}/reply")
def admin_reply(ticket_id: str, body: MessageIn, db: Session = Depends(get_db)):
    t = support.admin_reply(db, support.any_ticket(db, ticket_id), body.message)
    return support.ticket_out(t, admin=True, user=db.get(User, t.user_id))
