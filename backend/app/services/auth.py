import datetime

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

COOKIE_NAME = "cb_token"
ROLES = ["admin", "accounts", "finance", "sales", "viewer"]
# Billing terms ("commercials") are amended by the finance role only — through
# manual entry, import from a reviewed agreement, or bulk Excel upload. Admin
# manages users and system settings but deliberately cannot change terms.
ROLE_WRITE_ACCESS = {
    "admin": {"clients", "agreements", "settings", "users", "metrics", "addons", "datasource", "automation", "items"},
    "accounts": {"clients", "agreements", "billing", "credit", "items", "sales"},
    "finance": {"clients", "agreements", "billing", "commercials", "metrics", "addons", "approvals", "automation", "credit", "items", "sales", "purchases", "accounting"},
    "sales": set(),
    "viewer": set(),
}


def create_token(email: str, role: str) -> str:
    settings = get_settings()
    payload = {
        "email": email,
        "role": role,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def get_current_user(cb_token: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User | None:
    if not cb_token:
        return None
    payload = decode_token(cb_token)
    if not payload:
        return None
    user = db.query(User).filter(User.email == payload.get("email")).first()
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_write(entity: str):
    def checker(user: User = Depends(require_user)) -> User:
        allowed = ROLE_WRITE_ACCESS.get(user.role, set())
        if entity not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role '{user.role}' cannot write '{entity}'")
        return user
    return checker


def bootstrap_or_get_user(db: Session, email: str, requested_role: str = "viewer") -> User:
    settings = get_settings()
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        role = "admin" if email == settings.BOOTSTRAP_ADMIN_EMAIL else requested_role
        user = User(email=email, role=role, provider="dev")
        db.add(user)
        db.commit()
        db.refresh(user)
    user.last_login = datetime.datetime.utcnow()
    db.commit()
    return user


def require_any_write(*entities: str):
    def checker(user: User = Depends(require_user)) -> User:
        allowed = ROLE_WRITE_ACCESS.get(user.role, set())
        if not any(e in allowed for e in entities):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role '{user.role}' cannot access this")
        return user
    return checker


def require_roles(*roles: str):
    """Read access for finance data (ledger, purchases, reports) — not for sales/viewer roles."""
    def checker(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role '{user.role}' cannot view this")
        return user
    return checker


FINANCE_READERS = ("admin", "finance", "accounts")
