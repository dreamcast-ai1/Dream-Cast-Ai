import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Request

from .config import get_settings
from .errors import AppError

ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(subject: str, purpose: str = "access", minutes: int | None = None, extra: dict | None = None) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or s.access_token_minutes)
    payload = {"sub": subject, "purpose": purpose, "exp": exp, "iss": "dreamcast", **(extra or {})}
    return jwt.encode(payload, s.auth_secret_key, algorithm=ALGO)


def decode_local_token(token: str, purpose: str = "access") -> dict:
    payload = jwt.decode(token, get_settings().auth_secret_key, algorithms=[ALGO], issuer="dreamcast")
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong purpose")
    return payload


_filename_re = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Strip any path components and unsafe characters from a client-supplied filename."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = _filename_re.sub("_", base).strip("._") or "file"
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_len - len(ext) - 1] + dot + ext) if dot else base[:max_len]
    return base


def client_ip(request: Request) -> str:
    """The caller's IP. On Render every request arrives from the platform proxy, so the first X-Forwarded-For hop is used
    (only when trusted: production or TRUST_PROXY_HEADERS=true) or all visitors would share one rate-limit bucket."""
    if get_settings().use_forwarded_for:
        first = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Tiny in-memory sliding-window limiter (single process; use Redis if you scale out)."""

    def __init__(self, limit_getter, window: int = 60):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._limit_getter, self._window = limit_getter, window

    def __call__(self, request: Request):
        limit = self._limit_getter()
        if limit <= 0:
            return
        key = f"{request.url.path}:{client_ip(request)}"
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self._window:
            q.popleft()
        if len(q) >= limit:
            raise AppError("Too many attempts. Please wait a minute and try again.", 429, "rate_limited")
        q.append(now)

    def reset(self):
        self._hits.clear()

    # Per-key variant (e.g. one account) that does not depend on the caller's IP, so a spoofed X-Forwarded-For can't dodge it.
    def blocked(self, key: str) -> bool:
        limit = self._limit_getter()
        if limit <= 0:
            return False
        q, now = self._hits[key], time.monotonic()
        while q and now - q[0] > self._window:
            q.popleft()
        return len(q) >= limit

    def hit(self, key: str) -> None:
        if self._limit_getter() > 0:
            self._hits[key].append(time.monotonic())


auth_rate_limit = RateLimiter(lambda: get_settings().rate_limit_auth_per_minute)
def _hourly(n: int):
    # Disabled together with the other auth limits (RATE_LIMIT_AUTH_PER_MINUTE=0), so the same switch covers all of them.
    return lambda: n if get_settings().rate_limit_auth_per_minute > 0 else 0


otp_send_limit = RateLimiter(_hourly(5), window=3600)          # verification emails per account per hour
otp_verify_limit = RateLimiter(_hourly(10), window=600)        # code guesses per account per 10 minutes
reset_send_limit = RateLimiter(_hourly(5), window=3600)        # reset emails per account per hour
login_failure_limit = RateLimiter(lambda: get_settings().rate_limit_auth_per_minute)     # failed logins per account
