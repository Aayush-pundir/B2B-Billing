"""Push a locked run to Zoho Books as a background job, so 700+ invoices never depend on one web request staying open.
Progress lives on the invoices themselves (zoho_invoice_id / zoho_error), so a stopped or partly failed push simply resumes."""
from __future__ import annotations

import threading
import time

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BillingAccount, BillingRun, Client, Invoice
from app.services import zoho

_running: dict[int, threading.Thread] = {}
_lock = threading.Lock()
PAUSE_SECONDS = 0.35  # stays well inside Zoho's per-minute API allowance


def _pushable(run: BillingRun) -> list[Invoice]:
    return [i for i in run.invoices if i.status != "cancelled" and not i.zoho_invoice_id]


def push_one(db: Session, run: BillingRun, inv: Invoice) -> None:
    """Create (or find) the customer, then the draft invoice. Raises on failure; the caller records the message."""
    client = db.query(Client).get(inv.client_id)
    account = db.query(BillingAccount).filter(BillingAccount.client_id == client.id).first()
    if not client.zoho_contact_id:
        name = account.zoho_billing_name if account and account.zoho_billing_name else client.name
        client.zoho_contact_id = zoho.create_contact(name, client.gst)["contact_id"]
    notes = f"Platform Fee {run.period}"
    if account is not None and not account.auto_debit:
        notes += " — DO NOT AUTO DEBIT: collect manually."
    if any(l.get("component_type") for l in inv.lines or []):  # v2: one Zoho line per charge
        lines = [{"label": l["label"], "detail": l.get("calc_detail") or l.get("detail", ""), "amount": l["amount"]} for l in inv.lines if l.get("amount")]
        result = zoho.push_invoice_lines(client.zoho_contact_id, inv.invoice_no, (inv.issued_at or inv.created_at).date().isoformat(), inv.due_date, lines, notes)
    else:
        breakdown = [f"{l['label']}: ₹{l['amount']:,.2f} ({l['calc_detail']})" for l in inv.lines]
        result = zoho.push_draft_invoice(client.zoho_contact_id, f"Invoice for the month of {run.period}", inv.total,
                                         "\n".join(breakdown) + f"\n{inv.gst_type}: ₹{inv.gst:,.2f} · TDS: ₹{inv.tds:,.2f} · Total: ₹{inv.total:,.2f}")
    inv.zoho_invoice_id = result["invoice_id"]  # status is left alone: the invoice stays issued/sent/paid natively
    inv.zoho_error = ""


def _work(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.query(BillingRun).get(run_id)
        live = zoho.configured()
        for inv in _pushable(run):
            try:
                push_one(db, run, inv)
            except Exception as exc:
                db.rollback()
                inv = db.query(Invoice).get(inv.id)
                inv.zoho_error = str(exc)[:300]
            db.commit()
            if live:
                time.sleep(PAUSE_SECONDS)
        run = db.query(BillingRun).get(run_id)
        if not _pushable(run) and run.status == "approved":
            run.status = "pushed"
            db.commit()
    finally:
        db.close()
        with _lock:
            _running.pop(run_id, None)


def start(run_id: int) -> bool:
    """Begin pushing; returns False when a push for this run is already under way."""
    with _lock:
        if run_id in _running and _running[run_id].is_alive():
            return False
        t = threading.Thread(target=_work, args=(run_id,), name=f"zoho-push-{run_id}", daemon=True)
        _running[run_id] = t
        t.start()
        return True


def wait(run_id: int, timeout: float = 60) -> None:
    t = _running.get(run_id)
    if t:
        t.join(timeout)


def status(db: Session, run_id: int) -> dict:
    run = db.query(BillingRun).get(run_id)
    invoices = [i for i in run.invoices if i.status != "cancelled"]
    pushed = sum(1 for i in invoices if i.zoho_invoice_id)
    errors = [{"invoice_id": i.id, "invoice_no": i.invoice_no, "reason": i.zoho_error} for i in invoices if i.zoho_error and not i.zoho_invoice_id]
    with _lock:
        running = run_id in _running and _running[run_id].is_alive()
    return {"total": len(invoices), "pushed": pushed, "failed": len(errors), "errors": errors[:50], "running": running,
            "simulated": not zoho.configured(), "run_status": run.status}
