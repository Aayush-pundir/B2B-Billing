"""Credit control: exposure vs limit, automatic blocking of long-overdue
customers, and a collections forecast based on each customer's real payment behaviour."""
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.models import Client, Invoice, Payment

OPEN = ["issued", "sent", "partially_paid", "overdue", "disputed"]


def _d(value: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def exposure(db: Session, today: datetime.date | None = None) -> list[dict]:
    today = today or datetime.date.today()
    rows = []
    for c in db.query(Client).order_by(Client.name).all():
        invs = db.query(Invoice).filter(Invoice.client_id == c.id, Invoice.status.in_(OPEN)).all()
        outstanding = sum(i.balance_due or 0 for i in invs)
        overdue_amt, max_days, worst = 0.0, 0, ""
        for i in invs:
            due = _d(i.due_date)
            if due and due < today and not i.disputed:
                overdue_amt += i.balance_due or 0
                if (today - due).days > max_days:
                    max_days, worst = (today - due).days, i.invoice_no
        limit = c.credit_limit or 0
        flags = []
        if limit > 0 and outstanding > limit:
            flags.append("over_limit")
        elif limit > 0 and outstanding > 0.8 * limit:
            flags.append("near_limit")
        if max_days > 0:
            flags.append("overdue")
        rows.append({
            "client_id": c.id, "enterprise_id": c.enterprise_id, "name": c.name, "status": c.status or "active",
            "status_reason": c.status_reason or "", "credit_limit": limit, "outstanding": round(outstanding, 2),
            "overdue": round(overdue_amt, 2), "max_days_overdue": max_days, "worst_invoice": worst,
            "utilization_pct": round(outstanding / limit * 100, 1) if limit > 0 else None,
            "open_invoices": len(invs), "flags": flags,
        })
    return [r for r in rows if r["open_invoices"] or r["status"] != "active"]


def evaluate(db: Session, block_after_days: int, today: datetime.date | None = None) -> list[dict]:
    """Auto-block customers overdue beyond the threshold; auto-unblock the ones we blocked once they've cleared.
    Manual blocks (no 'auto:' reason) are never touched."""
    changes = []
    if block_after_days <= 0:
        return changes
    for r in exposure(db, today):
        c = db.query(Client).get(r["client_id"])
        if c.status == "active" and r["max_days_overdue"] > block_after_days:
            c.status = "blocked"
            c.status_reason = f"auto: ₹{r['overdue']:,.0f} overdue, oldest {r['max_days_overdue']} days ({r['worst_invoice']})"
            changes.append({"enterprise_id": c.enterprise_id, "name": c.name, "change": "blocked", "reason": c.status_reason})
        elif c.status == "blocked" and (c.status_reason or "").startswith("auto:") and r["max_days_overdue"] <= block_after_days:
            c.status, c.status_reason = "active", ""
            changes.append({"enterprise_id": c.enterprise_id, "name": c.name, "change": "unblocked", "reason": "overdue cleared"})
    db.commit()
    return changes


def _delay_by_client(db: Session) -> tuple[dict[int, float], float]:
    """Average days a customer pays after the due date (from matched payments); plus the org-wide average."""
    per: dict[int, list[int]] = {}
    for p in db.query(Payment).filter(Payment.invoice_id.isnot(None)).all():
        inv = db.query(Invoice).get(p.invoice_id)
        pd, due = _d(p.date), _d(inv.due_date) if inv else None
        if pd and due and inv:
            per.setdefault(inv.client_id, []).append(max(-5, min(90, (pd - due).days)))
    allv = [v for vs in per.values() for v in vs]
    org = sum(allv) / len(allv) if allv else 0.0
    return {k: sum(v[-6:]) / len(v[-6:]) for k, v in per.items()}, org


def forecast(db: Session, weeks: int = 8, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    delays, org_delay = _delay_by_client(db)
    buckets = [{"start": (monday + datetime.timedelta(weeks=i)).isoformat(),
                "end": (monday + datetime.timedelta(weeks=i, days=6)).isoformat(), "amount": 0.0, "count": 0} for i in range(weeks)]
    backlog = {"amount": 0.0, "count": 0}
    later = {"amount": 0.0, "count": 0}
    held = {"amount": 0.0, "count": 0}  # disputed — excluded from expected inflow

    for inv in db.query(Invoice).filter(Invoice.status.in_(OPEN)).all():
        amt = inv.balance_due or 0
        if amt <= 0:
            continue
        if inv.disputed:
            held["amount"] += amt; held["count"] += 1
            continue
        promised = _d(inv.promise_date)
        due = _d(inv.due_date) or today
        expected = promised or (due + datetime.timedelta(days=round(delays.get(inv.client_id, org_delay))))
        if expected < today:
            backlog["amount"] += amt; backlog["count"] += 1
            continue
        idx = (expected - monday).days // 7
        target = buckets[idx] if idx < weeks else later
        target["amount"] += amt; target["count"] += 1

    for b in (*buckets, backlog, later, held):
        b["amount"] = round(b["amount"], 2)
    total = round(sum(b["amount"] for b in (*buckets, backlog, later)), 2)
    return {"weeks": buckets, "past_expected": backlog, "later": later, "disputed": held,
            "expected_total": total, "avg_delay_days": round(org_delay, 1)}
