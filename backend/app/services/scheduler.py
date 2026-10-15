"""Automation: one `tick()` does everything that should happen without a human —
monthly run creation, dunning reminders, credit-control evaluation. A daemon
thread calls it every 15 minutes; the same function backs the 'Run now' button,
so behaviour is identical and testable."""
from __future__ import annotations

import datetime
import json
import threading
import time
import traceback

from app.database import SessionLocal
from app.models import AuditLog, Setting
from app.services import credit, reminders, settings_store, usage_db

TICK_SECONDS = 15 * 60
LAST_TICK_KEY = "AUTOMATION_LAST_TICK"
_started = False
_lock = threading.Lock()


class SystemUser:
    email = "system@scheduler"
    role = "system"


def _log(db, action: str, detail: str):
    db.add(AuditLog(user=SystemUser.email, action=action, entity="automation", detail=detail))
    db.commit()


def previous_period(today: datetime.date) -> str:
    first = today.replace(day=1)
    last_month = first - datetime.timedelta(days=1)
    return last_month.strftime("%Y-%m")


def tick(db, today: datetime.date | None = None, steps: tuple[str, ...] = ("run", "recurring", "dunning", "credit")) -> dict:
    from app.routers.billing_routes import RunExists, run_from_database  # lazy: avoids an import cycle

    today = today or datetime.date.today()
    automation = settings_store.get_automation(db)
    result: dict = {"at": datetime.datetime.utcnow().isoformat(timespec="seconds"), "steps": {}}

    if "run" in steps:
        step: dict = {"enabled": settings_store.is_yes(automation["AUTO_RUN_ENABLED"])}
        period = previous_period(today)
        step["period"] = period
        if not step["enabled"]:
            step["outcome"] = "disabled"
        elif today.day < int(automation["AUTO_RUN_DAY"] or 1):
            step["outcome"] = f"waiting for day {automation['AUTO_RUN_DAY']}"
        elif not usage_db.is_configured():
            step["outcome"] = "usage database not configured"
        else:
            try:
                out = run_from_database(db, SystemUser, period, 0.0, set(), force=False, trigger="scheduled")
                step["outcome"] = f"created run #{out['run_id']} ({out['status']}, {len(out['invoices'])} invoices, {len(out['anomalies'])} findings)"
                _log(db, "automation.run", f"{period}: {step['outcome']}")
            except RunExists:
                step["outcome"] = "run already exists"
            except usage_db.UsageDbError as exc:
                step["outcome"] = f"failed: {exc}"
                _log(db, "automation.run_failed", f"{period}: {exc}")
        result["steps"]["run"] = step

    if "recurring" in steps:
        from app.services import sales
        made = [o for o in sales.run_recurring(db, today) if "invoice_no" in o]
        result["steps"]["recurring"] = {"generated": len(made), "invoices": [m["invoice_no"] for m in made]}
        if made:
            _log(db, "automation.recurring", f"generated {len(made)}: {', '.join(m['invoice_no'] for m in made)}")

    if "dunning" in steps:
        out = reminders.run_reminders(db, today)
        result["steps"]["dunning"] = {"emailed": len(out["sent"]), "logged_only": len(out["logged_only"]),
                                      "held_for_promise": len(out["skipped_promise"]), "disabled": out["disabled"]}
        if out["sent"] or out["logged_only"]:
            _log(db, "automation.dunning", f"emailed={len(out['sent'])} logged_only={len(out['logged_only'])}")

    if "credit" in steps:
        days = int(automation["BLOCK_AFTER_OVERDUE_DAYS"] or 0)
        changes = credit.evaluate(db, days, today)
        result["steps"]["credit"] = {"enabled": days > 0, "changes": changes}
        for ch in changes:
            _log(db, "automation.credit", f"{ch['enterprise_id']} {ch['change']}: {ch['reason']}")

    row = db.query(Setting).filter(Setting.key == LAST_TICK_KEY).first()
    payload = json.dumps(result)
    if row is None:
        db.add(Setting(key=LAST_TICK_KEY, value=payload))
    else:
        row.value = payload
    db.commit()
    return result


def last_tick(db) -> dict | None:
    row = db.query(Setting).filter(Setting.key == LAST_TICK_KEY).first()
    return json.loads(row.value) if row and row.value else None


def _loop():
    while True:
        time.sleep(TICK_SECONDS)
        db = SessionLocal()
        try:
            with _lock:
                tick(db)
        except Exception:  # never let the scheduler thread die
            traceback.print_exc()
        finally:
            db.close()


def start_background():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="billing-scheduler", daemon=True).start()
