from sqlalchemy.orm import Session

from ..models import Notification


def notify(db: Session, user_id: str, title: str, message: str = "", *, type: str = "info",
           job_id: str | None = None, project_id: str | None = None, asset_id: str | None = None) -> Notification:
    n = Notification(user_id=user_id, type=type, title=title, message=message, job_id=job_id, project_id=project_id, asset_id=asset_id)
    db.add(n)
    db.commit()
    return n
