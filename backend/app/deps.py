from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .db import get_db
from .errors import Forbidden, NotFound, Unauthorized
from .models import Project, User
from .services.auth import user_from_token


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized()
    return user_from_token(db, header[7:].strip())


def admin_user(user: User = Depends(current_user)) -> User:
    """Enforced server-side on every /api/admin route."""
    if user.role != "ADMIN":
        raise Forbidden("Administrator access required.")
    return user


def owned_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    """Returns 404 (not 403) for other users' projects so IDs can't be probed."""
    project = db.get(Project, project_id)
    if not project or project.user_id != user.id:
        raise NotFound("Project not found.")
    return project
