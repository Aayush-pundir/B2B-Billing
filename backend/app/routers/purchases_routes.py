import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Bill, BillPayment, User, Vendor
from app.routers.core_routes import log_action
from app.services import ledger, sales, settings_store
from app.services.auth import FINANCE_READERS, require_roles, require_write

router = APIRouter(prefix="/api/purchases")
READ = require_roles(*FINANCE_READERS)


def assert_open(db: Session, date: str):
    lock = settings_store.get_lock_date(db)
    if lock and date <= lock:
        raise HTTPException(400, f"Books are locked up to {lock} — pick a later date or ask Finance to move the lock date")


def _date(d: str, name: str) -> str:
    try:
        return datetime.date.fromisoformat(d).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be a date (YYYY-MM-DD)")


# ----------------------------- vendors -----------------------------

class VendorIn(BaseModel):
    name: str
    gst: str = ""
    pan: str = ""
    state: str = ""
    state_code: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    payment_terms_days: int = 30
    tds_section: str = ""
    bank_account: str = ""
    ifsc: str = ""
    status: str = "active"
    notes: str = ""


@router.get("/vendors")
def list_vendors(db: Session = Depends(get_db), user: User = Depends(READ)):
    out = []
    for v in db.query(Vendor).order_by(Vendor.name).all():
        owed = sum(b.balance_due or 0 for b in db.query(Bill).filter(Bill.vendor_id == v.id, Bill.status.in_(("open", "partially_paid"))))
        out.append({f: getattr(v, f) for f in VendorIn.model_fields} | {"id": v.id, "outstanding": round(owed, 2)})
    return out


@router.post("/vendors")
def create_vendor(p: VendorIn, db: Session = Depends(get_db), user: User = Depends(require_write("purchases"))):
    if not p.name.strip():
        raise HTTPException(400, "Vendor name is required")
    if db.query(Vendor).filter(Vendor.name == p.name.strip()).first():
        raise HTTPException(400, "A vendor with this name already exists")
    v = Vendor(**p.model_dump() | {"name": p.name.strip()})
    db.add(v)
    db.commit()
    log_action(db, user, "vendor.create", "vendor", v.name)
    return {"id": v.id}


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, p: VendorIn, db: Session = Depends(get_db), user: User = Depends(require_write("purchases"))):
    v = db.query(Vendor).get(vendor_id)
    if not v:
        raise HTTPException(404, "Vendor not found")
    for k, val in p.model_dump().items():
        setattr(v, k, val)
    db.commit()
    return {"ok": True}


# ----------------------------- bills -----------------------------

class BillLineIn(BaseModel):
    description: str = ""
    account_code: str
    amount: float
    gst_rate: float = 0.0
    hsn_sac: str = ""


class BillIn(BaseModel):
    vendor_id: int
    bill_no: str
    date: str
    due_date: str | None = None
    lines: list[BillLineIn]
    itc_eligible: bool = True
    tds_rate: float = 0.0
    notes: str = ""


def _bill_out(b: Bill, vname: str) -> dict:
    return {"id": b.id, "vendor_id": b.vendor_id, "vendor": vname, "bill_no": b.bill_no, "date": b.date, "due_date": b.due_date,
            "lines": b.lines, "subtotal": b.subtotal, "gst_type": b.gst_type, "gst": b.gst, "itc_eligible": b.itc_eligible, "tds": b.tds,
            "total": b.total, "paid_amount": b.paid_amount, "balance_due": b.balance_due, "status": b.status, "notes": b.notes,
            "payments": [{"id": p.id, "date": p.date, "amount": p.amount, "mode": p.mode, "reference": p.reference} for p in b.payments]}


@router.get("/bills")
def list_bills(db: Session = Depends(get_db), user: User = Depends(READ)):
    names = {v.id: v.name for v in db.query(Vendor).all()}
    return [_bill_out(b, names.get(b.vendor_id, "?")) for b in db.query(Bill).order_by(Bill.date.desc(), Bill.id.desc()).all()]


@router.post("/bills")
def create_bill(p: BillIn, db: Session = Depends(get_db), user: User = Depends(require_write("purchases"))):
    vendor = db.query(Vendor).get(p.vendor_id)
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    if not p.bill_no.strip():
        raise HTTPException(400, "Enter the vendor's bill number")
    if db.query(Bill).filter(Bill.vendor_id == vendor.id, Bill.bill_no == p.bill_no.strip(), Bill.status != "void").first():
        raise HTTPException(400, "This vendor bill number is already recorded")
    date = _date(p.date, "Bill date")
    assert_open(db, date)
    if not p.lines:
        raise HTTPException(400, "Add at least one line")
    accts = {a.code: a for a in db.query(Account).all()}
    by_rate, lines = {}, []
    for n, l in enumerate(p.lines, start=1):
        a = accts.get(l.account_code)
        if not a or a.type not in ("expense", "asset") or not a.active:
            raise HTTPException(400, f"Line {n}: {l.account_code} must be an active expense or asset account")
        if l.amount <= 0 or not 0 <= l.gst_rate <= 40:
            raise HTTPException(400, f"Line {n}: amount must be positive and GST % between 0 and 40")
        lines.append(l.model_dump())
        by_rate[l.gst_rate] = by_rate.get(l.gst_rate, 0.0) + l.amount
    org = settings_store.get_all_settings(db)
    gst_type, gst = sales.gst_for(by_rate, vendor.state_code, org["ORG_STATE_CODE"])
    subtotal = round(sum(l["amount"] for l in lines), 2)
    tds = round(subtotal * p.tds_rate / 100, 2) if p.tds_rate else 0.0
    total = round(subtotal + gst - tds, 2)
    due = _date(p.due_date, "Due date") if p.due_date else (datetime.date.fromisoformat(date) + datetime.timedelta(days=vendor.payment_terms_days or 30)).isoformat()
    b = Bill(vendor_id=vendor.id, bill_no=p.bill_no.strip(), date=date, due_date=due, lines=lines, subtotal=subtotal, gst_type=gst_type, gst=gst,
             itc_eligible=p.itc_eligible, tds=tds, total=total, balance_due=total, notes=p.notes, created_by=user.email)
    db.add(b)
    db.commit()
    log_action(db, user, "bill.create", "bill", f"{vendor.name} {b.bill_no} ₹{total:,.2f}")
    return {"id": b.id, "total": total, "gst": gst, "tds": tds}


@router.post("/bills/{bill_id}/void")
def void_bill(bill_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("purchases"))):
    b = db.query(Bill).get(bill_id)
    if not b:
        raise HTTPException(404, "Bill not found")
    if b.payments:
        raise HTTPException(400, "This bill has payments — they must be removed first")
    assert_open(db, b.date)
    b.status, b.balance_due = "void", 0.0
    db.commit()
    log_action(db, user, "bill.void", "bill", b.bill_no)
    return {"ok": True}


class BillPayIn(BaseModel):
    amount: float
    date: str
    mode: str = "NEFT"
    reference: str = ""


@router.post("/bills/{bill_id}/pay")
def pay_bill(bill_id: int, p: BillPayIn, db: Session = Depends(get_db), user: User = Depends(require_write("purchases"))):
    b = db.query(Bill).get(bill_id)
    if not b or b.status == "void":
        raise HTTPException(404, "Bill not found")
    date = _date(p.date, "Payment date")
    assert_open(db, date)
    if p.amount <= 0 or p.amount > b.balance_due + 0.005:
        raise HTTPException(400, f"Amount must be between 0 and the balance of ₹{b.balance_due:,.2f}")
    db.add(BillPayment(bill_id=b.id, date=date, amount=p.amount, mode=p.mode, reference=p.reference, created_by=user.email))
    b.paid_amount = round(b.paid_amount + p.amount, 2)
    b.balance_due = round(b.total - b.paid_amount, 2)
    b.status = "paid" if b.balance_due <= 0.005 else "partially_paid"
    if b.status == "paid":
        b.balance_due = 0.0
    db.commit()
    log_action(db, user, "bill.pay", "bill", f"{b.bill_no} ₹{p.amount:,.2f}")
    return {"ok": True, "status": b.status, "balance_due": b.balance_due}
