"""Lightweight endpoints that keep the UI fast: dashboard totals and global quick-search."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BillingAccount, BillingRun, Client, Commercial, Invoice, User, Vendor
from app.services.auth import require_user

router = APIRouter(prefix="/api")


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """The numbers on the home page, computed in the database instead of shipping every row to the browser."""
    inv_count, revenue = db.query(func.count(Invoice.id), func.coalesce(func.sum(Invoice.total), 0.0)).one()
    counts = dict(db.query(Invoice.run_id, func.count(Invoice.id)).group_by(Invoice.run_id).all())
    runs = db.query(BillingRun).order_by(BillingRun.id.desc())
    return {
        "clients": db.query(func.count(Client.id)).scalar(),
        "locked_commercials": db.query(func.count(Commercial.id)).filter(Commercial.status == "locked").scalar(),
        "runs": runs.count(),
        "invoices": inv_count,
        "revenue": revenue,
        "recent_runs": [{"id": r.id, "period": r.period, "status": r.status, "created_at": r.created_at.isoformat(),
                         "invoice_count": counts.get(r.id, 0)} for r in runs.limit(8).all()],
    }


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
