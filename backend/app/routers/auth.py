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
from ..schemas import ForgotIn, LoginIn, ProfileIn, RegisterIn, ResendIn, ResetIn, TokenOut, UserOut, VerifyEmailIn
from ..services import email_auth, subscriptions
from ..security import (auth_rate_limit, create_token, hash_password, login_failure_limit, otp_send_limit, otp_verify_limit, reset_send_limit,
                        verify_password)

log = logging.getLogger("dreamcast")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _require_local():
    if get_settings().auth_provider != "local":
        raise AppError("This action is handled by your authentication provider.", 400, "wrong_auth_provider")


def _token_out(user: User) -> TokenOut:
    return TokenOut(access_token=create_token(user.id), user=user)


def _verification_info(user: User, db: Session, expires_in: int) -> dict:
    return {"verification_required": True, "email": user.email, "expires_in": expires_in, "resend_after": email_auth.seconds_until_resend(db, user)}


GENERIC_SENT = "If that email can be verified, we've sent a new code. It may take a minute to arrive."


@router.get("/config")
def auth_config():
    """Public, non-secret settings the browser needs. The Supabase anon key is public by design; no secret is ever listed here."""
    s = get_settings()
    return {"provider": s.auth_provider,
            "url": s.auth_url if s.auth_provider == "supabase" else None,
            "public_key": s.auth_public_key if s.auth_provider == "supabase" else None,
            "google_enabled": s.google_enabled and s.auth_provider == "local",
            "email_verification": s.require_email_verification and s.auth_provider == "local"}


@router.post("/register", dependencies=[Depends(auth_rate_limit)])
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Without email verification (REQUIRE_EMAIL_VERIFICATION=false): creates the account and signs in (token).
    With it: creates an UNVERIFIED account and emails a one-time code; no token is issued until the code is entered.
    An email that already has an account gets the same answer (and an email to its owner), so sign-up doesn't reveal who is registered."""
    _require_local()
    s = get_settings()
    email = body.email.lower()
    existing = db.scalars(select(User).where(User.email == email)).first()
    if not s.require_email_verification:
        if existing:
            raise AppError("An account with this email already exists.", 409, "email_taken")
        user = User(email=email, name=body.name.strip() or email.split("@")[0], password_hash=hash_password(body.password), email_verified=True,
                    role="ADMIN" if email in s.admin_email_set else "USER", last_login_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
        subscriptions.ensure_subscription(db, user)
        return _token_out(user)
    email_auth.require_email_ready()
    if existing and existing.email_verified:
        email_auth.send_already_registered_email(existing)
        return {"verification_required": True, "email": email, "expires_in": s.otp_ttl_minutes * 60, "resend_after": s.otp_resend_seconds}
    if existing:     # never verified: whoever controls the inbox decides, so a new sign-up replaces the pending password
        user = existing
        user.password_hash, user.name = hash_password(body.password), body.name.strip() or user.name
    else:
        user = User(email=email, name=body.name.strip() or email.split("@")[0], password_hash=hash_password(body.password), email_verified=False,
                    role="ADMIN" if email in s.admin_email_set else "USER")
        db.add(user)
    db.commit()
    subscriptions.ensure_subscription(db, user)
    if otp_send_limit.blocked(f"otp:{email}"):
        raise AppError("Too many codes requested. Please wait a while and try again.", 429, "rate_limited")
    otp_send_limit.hit(f"otp:{email}")
    expires_in = email_auth.issue_code(db, user)
    return _verification_info(user, db, expires_in)


@router.post("/verify-email", response_model=TokenOut, dependencies=[Depends(auth_rate_limit)])
def verify_email(body: VerifyEmailIn, db: Session = Depends(get_db)):
    _require_local()
    email = body.email.lower()
    if otp_verify_limit.blocked(f"verify:{email}"):
        raise AppError("Too many attempts. Please wait a few minutes and try again.", 429, "rate_limited")
    otp_verify_limit.hit(f"verify:{email}")
    user = email_auth.verify_code(db, email, body.code)
    if not user.is_active:
        raise AppError("This account has been disabled.", 403, "account_disabled")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return _token_out(user)


@router.post("/resend-otp", dependencies=[Depends(auth_rate_limit)])
def resend_otp(body: ResendIn, db: Session = Depends(get_db)):
    """Always answers the same way. A new code cancels the previous one."""
    _require_local()
    if not get_settings().require_email_verification:
        raise AppError("Email verification is not enabled.", 400, "verification_disabled")
    email_auth.require_email_ready()
    email = body.email.lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    if user and not user.email_verified and user.password_hash and email_auth.seconds_until_resend(db, user) == 0 and not otp_send_limit.blocked(f"otp:{email}"):
        otp_send_limit.hit(f"otp:{email}")
        email_auth.issue_code(db, user)
    return {"message": GENERIC_SENT, "resend_after": get_settings().otp_resend_seconds, "expires_in": get_settings().otp_ttl_minutes * 60}


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
    if get_settings().require_email_verification and not user.email_verified:
        raise AppError("Please verify your email address to continue. We can send you a new code.", 403, "email_not_verified")
    if user.email in get_settings().admin_email_set:
        user.role = "ADMIN"
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return _token_out(user)


@router.post("/forgot-password", dependencies=[Depends(auth_rate_limit)])
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Emails a single-use reset link. The answer is identical whether or not the email has an account."""
    _require_local()
    email_auth.require_email_ready()
    email = body.email.lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    if user and user.password_hash and user.is_active and not reset_send_limit.blocked(f"reset:{email}"):
        reset_send_limit.hit(f"reset:{email}")
        email_auth.send_reset_email(db, user)
    return {"message": "If an account exists for that email, we've sent a reset link. It may take a minute to arrive."}


@router.post("/reset-password", dependencies=[Depends(auth_rate_limit)])
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    _require_local()
    user = email_auth.consume_reset_token(db, body.token)
    user.password_hash = hash_password(body.password)        # the old password stays valid until this point
    user.email_verified = True                               # the reset link reached their inbox
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
