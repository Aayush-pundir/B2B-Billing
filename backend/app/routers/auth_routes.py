import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import dev_login_allowed, get_settings
from app.database import get_db
from app.models import User
from app.services.auth import (
    COOKIE_NAME, ROLES, bootstrap_or_get_user, create_token, get_current_user,
)

router = APIRouter()


class DevLoginRequest(BaseModel):
    email: str
    role: str = "viewer"
    passcode: str = ""


_FAILS: dict[str, list[float]] = {}
_MAX_FAILS, _WINDOW = 8, 300.0


def _throttle(key: str, failed: bool | None = None) -> None:
    """Allow a handful of wrong passcodes per client per five minutes."""
    now = time.monotonic()
    recent = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
    if failed is None and len(recent) >= _MAX_FAILS:
        raise HTTPException(429, "Too many wrong passcodes — wait a few minutes and try again")
    if failed:
        recent.append(now)
    _FAILS[key] = recent


@router.post("/auth/dev-login")
def dev_login(payload: DevLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if not dev_login_allowed(settings):
        raise HTTPException(403, "Dev login is disabled on this server — sign in with SSO")
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Enter a valid email")
    if settings.ACCESS_PASSCODE:
        who = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
        _throttle(who)
        if not hmac.compare_digest(payload.passcode.encode(), settings.ACCESS_PASSCODE.encode()):
            _throttle(who, failed=True)
            raise HTTPException(401, "Wrong passcode")
        role = "viewer"  # the server decides: existing users keep their role, newcomers start read-only
    else:
        role = payload.role if payload.role in ROLES else "viewer"
    user = bootstrap_or_get_user(db, email, role)
    token = create_token(user.email, user.role)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600, secure=request.url.scheme == "https")
    return {"email": user.email, "role": user.role}


@router.get("/auth/providers")
def providers():
    settings = get_settings()
    return {
        "microsoft": bool(settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET),
        "google": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
        "dev": dev_login_allowed(settings),
        "passcode": bool(settings.ACCESS_PASSCODE),
    }


@router.get("/auth/me")
def me(user: User | None = Depends(get_current_user)):
    if user is None:
        return None
    return {"email": user.email, "role": user.role, "provider": user.provider}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/auth/microsoft")
def microsoft_login():
    settings = get_settings()
    if not (settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET):
        return {"error": "Microsoft OAuth not configured. Set AZURE_CLIENT_ID / AZURE_CLIENT_SECRET in .env."}
    # Full OAuth code-exchange flow is wired via AZURE_CLIENT_ID/SECRET once
    # registered in Azure AD — see docs/README for the redirect URI to use.
    return {"error": "Microsoft OAuth flow not yet completed. Use dev-login for now."}


@router.get("/auth/google")
def google_login():
    settings = get_settings()
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        return {"error": "Google OAuth not configured. Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env."}
    return {"error": "Google OAuth flow not yet completed. Use dev-login for now."}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    db_ok = True
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "microsoft_oauth": bool(settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET),
        "google_oauth": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
        "zoho": bool(settings.ZOHO_REFRESH_TOKEN and settings.ZOHO_ORG_ID),
    }
