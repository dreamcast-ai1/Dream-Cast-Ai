"""Sign-up email verification (one-time codes) and email-based password reset.
Codes and reset tokens are never logged, never returned by the API, and only hashed values are stored."""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..email import EmailError, get_email_provider
from ..errors import AppError
from ..models import EmailVerification, PasswordResetToken, User

log = logging.getLogger("dreamcast.auth")
BAD_CODE = "That code is incorrect or has expired. Request a new one and try again."
BAD_RESET = "This reset link is invalid or has expired."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def email_ready() -> bool:
    return get_email_provider().is_configured()


def require_email_ready() -> None:
    if not email_ready():
        raise AppError("Email isn't set up on this server yet, so this can't be done right now. Please try again later.", 503, "email_not_configured")


def _send(to: str, subject: str, body: str) -> None:
    try:
        get_email_provider().send(to, subject, body)
    except EmailError:
        raise AppError("We couldn't send the email right now. Please try again in a minute.", 502, "email_send_failed")


# ------------------------------------------------------------------ one-time codes
def _hash_code(user_id: str, code: str) -> str:
    """Keyed hash: a leaked database alone is not enough to brute-force a 6-digit code."""
    key = get_settings().auth_secret_key.encode()
    return hmac.new(key, f"otp:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def seconds_until_resend(db: Session, user: User) -> int:
    last = db.scalars(select(EmailVerification).where(EmailVerification.user_id == user.id).order_by(EmailVerification.created_at.desc())).first()
    gap = get_settings().otp_resend_seconds
    if not last or gap <= 0:
        return 0
    return max(0, int(gap - (_now() - last.created_at).total_seconds()))


def issue_code(db: Session, user: User) -> int:
    """Creates a new code (cancelling any earlier one), emails it, and returns how many seconds it stays valid."""
    s = get_settings()
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = _now()
    db.execute(update(EmailVerification).where(EmailVerification.user_id == user.id, EmailVerification.used_at.is_(None)).values(used_at=now))
    db.add(EmailVerification(user_id=user.id, code_hash=_hash_code(user.id, code), expires_at=now + timedelta(minutes=s.otp_ttl_minutes)))
    db.commit()
    _send(user.email, "Your Dream Cast AI verification code",
          f"Your Dream Cast AI verification code is {code}.\n\nIt expires in {s.otp_ttl_minutes} minutes and can be used once. "
          f"If you didn't create an account, you can ignore this email.")
    return s.otp_ttl_minutes * 60


def verify_code(db: Session, email: str, code: str) -> User:
    """One generic failure message for every reason (unknown email, no code, wrong, expired, already used), so nothing leaks."""
    user = db.scalars(select(User).where(User.email == email)).first()
    row = None
    if user:
        row = db.scalars(select(EmailVerification).where(EmailVerification.user_id == user.id, EmailVerification.used_at.is_(None))
                         .order_by(EmailVerification.created_at.desc())).first()
    if not user or not row or row.expires_at <= _now():
        raise AppError(BAD_CODE, 400, "invalid_code")
    if not hmac.compare_digest(row.code_hash, _hash_code(user.id, (code or "").strip())):
        row.attempts += 1
        if row.attempts >= get_settings().otp_max_attempts:
            row.used_at = _now()                       # too many wrong guesses: this code is dead, a new one is needed
        db.commit()
        raise AppError(BAD_CODE, 400, "invalid_code")
    row.used_at = _now()
    user.email_verified = True
    db.commit()
    return user


# ------------------------------------------------------------------ password reset
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def send_reset_email(db: Session, user: User) -> None:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    now = _now()
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now))
    db.add(PasswordResetToken(user_id=user.id, token_hash=_hash_token(token), expires_at=now + timedelta(minutes=s.reset_ttl_minutes)))
    db.commit()
    _send(user.email, "Reset your Dream Cast AI password",
          f"Someone asked to reset the password for this Dream Cast AI account.\n\nChoose a new password here (valid for {s.reset_ttl_minutes} minutes, "
          f"single use):\n{s.frontend_url.rstrip('/')}/reset-password?token={token}\n\nIf this wasn't you, ignore this email: your password hasn't changed.")


def consume_reset_token(db: Session, token: str) -> User:
    row = db.scalars(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token or ""))).first()
    if not row or row.used_at is not None or row.expires_at <= _now():
        raise AppError(BAD_RESET, 400, "bad_reset_token")
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise AppError(BAD_RESET, 400, "bad_reset_token")
    now = _now()
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now))
    return user


def send_already_registered_email(user: User) -> None:
    """Someone tried to register an email that already has an account: tell the owner instead of telling the caller."""
    s = get_settings()
    try:
        get_email_provider().send(user.email, "Dream Cast AI sign-up attempt",
                                  f"Someone tried to create a Dream Cast AI account with this email address, which already has one.\n\n"
                                  f"If it was you, sign in or reset your password at {s.frontend_url.rstrip('/')}/login. Otherwise ignore this email.")
    except EmailError:
        pass
