import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..errors import AppError, Unauthorized
from ..models import User
from ..schemas import ForgotIn, LoginIn, ProfileIn, RegisterIn, ResetIn, TokenOut, UserOut
from ..services import subscriptions
from ..security import auth_rate_limit, create_token, login_failure_limit, decode_local_token, hash_password, verify_password

log = logging.getLogger("dreamcast")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _require_local():
    if get_settings().auth_provider != "local":
        raise AppError("This action is handled by your authentication provider.", 400, "wrong_auth_provider")


@router.get("/config")
def auth_config():
    """Public, non-secret settings the browser needs. The Supabase anon key is public by design."""
    s = get_settings()
    return {"provider": s.auth_provider,
            "url": s.auth_url if s.auth_provider == "supabase" else None,
            "public_key": s.auth_public_key if s.auth_provider == "supabase" else None}


@router.post("/register", response_model=TokenOut, dependencies=[Depends(auth_rate_limit)])
def register(body: RegisterIn, db: Session = Depends(get_db)):
    _require_local()
    email = body.email.lower()
    if db.scalars(select(User).where(User.email == email)).first():
        raise AppError("An account with this email already exists.", 409, "email_taken")
    user = User(email=email, name=body.name.strip() or email.split("@")[0], password_hash=hash_password(body.password),
                role="ADMIN" if email in get_settings().admin_email_set else "USER",
                last_login_at=datetime.now(timezone.utc))
    db.add(user)
    db.commit()
    subscriptions.ensure_subscription(db, user)
    return TokenOut(access_token=create_token(user.id), user=user)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(auth_rate_limit)])
def login(body: LoginIn, db: Session = Depends(get_db)):
    _require_local()
    email = body.email.lower()
    if login_failure_limit.blocked(f"login:{email}"):
        raise AppError("Too many attempts. Please wait a minute and try again.", 429, "rate_limited")
    user = db.scalars(select(User).where(User.email == email)).first()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        login_failure_limit.hit(f"login:{email}")
        raise Unauthorized("Incorrect email or password.")
    if not user.is_active:
        raise AppError("This account has been disabled.", 403, "account_disabled")
    if user.email in get_settings().admin_email_set:
        user.role = "ADMIN"
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return TokenOut(access_token=create_token(user.id), user=user)


@router.post("/forgot-password", dependencies=[Depends(auth_rate_limit)])
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """No email server is bundled. In local mode the reset link is written to the backend log."""
    _require_local()
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    if user and user.password_hash:
        # Token embeds a slice of the current hash so it stops working once the password changes.
        token = create_token(user.id, "reset", minutes=30, extra={"h": user.password_hash[-12:]})
        log.warning("PASSWORD RESET LINK for %s: %s/reset-password?token=%s", user.email, get_settings().frontend_url, token)
    return {"message": "If an account exists for that email, a reset link has been generated."}


@router.post("/reset-password", dependencies=[Depends(auth_rate_limit)])
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    _require_local()
    try:
        claims = decode_local_token(body.token, "reset")
    except Exception:
        raise AppError("This reset link is invalid or has expired.", 400, "bad_reset_token")
    user = db.get(User, claims["sub"])
    if not user or not user.password_hash or user.password_hash[-12:] != claims.get("h"):
        raise AppError("This reset link is invalid or has expired.", 400, "bad_reset_token")
    user.password_hash = hash_password(body.password)
    db.commit()
    return {"message": "Password updated. You can now sign in."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.name = body.name
    db.commit()
    return user
