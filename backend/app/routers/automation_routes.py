import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Client, User
from app.routers.core_routes import log_action
from app.services import credit, reminders, scheduler, settings_store
from app.services.auth import require_any_write, require_user, require_write

router = APIRouter(prefix="/api")


# ------------------------------ automation policy ------------------------------

@router.get("/automation")
def get_automation(db: Session = Depends(get_db), user: User = Depends(require_user)):
    recent = (db.query(AuditLog).filter(AuditLog.user == scheduler.SystemUser.email)
              .order_by(AuditLog.id.desc()).limit(15).all())
    return {
        "policy": settings_store.get_automation(db),
        "last_tick": scheduler.last_tick(db),
        "tick_minutes": scheduler.TICK_SECONDS // 60,
        "recent": [{"ts": r.ts.isoformat(), "action": r.action, "detail": r.detail} for r in recent],
    }


class PolicyIn(BaseModel):
    values: dict[str, str]


@router.put("/automation")
def put_automation(payload: PolicyIn, db: Session = Depends(get_db), user: User = Depends(require_write("automation"))):
    v = payload.values
    try:
        if "DUNNING_LADDER" in v:
            reminders.parse_ladder(v["DUNNING_LADDER"])
        for key in ("AUTO_APPROVE_MAX_TOTAL", "ANOMALY_SWING_PCT"):
            if key in v and float(v[key] or 0) < 0:
                raise ValueError(f"{key} cannot be negative")
        if "AUTO_RUN_DAY" in v and not 1 <= int(v["AUTO_RUN_DAY"]) <= 28:
            raise ValueError("AUTO_RUN_DAY must be between 1 and 28")
        if "BLOCK_AFTER_OVERDUE_DAYS" in v and int(v["BLOCK_AFTER_OVERDUE_DAYS"]) < 0:
            raise ValueError("BLOCK_AFTER_OVERDUE_DAYS cannot be negative")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    result = settings_store.update_automation(db, v)
    log_action(db, user, "automation.policy", "automation", ",".join(v.keys()))
    return {"policy": result}


class RunNowIn(BaseModel):
    steps: list[str] = ["run", "recurring", "dunning", "credit"]


@router.post("/automation/run-now")
def run_now(payload: RunNowIn, db: Session = Depends(get_db), user: User = Depends(require_write("automation"))):
    steps = tuple(s for s in payload.steps if s in ("run", "recurring", "dunning", "credit"))
    result = scheduler.tick(db, steps=steps)
    log_action(db, user, "automation.run_now", "automation", ",".join(steps))
    return result


# ------------------------------ credit control ------------------------------

@router.get("/credit/exposure")
def credit_exposure(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = credit.exposure(db)
    return {"rows": rows, "totals": {
        "outstanding": round(sum(r["outstanding"] for r in rows), 2), "overdue": round(sum(r["overdue"] for r in rows), 2),
        "over_limit": sum("over_limit" in r["flags"] for r in rows), "blocked": sum(r["status"] == "blocked" for r in rows)}}


@router.get("/credit/forecast")
def credit_forecast(weeks: int = 8, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return credit.forecast(db, max(2, min(weeks, 26)))


@router.post("/credit/evaluate")
def credit_evaluate(db: Session = Depends(get_db), user: User = Depends(require_any_write("credit", "automation"))):
    days = int(settings_store.get_automation(db)["BLOCK_AFTER_OVERDUE_DAYS"] or 0)
    if days <= 0:
        raise HTTPException(400, "Auto-block is off — set 'Block after overdue days' on the Automation page")
    changes = credit.evaluate(db, days)
    log_action(db, user, "credit.evaluate", "credit", f"changes={len(changes)}")
    return {"changes": changes}


class StatusIn(BaseModel):
    status: str
    reason: str = ""


@router.put("/credit/customers/{client_id}/status")
def set_customer_status(client_id: int, payload: StatusIn, db: Session = Depends(get_db), user: User = Depends(require_any_write("credit", "automation"))):
    if payload.status not in ("active", "blocked", "inactive"):
        raise HTTPException(400, "Status must be active, blocked or inactive")
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    c.status = payload.status
    c.status_reason = f"manual: {payload.reason}".strip() if payload.status != "active" else ""
    db.commit()
    log_action(db, user, "credit.set_status", "client", f"{c.enterprise_id} -> {payload.status} {payload.reason}")
    return {"ok": True}
