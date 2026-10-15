"""Public customer portal API. No login — access is by an unguessable per-invoice
token that is sent in the invoice email. Exposes only what the customer needs:
their invoice, how to pay, a way to promise a date or raise a query."""
import datetime
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Client, Invoice, Payment
from app.services import settings_store

router = APIRouter(prefix="/api/portal")

OPEN = ("issued", "sent", "partially_paid", "overdue", "disputed")


def _inv(db: Session, token: str) -> Invoice:
    if not token or len(token) < 16:
        raise HTTPException(404, "Invoice not found")
    inv = db.query(Invoice).filter(Invoice.public_token == token).first()
    if inv is None or inv.status in ("draft", "cancelled"):
        raise HTTPException(404, "Invoice not found")
    return inv


def _audit(db: Session, action: str, inv: Invoice, detail: str):
    db.add(AuditLog(user="customer-portal", action=action, entity="invoice", detail=f"{inv.invoice_no}: {detail}"))


@router.get("/{token}")
def portal_view(token: str, db: Session = Depends(get_db)):
    inv = _inv(db, token)
    client = db.query(Client).get(inv.client_id)
    org = settings_store.get_all_settings(db)
    inv.last_viewed_at = datetime.datetime.utcnow()
    db.commit()
    payments = db.query(Payment).filter(Payment.invoice_id == inv.id).order_by(Payment.date).all()
    others = (db.query(Invoice).filter(Invoice.client_id == inv.client_id, Invoice.status.in_(OPEN), Invoice.id != inv.id)
              .order_by(Invoice.due_date).all())
    upi = None
    if org.get("UPI_ID") and inv.balance_due > 0:
        from urllib.parse import quote
        upi = (f"upi://pay?pa={quote(org['UPI_ID'])}&pn={quote(org.get('ORG_NAME', ''))}"
               f"&am={inv.balance_due:.2f}&cu=INR&tn={quote('Invoice ' + inv.invoice_no)}")
    return {
        "org": {"name": org.get("ORG_NAME"), "gst": org.get("ORG_GST")},
        "customer": {"name": client.name},
        "invoice": {
            "invoice_no": inv.invoice_no, "period": inv.period, "status": inv.status, "due_date": inv.due_date,
            "lines": inv.lines, "subtotal": inv.subtotal, "gst_type": inv.gst_type, "gst": inv.gst, "tds": inv.tds,
            "total": inv.total, "paid_amount": inv.paid_amount, "balance_due": inv.balance_due,
            "disputed": inv.disputed, "dispute_reason": inv.dispute_reason,
            "promise_date": inv.promise_date, "promise_note": inv.promise_note,
        },
        "payments": [{"date": p.date, "amount": p.amount, "mode": p.mode} for p in payments],
        "other_open": [{"invoice_no": o.invoice_no, "due_date": o.due_date, "balance_due": o.balance_due, "status": o.status} for o in others],
        "pay": {"bank_name": org.get("BANK_NAME"), "account_no": org.get("BANK_ACCOUNT_NO"), "ifsc": org.get("BANK_IFSC"),
                "upi_id": org.get("UPI_ID"), "upi_link": upi, "reference": inv.invoice_no},
    }


class PromiseIn(BaseModel):
    date: str
    note: str = ""


@router.post("/{token}/promise")
def portal_promise(token: str, payload: PromiseIn, db: Session = Depends(get_db)):
    inv = _inv(db, token)
    if inv.balance_due <= 0:
        raise HTTPException(400, "This invoice has no balance due")
    try:
        d = datetime.date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(400, "Enter a valid date")
    today = datetime.date.today()
    if d < today or d > today + datetime.timedelta(days=90):
        raise HTTPException(400, "Choose a date within the next 90 days")
    inv.promise_date, inv.promise_note = d.isoformat(), payload.note.strip()[:300]
    _audit(db, "portal.promise", inv, f"will pay on {inv.promise_date} {inv.promise_note}")
    db.commit()
    return {"ok": True, "promise_date": inv.promise_date}


class QueryIn(BaseModel):
    reason: str


@router.post("/{token}/dispute")
def portal_dispute(token: str, payload: QueryIn, db: Session = Depends(get_db)):
    inv = _inv(db, token)
    if not payload.reason.strip():
        raise HTTPException(400, "Please describe the issue")
    if inv.status == "paid":
        raise HTTPException(400, "This invoice is already paid")
    inv.disputed, inv.dispute_reason, inv.status = True, payload.reason.strip()[:500], "disputed"
    _audit(db, "portal.dispute", inv, inv.dispute_reason)
    db.commit()
    return {"ok": True}


@router.get("/{token}/pdf")
def portal_pdf(token: str, db: Session = Depends(get_db)):
    from app.services.invoice_pdf import build_invoice_pdf
    inv = _inv(db, token)
    client = db.query(Client).get(inv.client_id)
    pdf = build_invoice_pdf(settings_store.get_all_settings(db), client, inv)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f"inline; filename={inv.invoice_no}.pdf"})
