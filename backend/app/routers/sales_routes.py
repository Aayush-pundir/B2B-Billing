import datetime
import io
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, CreditNote, Invoice, Payment, Quote, RecurringInvoice, User
from app.routers.core_routes import log_action
from app.services import invoicing, sales, settings_store
from app.services.auth import FINANCE_READERS, require_roles, require_user, require_write

router = APIRouter(prefix="/api/sales")


class LineIn(BaseModel):
    item_id: int | None = None
    description: str = ""
    qty: float = 1
    rate: float | None = None
    gst_rate: float | None = None
    hsn_sac: str = ""


def _lines(lines: list[LineIn]) -> list[dict]:
    return [l.model_dump() for l in lines]


def _client(db: Session, client_id: int) -> Client:
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    return c


def _valid_date(d: str, name: str) -> str:
    try:
        return datetime.date.fromisoformat(d).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be a date (YYYY-MM-DD)")


# ----------------------------- price preview -----------------------------

class PreviewIn(BaseModel):
    client_id: int
    lines: list[LineIn]


@router.post("/preview")
def preview(p: PreviewIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return sales.price(db, _client(db, p.client_id).state_code, _lines(p.lines))


# ----------------------------- ad-hoc invoices & debit notes -----------------------------

class DocIn(BaseModel):
    client_id: int
    lines: list[LineIn]
    date: str | None = None
    terms_days: int | None = None
    po_number: str = ""
    issue: bool = False
    parent_invoice_id: int | None = None  # debit notes may reference the invoice they adjust


def _create(db: Session, user: User, p: DocIn, doc_type: str):
    client = _client(db, p.client_id)
    parent = db.query(Invoice).get(p.parent_invoice_id) if p.parent_invoice_id else None
    if p.parent_invoice_id and (not parent or parent.client_id != client.id):
        raise HTTPException(400, "The referenced invoice does not belong to this customer")
    inv = sales.create_invoice(db, client, _lines(p.lines), email=user.email, source="debit_note" if doc_type == "debit_note" else "adhoc",
                               doc_type=doc_type, on=_valid_date(p.date, "Date") if p.date else None, terms_days=p.terms_days,
                               parent=parent, po_number=p.po_number, issue=p.issue)
    if p.issue:
        invoicing.send(db, inv, client)
    db.commit()
    log_action(db, user, f"{doc_type}.create", "invoice", f"{inv.invoice_no} total={inv.total}")
    return {"id": inv.id, "invoice_no": inv.invoice_no, "total": inv.total, "status": inv.status}


@router.post("/invoices")
def create_adhoc_invoice(p: DocIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    return _create(db, user, p, "invoice")


@router.post("/debit-notes")
def create_debit_note(p: DocIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    return _create(db, user, p, "debit_note")


@router.get("/documents")
def list_documents(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Ad-hoc, recurring and debit-note documents (usage-billing invoices live on the Invoices page)."""
    clients = {c.id: c.name for c in db.query(Client).all()}
    rows = db.query(Invoice).filter(Invoice.source != "usage").order_by(Invoice.id.desc()).all()
    return [{"id": i.id, "invoice_no": i.invoice_no, "doc_type": i.doc_type, "source": i.source, "client": clients.get(i.client_id, "?"),
             "period": i.period, "total": i.total, "balance_due": i.balance_due, "status": i.status, "due_date": i.due_date,
             "parent_invoice_id": i.parent_invoice_id} for i in rows]


# ----------------------------- write-off -----------------------------

class WriteOffIn(BaseModel):
    reason: str = ""


@router.post("/invoices/{invoice_id}/write-off")
def write_off(invoice_id: int, p: WriteOffIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    inv = db.query(Invoice).get(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status in ("draft", "cancelled", "paid", "written_off") or (inv.balance_due or 0) <= 0.01:
        raise HTTPException(400, "Nothing to write off on this invoice")
    amount = inv.balance_due
    db.add(CreditNote(invoice_id=inv.id, amount=amount, reason=f"Write-off: {p.reason}".strip(), created_by=user.email))
    inv.balance_due, inv.status = 0.0, "written_off"
    db.commit()
    log_action(db, user, "invoice.write_off", "invoice", f"{inv.invoice_no} ₹{amount:,.2f} {p.reason}")
    return {"ok": True, "written_off": amount}


# ----------------------------- receipts / advances -----------------------------

class ReceiptIn(BaseModel):
    client_id: int
    amount: float
    date: str
    mode: str = "NEFT"
    reference: str = ""
    invoice_id: int | None = None


@router.post("/receipts")
def record_receipt(p: ReceiptIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    """Record money received. With an invoice it settles it; without one it is an advance held as a customer credit."""
    from app.services.reconciliation import apply_payment_to_invoice
    client = _client(db, p.client_id)
    if p.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    date = _valid_date(p.date, "Date")
    pay = Payment(date=date, amount=p.amount, mode=p.mode, reference=p.reference, client_id=client.id, source="manual_entry",
                  created_by=user.email, match_confidence="manual" if p.invoice_id else "none", matched_by="manual")
    if p.invoice_id:
        inv = db.query(Invoice).get(p.invoice_id)
        if not inv or inv.client_id != client.id:
            raise HTTPException(400, "Invoice not found for this customer")
        if inv.status in ("draft", "cancelled"):
            raise HTTPException(400, "That invoice is not open")
        alloc = min(p.amount, inv.balance_due)
        apply_payment_to_invoice(inv, alloc)
        pay.invoice_id, pay.amount = inv.id, alloc
        db.add(pay)
        if p.amount - alloc > 0.5:  # excess becomes an advance
            db.add(Payment(date=date, amount=round(p.amount - alloc, 2), mode=p.mode, reference=p.reference, client_id=client.id,
                           source="manual_entry", created_by=user.email, match_confidence="none"))
    else:
        db.add(pay)
    db.commit()
    log_action(db, user, "receipt.record", "payment", f"{client.enterprise_id} ₹{p.amount:,.2f} {p.reference}")
    return {"ok": True}


@router.get("/advances")
def list_advances(db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_READERS))):
    """Unallocated customer receipts, with the customer's open invoices they could be applied to."""
    out = []
    for p in db.query(Payment).filter(Payment.invoice_id.is_(None), Payment.client_id.isnot(None),
                                      Payment.match_confidence != "dismissed").all():
        c = db.query(Client).get(p.client_id)
        open_inv = db.query(Invoice).filter(Invoice.client_id == p.client_id, Invoice.status.in_(("issued", "sent", "partially_paid", "overdue"))).all()
        out.append({"id": p.id, "date": p.date, "amount": p.amount, "reference": p.reference, "customer": c.name if c else "?",
                    "open_invoices": [{"id": i.id, "invoice_no": i.invoice_no, "balance_due": i.balance_due} for i in open_inv]})
    return out


# ----------------------------- quotes -----------------------------

class QuoteIn(BaseModel):
    client_id: int
    lines: list[LineIn]
    date: str | None = None
    valid_days: int = 30
    notes: str = ""


def _quote_out(q: Quote, cname: str) -> dict:
    return {"id": q.id, "quote_no": q.quote_no, "client_id": q.client_id, "customer": cname, "date": q.date, "valid_until": q.valid_until,
            "lines": q.lines, "subtotal": q.subtotal, "gst_type": q.gst_type, "gst": q.gst, "total": q.total, "status": q.status,
            "notes": q.notes, "invoice_id": q.invoice_id}


@router.get("/quotes")
def list_quotes(db: Session = Depends(get_db), user: User = Depends(require_user)):
    clients = {c.id: c.name for c in db.query(Client).all()}
    today = datetime.date.today().isoformat()
    out = []
    for q in db.query(Quote).order_by(Quote.id.desc()).all():
        o = _quote_out(q, clients.get(q.client_id, "?"))
        if q.status in ("draft", "sent") and q.valid_until and q.valid_until < today:
            o["status"] = "expired"
        out.append(o)
    return out


@router.post("/quotes")
def create_quote(p: QuoteIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    client = _client(db, p.client_id)
    priced = sales.price(db, client.state_code, _lines(p.lines))
    date = _valid_date(p.date, "Date") if p.date else datetime.date.today().isoformat()
    q = Quote(quote_no=sales.next_quote_no(db), client_id=client.id, date=date,
              valid_until=(datetime.date.fromisoformat(date) + datetime.timedelta(days=p.valid_days)).isoformat(),
              lines=priced["lines"], subtotal=priced["subtotal"], gst_type=priced["gst_type"], gst=priced["gst"],
              total=priced["total"], notes=p.notes, created_by=user.email)
    db.add(q)
    db.commit()
    log_action(db, user, "quote.create", "quote", f"{q.quote_no} total={q.total}")
    return {"id": q.id, "quote_no": q.quote_no, "total": q.total}


class QuoteStatusIn(BaseModel):
    status: str


@router.put("/quotes/{quote_id}/status")
def set_quote_status(quote_id: int, p: QuoteStatusIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    q = db.query(Quote).get(quote_id)
    if not q:
        raise HTTPException(404, "Quote not found")
    if p.status not in ("draft", "sent", "accepted", "declined"):
        raise HTTPException(400, "Invalid status")
    if q.status == "invoiced":
        raise HTTPException(400, "Already converted to an invoice")
    q.status = p.status
    db.commit()
    return {"ok": True}


@router.post("/quotes/{quote_id}/convert")
def convert_quote(quote_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    q = db.query(Quote).get(quote_id)
    if not q:
        raise HTTPException(404, "Quote not found")
    if q.status == "invoiced":
        raise HTTPException(400, "Already converted")
    if q.status == "declined":
        raise HTTPException(400, "A declined quote cannot be invoiced")
    client = _client(db, q.client_id)
    raw = [{"item_id": l.get("item_id"), "description": l["label"], "qty": l["qty"], "rate": l["amount"] / l["qty"] if l["qty"] else l["amount"],
            "gst_rate": l.get("gst_rate"), "hsn_sac": l.get("hsn_sac", "")} for l in q.lines]
    inv = sales.create_invoice(db, client, raw, email=user.email, source="quote", po_number=q.quote_no)
    q.status, q.invoice_id = "invoiced", inv.id
    db.commit()
    log_action(db, user, "quote.convert", "quote", f"{q.quote_no} -> {inv.invoice_no}")
    return {"ok": True, "invoice_id": inv.id, "invoice_no": inv.invoice_no}


@router.get("/quotes/{quote_id}/pdf")
def quote_pdf(quote_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    from app.services.invoice_pdf import build_invoice_pdf
    q = db.query(Quote).get(quote_id)
    if not q:
        raise HTTPException(404, "Quote not found")
    doc = SimpleNamespace(invoice_no=q.quote_no, period=q.date, due_date=f"valid until {q.valid_until}", lines=q.lines, subtotal=q.subtotal,
                          gst_type=q.gst_type, gst=q.gst, tds=0.0, total=q.total, doc_type="quote")
    pdf = build_invoice_pdf(settings_store.get_all_settings(db), db.query(Client).get(q.client_id), doc)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={q.quote_no}.pdf"})


# ----------------------------- recurring invoices -----------------------------

class RecurringIn(BaseModel):
    client_id: int
    name: str = ""
    lines: list[LineIn]
    frequency: str = "monthly"
    start_date: str
    end_date: str | None = None
    auto_issue: bool = True


def _rec_out(r: RecurringInvoice, cname: str) -> dict:
    return {"id": r.id, "client_id": r.client_id, "customer": cname, "name": r.name, "lines": r.lines, "frequency": r.frequency,
            "start_date": r.start_date, "next_date": r.next_date, "end_date": r.end_date, "auto_issue": r.auto_issue, "status": r.status,
            "generated_count": r.generated_count, "last_generated": r.last_generated}


@router.get("/recurring")
def list_recurring(db: Session = Depends(get_db), user: User = Depends(require_user)):
    clients = {c.id: c.name for c in db.query(Client).all()}
    return [_rec_out(r, clients.get(r.client_id, "?")) for r in db.query(RecurringInvoice).order_by(RecurringInvoice.id.desc()).all()]


@router.post("/recurring")
def create_recurring(p: RecurringIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    client = _client(db, p.client_id)
    if p.frequency not in sales.FREQ_MONTHS:
        raise HTTPException(400, f"Frequency must be one of {list(sales.FREQ_MONTHS)}")
    start = _valid_date(p.start_date, "Start date")
    end = _valid_date(p.end_date, "End date") if p.end_date else None
    if end and end < start:
        raise HTTPException(400, "End date is before the start date")
    priced = sales.price(db, client.state_code, _lines(p.lines))  # validates the lines now, not at 2 a.m. on the 1st
    r = RecurringInvoice(client_id=client.id, name=p.name or f"{client.name} — {p.frequency}", lines=_lines(p.lines), frequency=p.frequency,
                         start_date=start, next_date=start, end_date=end, auto_issue=p.auto_issue, created_by=user.email)
    db.add(r)
    db.commit()
    log_action(db, user, "recurring.create", "recurring", f"{r.name} {p.frequency} ₹{priced['total']:,.2f}")
    return {"id": r.id, "each_invoice_total": priced["total"]}


class RecurringStatusIn(BaseModel):
    status: str


@router.put("/recurring/{rid}/status")
def set_recurring_status(rid: int, p: RecurringStatusIn, db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    r = db.query(RecurringInvoice).get(rid)
    if not r:
        raise HTTPException(404, "Not found")
    if p.status not in ("active", "paused", "ended"):
        raise HTTPException(400, "Invalid status")
    r.status = p.status
    db.commit()
    return {"ok": True}


@router.post("/recurring/run")
def run_recurring_now(db: Session = Depends(get_db), user: User = Depends(require_write("sales"))):
    out = sales.run_recurring(db)
    log_action(db, user, "recurring.run", "recurring", f"generated={len([o for o in out if 'invoice_no' in o])}")
    return {"generated": out}
