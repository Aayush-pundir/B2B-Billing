import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BankStatementImport, Client, Invoice, Payment, User
from app.services import reconciliation as recon
from app.services.auth import require_user, require_write
from app.routers.core_routes import log_action

router = APIRouter(prefix="/api/billing")


@router.post("/reconciliation/upload-statement")
async def upload_statement(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    raw = await file.read()
    rows = recon.parse_bank_statement(raw)
    if not rows:
        raise HTTPException(400, "No credit rows found — check the statement has a recognisable date/narration/credit column")

    imp = BankStatementImport(
        file_name=file.filename, uploaded_by=user.email, rows_parsed=len(rows),
        date_from=min((r.date for r in rows if r.date), default=None),
        date_to=max((r.date for r in rows if r.date), default=None),
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)

    result = recon.reconcile_credit_rows(db, rows, source="bank_statement", import_id=imp.id, created_by=user.email)
    imp.rows_matched = result["auto_matched"]
    db.commit()

    log_action(db, user, "reconciliation.upload_statement",
               "bank_statement_import", f"{imp.id} rows={len(rows)} auto={result['auto_matched']}")
    return {"import_id": imp.id, **result}


@router.post("/reconciliation/manual-upload")
async def manual_upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    raw = await file.read()
    rows = recon.parse_manual_collections(raw)
    if not rows:
        raise HTTPException(400, "No rows found — expected columns: enterprise_id, amount, date, mode, reference")
    result = recon.reconcile_manual_rows(db, rows, created_by=user.email)
    log_action(db, user, "reconciliation.manual_upload", "payment", f"rows={len(rows)} auto={result['auto_matched']}")
    return result


@router.get("/reconciliation/queue")
def review_queue(db: Session = Depends(get_db), user: User = Depends(require_user)):
    payments = db.query(Payment).filter(Payment.invoice_id.is_(None)).order_by(Payment.created_at.desc()).all()
    out = []
    for p in payments:
        client = db.query(Client).get(p.client_id) if p.client_id else None
        candidate_invoices = []
        if p.client_id:
            open_inv = (
                db.query(Invoice)
                .filter(Invoice.client_id == p.client_id, Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]))
                .all()
            )
            candidate_invoices = [{"id": i.id, "invoice_no": i.invoice_no, "balance_due": i.balance_due} for i in open_inv]
        out.append({
            "id": p.id, "date": p.date, "amount": p.amount, "narration": p.narration, "reference": p.reference,
            "match_confidence": p.match_confidence, "source": p.source,
            "client_name": client.name if client else None,
            "candidate_invoices": candidate_invoices,
        })
    return out


class ConfirmMatchIn(BaseModel):
    invoice_id: int
    amount: float | None = None  # defaults to full payment amount; lower value = partial allocation


@router.post("/reconciliation/payments/{payment_id}/confirm")
def confirm_match(payment_id: int, payload: ConfirmMatchIn, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    payment = db.query(Payment).get(payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")
    invoice = db.query(Invoice).get(payload.invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    allocate = min(payload.amount or payment.amount, payment.amount, invoice.balance_due if invoice.balance_due > 0 else payment.amount)
    recon.apply_payment_to_invoice(invoice, allocate)

    remainder = round(payment.amount - allocate, 2)
    payment.amount = allocate
    payment.invoice_id = invoice.id
    payment.client_id = invoice.client_id
    payment.match_confidence = "manual"
    payment.matched_by = "manual"

    if remainder > 0.5:
        residual = Payment(
            date=payment.date, amount=remainder, narration=payment.narration, reference=payment.reference,
            source=payment.source, import_id=payment.import_id, created_by=user.email,
            client_id=payment.client_id, match_confidence="partial", matched_by="",
        )
        db.add(residual)

    db.commit()
    log_action(db, user, "reconciliation.confirm", "payment", f"{payment_id} -> invoice {invoice.invoice_no} amount={allocate}")
    return {"ok": True, "allocated": allocate, "invoice_status": invoice.status, "remainder_queued": remainder > 0.5}


@router.post("/reconciliation/payments/{payment_id}/dismiss")
def dismiss_unmatched(payment_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    """Mark an unidentified receipt as reviewed-and-ignored (advance, refund, unrelated income)."""
    payment = db.query(Payment).get(payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")
    payment.match_confidence = "dismissed"
    payment.matched_by = "manual"
    db.commit()
    log_action(db, user, "reconciliation.dismiss", "payment", str(payment_id))
    return {"ok": True}


@router.get("/receivables/aging")
def aging_report(db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = datetime.date.today()
    invoices = db.query(Invoice).filter(Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"])).all()
    buckets = {"current": [], "0_30": [], "31_60": [], "61_90": [], "90_plus": []}
    for inv in invoices:
        client = db.query(Client).get(inv.client_id)
        row = {
            "invoice_no": inv.invoice_no, "client_name": client.name if client else "?",
            "due_date": inv.due_date, "balance_due": inv.balance_due, "disputed": inv.disputed,
        }
        if not inv.due_date:
            buckets["current"].append(row)
            continue
        try:
            due = datetime.date.fromisoformat(inv.due_date)
        except ValueError:
            buckets["current"].append(row)
            continue
        days_overdue = (today - due).days
        row["days_overdue"] = days_overdue
        if days_overdue <= 0:
            buckets["current"].append(row)
        elif days_overdue <= 30:
            buckets["0_30"].append(row)
        elif days_overdue <= 60:
            buckets["31_60"].append(row)
        elif days_overdue <= 90:
            buckets["61_90"].append(row)
        else:
            buckets["90_plus"].append(row)

        if days_overdue > 0 and inv.status not in ("overdue", "disputed"):
            inv.status = "overdue"
    db.commit()

    totals = {k: round(sum(r["balance_due"] for r in v), 2) for k, v in buckets.items()}
    return {"buckets": buckets, "totals": totals, "total_outstanding": round(sum(totals.values()), 2)}
