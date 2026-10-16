"""Push a locked run to Zoho Books as a background job, so 700+ invoices never depend on one web request staying open.
Progress lives on the invoices themselves (zoho_invoice_id / zoho_error), so a stopped or partly failed push simply resumes."""
from __future__ import annotations

import threading
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import BillingAccount, BillingRun, Client, Invoice
from app.services import gst_states, settings_store, zoho, zoho_sync

_running: dict[int, threading.Thread] = {}
_lock = threading.Lock()


def _pushable(run: BillingRun) -> list[Invoice]:
    return [i for i in run.invoices if i.status != "cancelled" and not i.zoho_invoice_id]


GST_SLABS = (0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0)


def _gst_slab(raw_percent: float) -> float:
    """The GST rate an invoice was charged at, recovered from its rounded amounts: 17.99 means 18, never a tax called 'GST17.99'."""
    return min(GST_SLABS, key=lambda r: abs(r - raw_percent))


def push_one(db: Session, run: BillingRun, inv: Invoice) -> None:
    """Create (or find) the customer, then the draft invoice with GST on every line. Raises on failure; the caller records the message."""
    client = db.query(Client).get(inv.client_id)
    account = db.query(BillingAccount).filter(BillingAccount.client_id == client.id).first()
    state_map = zoho_sync.learned_state_map(db)
    short = gst_states.short_code(client.state_code, state_map)
    if not client.zoho_contact_id:
        name = account.zoho_billing_name if account and account.zoho_billing_name else client.name
        address = {"address": client.address_line1, "street2": client.address_line2, "city": client.city, "state": client.state, "zip": client.pincode, "country": client.country}
        client.zoho_contact_id = zoho.create_contact(name, client.gst, state_short=short, email=client.billing_contact_email, address=address)["contact_id"]
    notes = f"Platform Fee {run.period}"
    if account is not None and not account.auto_debit:
        notes += " — DO NOT AUTO DEBIT: collect manually."
    if any(l.get("component_type") for l in inv.lines or []):  # v2: one Zoho line per charge
        lines = [{"label": l["label"], "detail": l.get("calc_detail") or l.get("detail", ""), "amount": l["amount"]} for l in inv.lines if l.get("amount")]
        rate = _gst_slab(inv.gst / inv.subtotal * 100) if inv.subtotal else 18.0
        result = zoho.push_invoice_lines(client.zoho_contact_id, inv.invoice_no, (inv.issued_at or inv.created_at).date().isoformat(), inv.due_date, lines, notes,
                                         gst_type="IGST" if inv.gst_type == "IGST" else "CGST_SGST", gst_rate=rate, place_of_supply=short, gstin=client.gst,
                                         sac=settings_store.get_all_settings(db).get("DEFAULT_SAC") or "998314")
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
                time.sleep(get_settings().ZOHO_PUSH_PAUSE_SECONDS)  # stays well inside Zoho's per-minute API allowance
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
