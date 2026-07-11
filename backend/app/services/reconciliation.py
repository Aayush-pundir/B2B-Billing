"""Bank statement reconciliation — the authoritative source for whether an
invoice is actually paid. Zoho's own payment status (when the org uses it)
feeds into the same `payments` table as just one more source, never as the
sole signal, because most B2B clients pay by direct NEFT/RTGS that never
touches Zoho at all.

Five-tier matching, most confident first:
  1. exact_reference   — invoice number appears verbatim in the narration
  2. amount_client      — amount matches exactly one open invoice for a
                           client identifiable from the narration
  3. amount_only        — amount matches multiple open invoices; queued
  4. partial             — amount is less than an identifiable invoice's
                           balance; queued for manual partial-allocation
  5. none                — no candidate at all; queued as an unidentified
                           receipt
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from dateutil import parser as dateutil_parser
from sqlalchemy.orm import Session

from app.models import Client, Invoice, Payment

AMOUNT_TOLERANCE = 1.0  # rupees — allows for bank rounding


def _normalise_date(raw: str) -> str:
    """Bank statements and manual sheets use every date format imaginable —
    normalise to ISO so the client statement of account sorts correctly
    against invoice timestamps."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return dateutil_parser.parse(raw, dayfirst=True).date().isoformat()
    except (ValueError, OverflowError):
        return raw

HEADER_ALIASES = {
    "date": ["date", "txn date", "transaction date", "value date", "posting date"],
    "narration": ["narration", "description", "particulars", "remarks", "details", "transaction remarks"],
    "credit": ["credit", "credit amount", "deposit", "deposit amt", "amount (cr)", "cr amount"],
    "debit": ["debit", "debit amount", "withdrawal", "withdrawal amt", "amount (dr)", "dr amount"],
    "reference": ["reference", "reference no", "chq/ref no", "cheque no", "utr", "utr no", "ref no"],
}


def _match_header(fieldnames: list[str], canonical: str) -> str | None:
    aliases = HEADER_ALIASES[canonical]
    lowered = {f.strip().lower(): f for f in fieldnames}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    # fallback: substring match
    for f_lower, f_orig in lowered.items():
        if any(alias in f_lower for alias in aliases):
            return f_orig
    return None


@dataclass
class CreditRow:
    date: str
    amount: float
    narration: str
    reference: str


def parse_bank_statement(raw: bytes) -> list[CreditRow]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    col_date = _match_header(reader.fieldnames, "date")
    col_narration = _match_header(reader.fieldnames, "narration")
    col_credit = _match_header(reader.fieldnames, "credit")
    col_reference = _match_header(reader.fieldnames, "reference")

    rows: list[CreditRow] = []
    for row in reader:
        credit_raw = (row.get(col_credit) or "0").replace(",", "").strip() if col_credit else "0"
        try:
            credit = float(credit_raw) if credit_raw else 0.0
        except ValueError:
            credit = 0.0
        if credit <= 0:
            continue  # only money coming in is relevant to receivables
        rows.append(CreditRow(
            date=_normalise_date(row.get(col_date)) if col_date else "",
            amount=credit,
            narration=(row.get(col_narration) or "").strip() if col_narration else "",
            reference=(row.get(col_reference) or "").strip() if col_reference else "",
        ))
    return rows


def _find_invoice_number_in_text(text: str, open_invoices: list[Invoice]) -> Invoice | None:
    text_upper = text.upper()
    for inv in open_invoices:
        if inv.invoice_no.upper() in text_upper:
            return inv
    return None


def _identify_client(text: str, clients: list[Client]) -> Client | None:
    text_upper = text.upper()
    for client in clients:
        if client.enterprise_id.upper() in text_upper:
            return client
    for client in clients:
        first_word = client.name.split()[0].upper() if client.name else ""
        if len(first_word) >= 4 and first_word in text_upper:
            return client
    return None


def apply_payment_to_invoice(invoice: Invoice, amount: float) -> None:
    invoice.paid_amount = round((invoice.paid_amount or 0.0) + amount, 2)
    invoice.balance_due = round(max(0.0, invoice.total - invoice.paid_amount), 2)
    if invoice.balance_due <= 0.01:
        invoice.status = "paid"
    elif invoice.paid_amount > 0:
        invoice.status = "partially_paid"


@dataclass
class ManualRow:
    enterprise_id: str
    date: str
    amount: float
    mode: str
    reference: str


def parse_manual_collections(raw: bytes) -> list[ManualRow]:
    """columns: enterprise_id, amount, date, mode, reference"""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        try:
            amount = float((row.get("amount") or "0").replace(",", ""))
        except ValueError:
            amount = 0.0
        if amount <= 0:
            continue
        rows.append(ManualRow(
            enterprise_id=(row.get("enterprise_id") or "").strip(),
            date=_normalise_date(row.get("date")),
            amount=amount,
            mode=(row.get("mode") or "").strip(),
            reference=(row.get("reference") or "").strip(),
        ))
    return rows


def reconcile_manual_rows(db: Session, rows: list[ManualRow], created_by: str) -> dict:
    clients_by_eid = {c.enterprise_id: c for c in db.query(Client).all()}
    auto_matched, queued, unmatched = [], [], []

    for row in rows:
        client = clients_by_eid.get(row.enterprise_id)
        payment = Payment(
            date=row.date, amount=row.amount, mode=row.mode, reference=row.reference,
            source="manual_upload", created_by=created_by,
        )
        if client is None:
            payment.match_confidence = "none"
            db.add(payment)
            db.flush()
            unmatched.append({"payment_id": payment.id, "amount": row.amount,
                               "reason": f"unknown EnterpriseId {row.enterprise_id}"})
            continue

        payment.client_id = client.id
        open_invoices = (
            db.query(Invoice)
            .filter(Invoice.client_id == client.id, Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]))
            .all()
        )
        candidates = [inv for inv in open_invoices if abs(inv.balance_due - row.amount) <= AMOUNT_TOLERANCE]

        if len(candidates) == 1:
            inv = candidates[0]
            payment.invoice_id = inv.id
            payment.match_confidence = "amount_client"
            payment.matched_by = "auto"
            apply_payment_to_invoice(inv, row.amount)
            db.add(payment)
            auto_matched.append({"amount": row.amount, "invoice_no": inv.invoice_no, "tier": "amount_client"})
            continue

        payment.match_confidence = "amount_only" if candidates else "partial"
        db.add(payment)
        db.flush()
        queued.append({"payment_id": payment.id, "amount": row.amount, "enterprise_id": row.enterprise_id,
                        "candidates": [c.invoice_no for c in (candidates or open_invoices)]})

    db.commit()
    return {
        "auto_matched": len(auto_matched), "queued": len(queued), "unmatched": len(unmatched),
        "auto_matched_detail": auto_matched, "queued_detail": queued, "unmatched_detail": unmatched,
    }


def reconcile_credit_rows(db: Session, rows: list[CreditRow], source: str, import_id: int | None, created_by: str) -> dict:
    clients = db.query(Client).all()
    open_invoices = (
        db.query(Invoice)
        .filter(Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]))
        .all()
    )

    auto_matched, queued, unmatched = [], [], []

    for row in rows:
        text = f"{row.narration} {row.reference}"
        payment = Payment(
            date=row.date or "", amount=row.amount, narration=row.narration, reference=row.reference,
            source=source, import_id=import_id, created_by=created_by,
        )

        exact = _find_invoice_number_in_text(text, open_invoices)
        if exact is not None and abs(exact.balance_due - row.amount) <= AMOUNT_TOLERANCE:
            payment.invoice_id = exact.id
            payment.client_id = exact.client_id
            payment.match_confidence = "exact_reference"
            payment.matched_by = "auto"
            apply_payment_to_invoice(exact, row.amount)
            db.add(payment)
            auto_matched.append({"amount": row.amount, "narration": row.narration, "date": row.date,
                                  "invoice_no": exact.invoice_no, "tier": "exact_reference"})
            continue

        client = _identify_client(text, clients)
        candidates = [
            inv for inv in open_invoices
            if abs(inv.balance_due - row.amount) <= AMOUNT_TOLERANCE and (client is None or inv.client_id == client.id)
        ]

        if client is not None and len(candidates) == 1:
            inv = candidates[0]
            payment.invoice_id = inv.id
            payment.client_id = client.id
            payment.match_confidence = "amount_client"
            payment.matched_by = "auto"
            apply_payment_to_invoice(inv, row.amount)
            db.add(payment)
            auto_matched.append({"amount": row.amount, "narration": row.narration, "date": row.date,
                                  "invoice_no": inv.invoice_no, "tier": "amount_client"})
            continue

        if len(candidates) > 1:
            payment.client_id = client.id if client else None
            payment.match_confidence = "amount_only"
            payment.matched_by = ""
            db.add(payment)
            db.flush()
            queued.append({"payment_id": payment.id, "amount": row.amount, "narration": row.narration,
                            "candidates": [c.invoice_no for c in candidates], "tier": "amount_only"})
            continue

        # partial payment: identifiable client with an open invoice of larger balance
        if client is not None:
            partial_candidates = [inv for inv in open_invoices if inv.client_id == client.id and inv.balance_due > row.amount]
            if partial_candidates:
                payment.client_id = client.id
                payment.match_confidence = "partial"
                payment.matched_by = ""
                db.add(payment)
                db.flush()
                queued.append({"payment_id": payment.id, "amount": row.amount, "narration": row.narration,
                                "candidates": [c.invoice_no for c in partial_candidates], "tier": "partial"})
                continue

        payment.match_confidence = "none"
        payment.matched_by = ""
        db.add(payment)
        db.flush()
        unmatched.append({"payment_id": payment.id, "amount": row.amount, "narration": row.narration, "date": row.date})

    db.commit()
    return {
        "auto_matched": len(auto_matched),
        "queued": len(queued),
        "unmatched": len(unmatched),
        "auto_matched_detail": auto_matched,
        "queued_detail": queued,
        "unmatched_detail": unmatched,
    }
