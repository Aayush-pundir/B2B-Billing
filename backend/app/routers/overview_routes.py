"""Lightweight endpoints that keep the UI fast: dashboard totals and global quick-search."""
import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (AccountEnterprise, BillingAccount, BillingException, BillingRun, Client, Commercial, Contract, ContractComponent,
                        Invoice, ReviewItem, User, Vendor)
from app.services import billing_v2, review
from app.services.auth import require_user

OPEN = ("issued", "sent", "partially_paid", "overdue", "disputed", "pushed")


def _prev_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _periods_back(period: str, n: int) -> list[str]:
    out = [period]
    for _ in range(n - 1):
        out.append(_prev_period(out[-1]))
    return out[::-1]

router = APIRouter(prefix="/api")


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """The numbers on the home page, computed in the database instead of shipping every row to the browser."""
    today = datetime.date.today()
    return dashboard_payload(db, today, _prev_period(today.strftime("%Y-%m")))


def dashboard_payload(db: Session, today: datetime.date, period: str) -> dict:
    """period is the month being closed. KPIs and the chart end at the latest month that has actually been issued,
    so a month still in review (draft invoices) does not show as ₹0."""
    kpi_period = db.query(func.max(Invoice.period)).filter(Invoice.status.notin_(("draft", "cancelled")), Invoice.period <= period).scalar() or period
    inv_count, revenue = db.query(func.count(Invoice.id), func.coalesce(func.sum(Invoice.total), 0.0)).one()
    counts = dict(db.query(Invoice.run_id, func.count(Invoice.id)).group_by(Invoice.run_id).all())
    runs = db.query(BillingRun).order_by(BillingRun.id.desc())
    return {
        "period": period,
        "kpi_period": kpi_period,
        "close": _close_status(db, period),
        "kpis": _kpis(db, kpi_period, today),
        "trend": _trend(db, kpi_period),
        "attention": _attention(db, period, today),
        "clients": db.query(func.count(Client.id)).scalar(),
        "locked_commercials": db.query(func.count(Commercial.id)).filter(Commercial.status == "locked").scalar(),
        "runs": runs.count(),
        "invoices": inv_count,
        "revenue": revenue,
        "recent_runs": [{"id": r.id, "period": r.period, "status": r.status, "created_at": r.created_at.isoformat(),
                         "invoice_count": counts.get(r.id, 0)} for r in runs.limit(8).all()],
    }


def _close_status(db: Session, period: str) -> dict:
    run = db.query(BillingRun).filter(BillingRun.period == period, BillingRun.status != "rejected").order_by(BillingRun.id.desc()).first()
    if not run:
        return {"period": period, "run": None, "step": "calc", "review": None, "zoho": None}
    rv = review.counts(db, run.id)
    z = None
    if run.status in ("approved", "pushed"):
        live = [i for i in run.invoices if i.status != "cancelled"]
        z = {"pushed": sum(1 for i in live if i.zoho_invoice_id), "total": len(live)}
    if run.status == "pending_approval":
        step = "review" if rv["open"] else "lock"
    elif z and z["total"] and z["pushed"] == z["total"]:
        step = "done"
    else:
        step = "zoho"
    return {"period": period, "run": {"id": run.id, "status": run.status}, "step": step, "review": rv, "zoho": z,
            "invoices": len(run.invoices), "total": (run.summary or {}).get("total", 0), "withheld": len((run.summary or {}).get("skipped", []))}


def _taxable(db: Session, period: str) -> float:
    return float(db.query(func.coalesce(func.sum(Invoice.subtotal), 0.0)).filter(Invoice.period == period, Invoice.status.notin_(("draft", "cancelled"))).scalar() or 0.0)


def _trend(db: Session, period: str) -> list[dict]:
    rows = dict(db.query(Invoice.period, func.sum(Invoice.subtotal)).filter(Invoice.status.notin_(("draft", "cancelled")))
                .group_by(Invoice.period).all())
    return [{"period": p, "taxable": round(float(rows.get(p) or 0), 2)} for p in _periods_back(period, 6)]


def _overdue_query(db: Session, today: datetime.date):
    """Open, undisputed invoices past their due date (disputes pause the aging clock)."""
    return db.query(Invoice).filter(Invoice.status.in_(OPEN), or_(Invoice.disputed.is_(False), Invoice.disputed.is_(None)),
                                    Invoice.due_date < today.isoformat(), Invoice.balance_due > 0)


def _kpis(db: Session, period: str, today: datetime.date) -> dict:
    billed, before = _taxable(db, period), _taxable(db, _prev_period(period))
    outstanding = db.query(func.coalesce(func.sum(Invoice.balance_due), 0.0)).filter(Invoice.status.in_(OPEN)).scalar()
    od = _overdue_query(db, today).with_entities(Invoice.balance_due).all()
    return {"billed": round(billed, 2), "prev_billed": round(before, 2),
            "change_pct": round((billed - before) / before * 100, 1) if before else None,
            "outstanding": round(float(outstanding or 0), 2), "overdue": round(sum(x[0] or 0 for x in od), 2), "overdue_count": len(od),
            "accounts": db.query(func.count(BillingAccount.id)).scalar()}


def _attention(db: Session, period: str, today: datetime.date) -> list[dict]:
    out: list[dict] = []
    close = _close_status(db, period)
    if close["run"] is None:
        out.append({"level": "warn", "text": f"{period} has not been calculated yet", "page": "close", "cta": "Start the close"})
    elif close["step"] == "review":
        out.append({"level": "warn", "text": f"{close['review']['open']} invoice(s) in {period} wait for a second check", "page": "close", "cta": "Review"})
    elif close["step"] == "lock":
        out.append({"level": "warn", "text": f"{period} is reviewed and ready to lock", "page": "close", "cta": "Lock"})
    elif close["step"] == "zoho":
        out.append({"level": "warn", "text": f"{period} is locked but not yet in Zoho", "page": "close", "cta": "Push"})
    if close["withheld"] if close["run"] else 0:
        out.append({"level": "high", "text": f"{close['withheld']} account(s) were withheld from {period} and need fixing", "page": "close", "cta": "See why"})
    high = db.query(func.count(BillingException.id)).filter(BillingException.status == "open", BillingException.severity == "high").scalar()
    if high:
        out.append({"level": "high", "text": f"{high} high-severity billing finding(s) are open", "page": "accounts", "cta": "Open"})
    unmapped = db.query(func.count(func.distinct(AccountEnterprise.account_id))).filter(
        or_(AccountEnterprise.enterprise_id.like("ROW-%"), AccountEnterprise.enterprise_id.like("UNMAPPED%"))).scalar()
    if unmapped:
        out.append({"level": "warn", "text": f"{unmapped} account(s) have no platform enterprise ID yet", "page": "accounts", "cta": "Map"})
    billable = billing_v2.billable_accounts(db, period)  # dropped, paused and not-yet-live accounts cannot bill, so they are not "to model"
    unmodelled = sum(1 for a in billable for k in (billing_v2.active_contract(a, period).components) if k.component_type == "manual_formula")
    no_state = [a for a in billable if not (a.client.state_code or "").strip() and (a.client.gst_treatment or "") != "overseas"]
    if no_state:
        out.append({"level": "high", "text": f"{len(no_state)} billable customer(s) have no GST state on file and will be withheld", "page": "clients", "cta": "Pull from Zoho"})
    pending = sum(len(billing_v2.open_assumptions(billing_v2.active_contract(a, period))) for a in billable)
    if pending:
        out.append({"level": "info", "text": f"{pending} interpreted contract term(s) wait for Finance to confirm", "page": "accounts", "cta": "Confirm"})
    if unmodelled:
        out.append({"level": "warn", "text": f"{unmodelled} charge(s) are free text and bill ₹0 until modelled", "page": "accounts", "cta": "Model"})
    drafts = db.query(func.count(Contract.id)).filter(Contract.status == "draft").scalar()
    if drafts:
        out.append({"level": "info", "text": f"{drafts} contract draft(s) wait for Finance to lock", "page": "accounts", "cta": "Open"})
    od = _overdue_query(db, today).with_entities(func.count(Invoice.id), func.coalesce(func.sum(Invoice.balance_due), 0.0)).one()
    if od[0]:
        out.append({"level": "warn", "text": f"{od[0]} invoice(s) are overdue (₹{od[1]:,.0f})", "page": "receivables", "cta": "Chase"})
    return out


@router.get("/search")
def search(q: str = "", limit: int = 6, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Quick-jump search across customers, billing accounts, invoices and vendors."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"customers": [], "accounts": [], "invoices": [], "vendors": []}
    like, limit = f"%{q}%", max(1, min(limit, 20))
    customers = db.query(Client).filter(or_(Client.name.ilike(like), Client.enterprise_id.ilike(like), Client.gst.ilike(like))).order_by(Client.name).limit(limit).all()
    accounts = db.query(BillingAccount).filter(or_(BillingAccount.display_name.ilike(like), BillingAccount.kam.ilike(like))).order_by(BillingAccount.display_name).limit(limit).all()
    invoices = (db.query(Invoice, Client.name).outerjoin(Client, Client.id == Invoice.client_id)
                .filter(or_(Invoice.invoice_no.ilike(like), Client.name.ilike(like))).order_by(Invoice.id.desc()).limit(limit).all())
    vendors = db.query(Vendor).filter(Vendor.name.ilike(like)).order_by(Vendor.name).limit(limit).all()
    return {
        "customers": [{"id": c.id, "name": c.name, "sub": c.enterprise_id} for c in customers],
        "accounts": [{"id": a.id, "name": a.display_name, "sub": a.kam or ""} for a in accounts],
        "invoices": [{"id": i.id, "name": i.invoice_no, "sub": f"{name or ''} · {i.period} · ₹{i.total:,.0f} · {i.status}"} for i, name in invoices],
        "vendors": [{"id": v.id, "name": v.name, "sub": ""} for v in vendors],
    }
