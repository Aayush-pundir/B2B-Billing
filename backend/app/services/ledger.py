"""Double-entry ledger, derived live from the source documents. Nothing is
posted by hooks, so the books can never drift from invoices, receipts and bills:

  issued invoice / debit note   Dr Receivable (+ TDS receivable)   Cr Revenue, Output GST
  receipt matched to invoice    Dr Bank                            Cr Receivable
  receipt not yet matched       Dr Bank                            Cr Unallocated receipts (or Suspense if dismissed)
  credit note                   Dr Sales returns, Output GST       Cr Receivable
  write-off                     Dr Bad debts                       Cr Receivable
  bill                          Dr Expense, Input GST (if ITC)     Cr Payable (+ TDS payable)
  bill payment                  Dr Payable                         Cr Bank
  manual / opening journal      as entered
"""
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.models import Account, Bill, BillPayment, Client, CreditNote, Invoice, Item, JournalEntry, Payment, Vendor

BANK, AR, TDS_REC, AP, UNALLOC, SUSPENSE, TDS_PAY = "1100", "1200", "1250", "2100", "2250", "2260", "2400"
OUT_GST = {"cgst": "2310", "sgst": "2320", "igst": "2330"}
IN_GST = {"cgst": "1310", "sgst": "1320", "igst": "1330"}
REVENUE, RETURNS, BAD_DEBT = "4000", "4900", "5800"
DEBIT_NORMAL = {"asset", "expense"}


def parse_date(value) -> str | None:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    for candidate in {text.split("T")[0].split(" ")[0], text[:11].strip(), text[:10]}:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y/%m/%d"):
            try:
                return datetime.datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _split_gst(gst_type: str, amount: float) -> dict[str, float]:
    if gst_type == "CGST_SGST":
        half = round(amount / 2, 2)
        return {"cgst": half, "sgst": round(amount - half, 2)}
    return {"igst": round(amount, 2)}


def _row(date, account, debit, credit, ref, desc, source, party=""):
    return {"date": date, "account": account, "debit": round(debit, 2), "credit": round(credit, 2),
            "ref": ref, "desc": desc, "source": source, "party": party}


def entries(db: Session) -> list[dict]:
    rows: list[dict] = []
    clients = {c.id: c.name for c in db.query(Client).all()}
    items = {i.id: i for i in db.query(Item).all()}
    invoices = {i.id: i for i in db.query(Invoice).all()}

    for inv in invoices.values():
        if inv.status in ("draft", "cancelled"):
            continue
        date = parse_date(inv.issued_at or inv.created_at)
        party = clients.get(inv.client_id, "")
        kind = "Debit note" if inv.doc_type == "debit_note" else "Invoice"
        ref, desc = inv.invoice_no, f"{kind} {inv.invoice_no} — {party}"
        revenue: dict[str, float] = {}
        for ln in inv.lines or []:
            item = items.get(ln.get("item_id")) if ln.get("item_id") else None
            acct = item.revenue_account if item and item.revenue_account else REVENUE
            revenue[acct] = revenue.get(acct, 0.0) + (ln.get("amount") or 0.0)
        if not revenue:
            revenue[REVENUE] = inv.subtotal
        drift = round(inv.subtotal - sum(revenue.values()), 2)
        first = next(iter(revenue))
        revenue[first] = round(revenue[first] + drift, 2)
        source = "debit_note" if inv.doc_type == "debit_note" else "invoice"
        receivable = round(inv.subtotal + inv.gst - (inv.tds or 0.0), 2)  # equals inv.total; recomputed so the entry always balances
        rows.append(_row(date, AR, receivable, 0, ref, desc, source, party))
        if inv.tds:
            rows.append(_row(date, TDS_REC, inv.tds, 0, ref, desc, source, party))
        for acct, amt in revenue.items():
            rows.append(_row(date, acct, 0, amt, ref, desc, source, party))
        for k, amt in _split_gst(inv.gst_type, inv.gst).items():
            if amt:
                rows.append(_row(date, OUT_GST[k], 0, amt, ref, desc, source, party))

    for p in db.query(Payment).all():
        date = parse_date(p.date)
        if not date or not p.amount:
            continue
        inv = invoices.get(p.invoice_id) if p.invoice_id else None
        party = clients.get(p.client_id, "")
        ref = p.reference or f"receipt#{p.id}"
        if inv:
            rows.append(_row(date, BANK, p.amount, 0, ref, f"Receipt against {inv.invoice_no}", "receipt", party))
            rows.append(_row(date, AR, 0, p.amount, ref, f"Receipt against {inv.invoice_no}", "receipt", party))
        else:
            target = SUSPENSE if p.match_confidence == "dismissed" else UNALLOC
            rows.append(_row(date, BANK, p.amount, 0, ref, "Receipt not yet matched to an invoice", "receipt", party))
            rows.append(_row(date, target, 0, p.amount, ref, "Receipt not yet matched to an invoice", "receipt", party))

    for n in db.query(CreditNote).all():
        inv = invoices.get(n.invoice_id)
        if not inv or inv.status == "cancelled":
            continue
        date, party = parse_date(n.created_at), clients.get(inv.client_id, "")
        ref = f"CN-{n.id}"
        if (n.reason or "").lower().startswith("write"):
            rows.append(_row(date, BAD_DEBT, n.amount, 0, ref, f"Write-off {inv.invoice_no}", "write_off", party))
            rows.append(_row(date, AR, 0, n.amount, ref, f"Write-off {inv.invoice_no}", "write_off", party))
            continue
        gross = inv.subtotal + inv.gst
        net = round(n.amount * inv.subtotal / gross, 2) if gross else n.amount
        tax = round(n.amount - net, 2)
        rows.append(_row(date, RETURNS, net, 0, ref, f"Credit note on {inv.invoice_no}", "credit_note", party))
        for k, amt in _split_gst(inv.gst_type, tax).items():
            if amt:
                rows.append(_row(date, OUT_GST[k], amt, 0, ref, f"Credit note on {inv.invoice_no}", "credit_note", party))
        rows.append(_row(date, AR, 0, n.amount, ref, f"Credit note on {inv.invoice_no}", "credit_note", party))

    vendors = {v.id: v.name for v in db.query(Vendor).all()}
    bills = {b.id: b for b in db.query(Bill).all()}
    for b in bills.values():
        if b.status == "void":
            continue
        party, ref = vendors.get(b.vendor_id, ""), b.bill_no
        desc = f"Bill {b.bill_no} — {party}"
        expense: dict[str, float] = {}
        for ln in b.lines or []:
            expense[ln["account_code"]] = expense.get(ln["account_code"], 0.0) + ln.get("amount", 0.0)
        drift = round(b.subtotal - sum(expense.values()), 2)
        k0 = next(iter(expense))
        expense[k0] = round(expense[k0] + drift, 2)
        for acct, amt in expense.items():
            rows.append(_row(b.date, acct, amt, 0, ref, desc, "bill", party))
        gst_split = _split_gst(b.gst_type, b.gst)
        for k, amt in gst_split.items():
            if amt:  # ineligible input tax is a cost, not a credit
                rows.append(_row(b.date, IN_GST[k] if b.itc_eligible else k0, amt, 0, ref, desc, "bill", party))
        payable = round(b.subtotal + b.gst - (b.tds or 0.0), 2)
        rows.append(_row(b.date, AP, 0, payable, ref, desc, "bill", party))
        if b.tds:
            rows.append(_row(b.date, TDS_PAY, 0, b.tds, ref, desc, "bill", party))

    for bp in db.query(BillPayment).all():
        b = bills.get(bp.bill_id)
        if not b or b.status == "void":
            continue
        party = vendors.get(b.vendor_id, "")
        desc = f"Payment for bill {b.bill_no} — {party}"
        rows.append(_row(bp.date, AP, bp.amount, 0, bp.reference or f"bp#{bp.id}", desc, "bill_payment", party))
        rows.append(_row(bp.date, BANK, 0, bp.amount, bp.reference or f"bp#{bp.id}", desc, "bill_payment", party))

    for je in db.query(JournalEntry).all():
        for ln in je.lines:
            rows.append(_row(je.date, ln.account_code, ln.debit or 0, ln.credit or 0, je.ref or f"JE-{je.id}",
                             ln.note or je.memo or "Journal", "journal" if je.kind != "opening" else "opening", ""))
    rows = [r for r in rows if r["date"]]
    rows.sort(key=lambda r: (r["date"], r["ref"]))
    return rows


def account_map(db: Session) -> dict[str, Account]:
    return {a.code: a for a in db.query(Account).all()}


def signed(account_type: str, debit: float, credit: float) -> float:
    """Balance in the account's natural direction (assets/expenses: Dr-Cr; others: Cr-Dr)."""
    return round(debit - credit, 2) if account_type in DEBIT_NORMAL else round(credit - debit, 2)
