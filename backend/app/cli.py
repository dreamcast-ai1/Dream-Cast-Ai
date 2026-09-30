"""Small admin CLI:  python -m app.cli make-admin you@example.com"""
import sys

from sqlalchemy import select

from .db import SessionLocal
from .models import User


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("make-admin", "remove-admin"):
        print("usage: python -m app.cli make-admin|remove-admin <email>")
        return 2
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == sys.argv[2].lower())).first()
        if not user:
            print("No user with that email. Register first, then run this again.")
            return 1
        user.role = "ADMIN" if sys.argv[1] == "make-admin" else "USER"
        db.commit()
        print(f"{user.email} is now {user.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
