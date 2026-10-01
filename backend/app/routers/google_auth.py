import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import AppError
from ..security import auth_rate_limit, create_token
from ..services import google_oauth

log = logging.getLogger("dreamcast.google")
router = APIRouter(prefix="/api/auth/google", tags=["auth"], dependencies=[Depends(auth_rate_limit)])


def _back_to_login(code: str) -> RedirectResponse:
    r = RedirectResponse(f"{get_settings().frontend_url.rstrip('/')}/login?error={quote(code)}", status_code=302)
    r.delete_cookie(google_oauth.STATE_COOKIE, path="/api/auth/google")
    return r


@router.get("/start")
def start():
    """Sends the browser to Google. The state cookie is HttpOnly and only valid for this one sign-in attempt."""
    if get_settings().auth_provider != "local":
        raise AppError("This action is handled by your authentication provider.", 400, "wrong_auth_provider")
    google_oauth.require_enabled()
    url, nonce = google_oauth.start()
    r = RedirectResponse(url, status_code=302)
    r.set_cookie(google_oauth.STATE_COOKIE, nonce, max_age=google_oauth.STATE_MINUTES * 60, httponly=True, samesite="lax",
                 secure=get_settings().is_production, path="/api/auth/google")
    return r


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """Google sends the browser back here. On success the browser lands on the frontend with a normal DreamCast session token
    in the URL fragment (never sent to any server), exactly like the existing /auth/callback page expects."""
    if error or not code or not state:
        return _back_to_login("google_cancelled" if error == "access_denied" else "google_failed")
    try:
        user = google_oauth.complete(db, code, state, request.cookies.get(google_oauth.STATE_COOKIE))
    except google_oauth.GoogleAuthError as e:
        log.warning("google sign-in refused: %s", e)             # reason only; codes and tokens are never logged
        return _back_to_login("google_failed")
    except AppError:
        return _back_to_login("google_unavailable")
    r = RedirectResponse(f"{get_settings().frontend_url.rstrip('/')}/auth/callback#access_token={create_token(user.id)}", status_code=302)
    r.delete_cookie(google_oauth.STATE_COOKIE, path="/api/auth/google")
    r.headers["Cache-Control"] = "no-store"
    return r
