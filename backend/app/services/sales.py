"""Sales documents: build priced lines with per-line GST, create ad-hoc /
recurring / quote-derived invoices and debit notes. They are ordinary Invoice
rows (source/doc_type tell them apart) so aging, reminders, the portal,
reconciliation and GST reporting all work on them unchanged."""
from __future__ import annotations

import datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import BillingRun, Client, Invoice, Item, Quote
from app.services import invoicing, settings_store


def _num(v, name: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} must be a number")


def build_lines(db: Session, raw: list[dict], default_gst: float) -> tuple[list[dict], float, dict[float, float]]:
    """raw line: {item_id?, description?, qty, rate?, gst_rate?, hsn_sac?}. Returns (lines, subtotal, taxable_by_gst_rate)."""
    if not raw:
        raise HTTPException(400, "Add at least one line")
    items = {i.id: i for i in db.query(Item).all()}
    lines, by_rate = [], {}
    for n, r in enumerate(raw, start=1):
        item = items.get(r.get("item_id")) if r.get("item_id") else None
        qty = _num(r.get("qty", 1), f"Line {n} quantity")
        if qty <= 0:
            raise HTTPException(400, f"Line {n}: quantity must be positive")
        rate = _num(r["rate"], f"Line {n} rate") if r.get("rate") not in (None, "") else (item.rate if item else None)
        if rate is None or rate < 0:
            raise HTTPException(400, f"Line {n}: enter a rate")
        label = (r.get("description") or (item.name if item else "")).strip()
        if not label:
            raise HTTPException(400, f"Line {n}: choose an item or enter a description")
        gst_rate = _num(r["gst_rate"], f"Line {n} GST %") if r.get("gst_rate") not in (None, "") else (item.gst_rate if item else default_gst)
        if not 0 <= gst_rate <= 40:
            raise HTTPException(400, f"Line {n}: GST % must be between 0 and 40")
        amount = round(qty * rate, 2)
        lines.append({
            "label": label, "metric": "item", "qty": qty, "item_id": item.id if item else None,
            "hsn_sac": (r.get("hsn_sac") or (item.hsn_sac if item else "") or "").strip(), "gst_rate": gst_rate,
            "calc_detail": f"{qty:g} × ₹{rate:,.2f} @ {gst_rate:g}% GST", "amount": amount,
        })
        by_rate[gst_rate] = by_rate.get(gst_rate, 0.0) + amount
    return lines, round(sum(l["amount"] for l in lines), 2), by_rate


def gst_for(by_rate: dict[float, float], client_state_code: str, org_state_code: str) -> tuple[str, float]:
    intra = bool(client_state_code) and client_state_code == org_state_code
    total = round(sum(taxable * rate / 100.0 for rate, taxable in by_rate.items()), 2)
    return ("CGST_SGST" if intra else "IGST"), total


def price(db: Session, client_state_code: str, raw: list[dict]) -> dict:
    org = settings_store.get_all_settings(db)
    lines, subtotal, by_rate = build_lines(db, raw, float(org["DEFAULT_GST_RATE"] or 18))
    gst_type, gst = gst_for(by_rate, client_state_code, org["ORG_STATE_CODE"])
    return {"lines": lines, "subtotal": subtotal, "gst_type": gst_type, "gst": gst, "total": round(subtotal + gst, 2)}


def adhoc_run(db: Session, period: str, email: str) -> BillingRun:
    run = db.query(BillingRun).filter(BillingRun.period == period, BillingRun.trigger == "adhoc").first()
    if run is None:
        run = BillingRun(period=period, status="approved", trigger="adhoc", created_by=email,
                         approved_by="n/a (ad-hoc documents)", approved_at=datetime.datetime.utcnow(),
                         summary={"usage_source": "adhoc", "anomalies": []})
        db.add(run)
        db.flush()
    return run


def next_invoice_no(db: Session, prefix: str) -> str:
    n = 2480 + db.query(Invoice).count()
    while db.query(Invoice).filter(Invoice.invoice_no == f"{prefix}{n}").first():
        n += 1
    return f"{prefix}{n}"


def next_note_no(db: Session, prefix: str, doc_type: str) -> str:
    n = db.query(Invoice).filter(Invoice.doc_type == doc_type).count() + 1
    while db.query(Invoice).filter(Invoice.invoice_no == f"{prefix}{n:04d}").first():
        n += 1
    return f"{prefix}{n:04d}"


def next_quote_no(db: Session) -> str:
    n = db.query(Quote).count() + 1
    while db.query(Quote).filter(Quote.quote_no == f"QT-{n:04d}").first():
        n += 1
    return f"QT-{n:04d}"


def create_invoice(db: Session, client: Client, raw_lines: list[dict], *, email: str, source: str = "adhoc",
                   doc_type: str = "invoice", on: str | None = None, terms_days: int | None = None,
                   parent: Invoice | None = None, po_number: str = "", issue: bool = False) -> Invoice:
    if (client.status or "active") != "active":
        raise HTTPException(400, f"{client.name} is {client.status} — activate the customer first")
    org = settings_store.get_all_settings(db)
    priced = price(db, client.state_code, raw_lines)
    date = on or datetime.date.today().isoformat()
    run = adhoc_run(db, date[:7], email)
    days = int(terms_days or client.payment_terms_days or org["PAYMENT_TERMS_DAYS"] or 15)
    no = next_note_no(db, "DN-", "debit_note") if doc_type == "debit_note" else next_invoice_no(db, org["INVOICE_PREFIX"])
    inv = Invoice(run_id=run.id, client_id=client.id, invoice_no=no, period=date[:7], lines=priced["lines"],
                  subtotal=priced["subtotal"], gst_type=priced["gst_type"], gst=priced["gst"], tds=0.0,
                  total=priced["total"], payment_terms_days=days, balance_due=priced["total"],
                  due_date=(datetime.date.fromisoformat(date) + datetime.timedelta(days=days)).isoformat(),
                  doc_type=doc_type, source=source, parent_invoice_id=parent.id if parent else None, po_number=po_number)
    db.add(inv)
    db.flush()
    if issue:
        invoicing.issue(db, inv)
    return inv


def add_months(d: datetime.date, months: int) -> datetime.date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return datetime.date(year, month, min(d.day, last))


FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "annual": 12}


def advance(date_str: str, frequency: str) -> str:
    return add_months(datetime.date.fromisoformat(date_str), FREQ_MONTHS[frequency]).isoformat()


def run_recurring(db: Session, today: datetime.date | None = None, limit: int = 12) -> list[dict]:
    """Generate every recurring invoice that is due (catching up missed cycles, up to `limit` each)."""
    from app.models import RecurringInvoice
    today = today or datetime.date.today()
    out = []
    for rec in db.query(RecurringInvoice).filter(RecurringInvoice.status == "active").all():
        made = 0
        while rec.next_date <= today.isoformat() and made < limit:
            if rec.end_date and rec.next_date > rec.end_date:
                rec.status = "ended"
                break
            client = db.query(Client).get(rec.client_id)
            if client is None or (client.status or "active") != "active":
                out.append({"recurring_id": rec.id, "skipped": f"customer {'missing' if client is None else client.status}"})
                break
            inv = create_invoice(db, client, rec.lines, email="system@scheduler", source="recurring", on=rec.next_date, issue=rec.auto_issue)
            if rec.auto_issue:
                invoicing.send(db, inv, client)
            rec.generated_count = (rec.generated_count or 0) + 1
            rec.last_generated = rec.next_date
            rec.next_date = advance(rec.next_date, rec.frequency)
            made += 1
            out.append({"recurring_id": rec.id, "invoice_no": inv.invoice_no, "date": inv.period, "total": inv.total})
        if rec.end_date and rec.next_date > rec.end_date:
            rec.status = "ended"
    db.commit()
    return out
