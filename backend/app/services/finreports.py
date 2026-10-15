"""Financial and GST reports computed from the derived ledger and source documents."""
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.models import Bill, Client, CreditNote, Invoice, Item, Vendor
from app.services import ledger, settings_store

OPEN_INV = ("issued", "sent", "partially_paid", "overdue", "disputed", "pushed")


def _balances(rows: list[dict], accounts: dict, start: str | None, end: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if (start and r["date"] < start) or (end and r["date"] > end):
            continue
        a = out.setdefault(r["account"], {"debit": 0.0, "credit": 0.0})
        a["debit"] += r["debit"]
        a["credit"] += r["credit"]
    return out


def trial_balance(db: Session, as_of: str) -> dict:
    accts, rows = ledger.account_map(db), ledger.entries(db)
    bal = _balances(rows, accts, None, as_of)
    lines, td, tc = [], 0.0, 0.0
    for code in sorted(set(bal) | set(accts)):
        a = accts.get(code)
        b = bal.get(code, {"debit": 0.0, "credit": 0.0})
        net = round(b["debit"] - b["credit"], 2)
        if not net and code not in bal:
            continue
        dr, cr = (net, 0.0) if net > 0 else (0.0, -net)
        lines.append({"code": code, "name": a.name if a else "(unknown account)", "type": a.type if a else "?", "debit": dr, "credit": cr})
        td += dr; tc += cr
    return {"as_of": as_of, "lines": lines, "total_debit": round(td, 2), "total_credit": round(tc, 2),
            "balanced": abs(td - tc) < 0.01}


def profit_and_loss(db: Session, start: str, end: str) -> dict:
    accts, rows = ledger.account_map(db), ledger.entries(db)
    bal = _balances(rows, accts, start, end)
    income, expense = [], []
    for code, b in sorted(bal.items()):
        a = accts.get(code)
        if not a or a.type not in ("income", "expense"):
            continue
        amt = ledger.signed(a.type, b["debit"], b["credit"])
        (income if a.type == "income" else expense).append({"code": code, "name": a.name, "amount": amt})
    ti, te = round(sum(x["amount"] for x in income), 2), round(sum(x["amount"] for x in expense), 2)
    return {"start": start, "end": end, "income": income, "expense": expense, "total_income": ti,
            "total_expense": te, "net_profit": round(ti - te, 2)}


def balance_sheet(db: Session, as_of: str) -> dict:
    accts, rows = ledger.account_map(db), ledger.entries(db)
    bal = _balances(rows, accts, None, as_of)
    groups: dict[str, list] = {"asset": [], "liability": [], "equity": []}
    profit = 0.0
    for code, b in sorted(bal.items()):
        a = accts.get(code)
        if not a:
            continue
        amt = ledger.signed(a.type, b["debit"], b["credit"])
        if a.type in groups:
            if amt:
                groups[a.type].append({"code": code, "name": a.name, "amount": amt})
        elif a.type == "income":
            profit += amt
        elif a.type == "expense":
            profit -= amt
    groups["equity"].append({"code": "", "name": "Retained earnings (cumulative profit)", "amount": round(profit, 2)})
    totals = {k: round(sum(x["amount"] for x in v), 2) for k, v in groups.items()}
    return {"as_of": as_of, **groups, "total_assets": totals["asset"], "total_liabilities": totals["liability"],
            "total_equity": totals["equity"], "balanced": abs(totals["asset"] - totals["liability"] - totals["equity"]) < 0.01}


def general_ledger(db: Session, code: str, start: str, end: str) -> dict:
    accts = ledger.account_map(db)
    a = accts.get(code)
    if not a:
        return {"error": "Unknown account"}
    rows = [r for r in ledger.entries(db) if r["account"] == code]
    opening = ledger.signed(a.type, sum(r["debit"] for r in rows if r["date"] < start), sum(r["credit"] for r in rows if r["date"] < start))
    running, lines = opening, []
    for r in rows:
        if start <= r["date"] <= end:
            running = round(running + (r["debit"] - r["credit"] if a.type in ledger.DEBIT_NORMAL else r["credit"] - r["debit"]), 2)
            lines.append({**r, "balance": running})
    return {"account": {"code": a.code, "name": a.name, "type": a.type}, "start": start, "end": end,
            "opening": opening, "lines": lines, "closing": running}


def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0: return "current"
    if days_overdue <= 30: return "d1_30"
    if days_overdue <= 60: return "d31_60"
    if days_overdue <= 90: return "d61_90"
    return "d90_plus"


def _aged(rows: list[tuple[str, str, float, str | None]], today: datetime.date) -> dict:
    """rows: (party, ref, balance, due_date)."""
    parties: dict[str, dict] = {}
    for party, ref, bal, due in rows:
        try:
            days = (today - datetime.date.fromisoformat((due or "")[:10])).days
        except ValueError:
            days = 0
        p = parties.setdefault(party, {"party": party, "current": 0.0, "d1_30": 0.0, "d31_60": 0.0, "d61_90": 0.0, "d90_plus": 0.0, "total": 0.0})
        p[_bucket(days)] += bal
        p["total"] += bal
    out = sorted(parties.values(), key=lambda p: -p["total"])
    for p in out:
        for k in p:
            if k != "party":
                p[k] = round(p[k], 2)
    totals = {k: round(sum(p[k] for p in out), 2) for k in ("current", "d1_30", "d31_60", "d61_90", "d90_plus", "total")}
    return {"rows": out, "totals": totals}


def aged_receivables(db: Session) -> dict:
    clients = {c.id: c.name for c in db.query(Client).all()}
    rows = [(clients.get(i.client_id, "?"), i.invoice_no, i.balance_due or 0.0, i.due_date)
            for i in db.query(Invoice).filter(Invoice.status.in_(OPEN_INV)).all() if (i.balance_due or 0) > 0.005]
    res = _aged(rows, datetime.date.today())
    ar = sum(r["debit"] - r["credit"] for r in ledger.entries(db) if r["account"] == ledger.AR)
    res["ledger_balance"] = round(ar, 2)
    res["reconciled"] = abs(ar - res["totals"]["total"]) < 0.01
    return res


def aged_payables(db: Session) -> dict:
    vendors = {v.id: v.name for v in db.query(Vendor).all()}
    rows = [(vendors.get(b.vendor_id, "?"), b.bill_no, b.balance_due or 0.0, b.due_date)
            for b in db.query(Bill).filter(Bill.status.in_(("open", "partially_paid"))).all() if (b.balance_due or 0) > 0.005]
    res = _aged(rows, datetime.date.today())
    ap = sum(r["credit"] - r["debit"] for r in ledger.entries(db) if r["account"] == ledger.AP)
    res["ledger_balance"] = round(ap, 2)
    res["reconciled"] = abs(ap - res["totals"]["total"]) < 0.01
    return res


def _issued(db: Session, start: str, end: str):
    for inv in db.query(Invoice).filter(Invoice.status.notin_(("draft", "cancelled"))).all():
        d = ledger.parse_date(inv.issued_at or inv.created_at)
        if d and start <= d <= end:
            yield inv, d


def sales_by_customer(db: Session, start: str, end: str) -> dict:
    clients = {c.id: c.name for c in db.query(Client).all()}
    agg: dict[str, dict] = {}
    for inv, _ in _issued(db, start, end):
        a = agg.setdefault(clients.get(inv.client_id, "?"), {"customer": clients.get(inv.client_id, "?"), "invoices": 0, "taxable": 0.0, "gst": 0.0, "total": 0.0})
        a["invoices"] += 1; a["taxable"] += inv.subtotal; a["gst"] += inv.gst; a["total"] += inv.total
    rows = sorted(agg.values(), key=lambda r: -r["taxable"])
    for r in rows:
        for k in ("taxable", "gst", "total"):
            r[k] = round(r[k], 2)
    return {"start": start, "end": end, "rows": rows, "total_taxable": round(sum(r["taxable"] for r in rows), 2)}


def sales_by_item(db: Session, start: str, end: str) -> dict:
    agg: dict[str, dict] = {}
    for inv, _ in _issued(db, start, end):
        for ln in inv.lines or []:
            a = agg.setdefault(ln.get("label", "?"), {"item": ln.get("label", "?"), "qty": 0.0, "taxable": 0.0})
            a["qty"] += ln.get("qty") or 0
            a["taxable"] += ln.get("amount") or 0
    rows = sorted(agg.values(), key=lambda r: -r["taxable"])
    for r in rows:
        r["taxable"] = round(r["taxable"], 2); r["qty"] = round(r["qty"], 2)
    return {"start": start, "end": end, "rows": rows, "total_taxable": round(sum(r["taxable"] for r in rows), 2)}


# ------------------------------- GST -------------------------------

def _inv_rate_rows(inv: Invoice) -> list[dict]:
    """Taxable value and tax split per GST rate for one invoice."""
    by_rate: dict[float, float] = {}
    if inv.lines and all(ln.get("gst_rate") is not None for ln in inv.lines):
        for ln in inv.lines:
            by_rate[ln["gst_rate"]] = by_rate.get(ln["gst_rate"], 0.0) + (ln.get("amount") or 0)
    else:
        rate = round(inv.gst / inv.subtotal * 100, 2) if inv.subtotal else 0.0
        by_rate[rate] = inv.subtotal
    rows = []
    for rate, taxable in by_rate.items():
        tax = round(taxable * rate / 100, 2)
        rows.append({"rate": rate, "taxable": round(taxable, 2), **{k: 0.0 for k in ("igst", "cgst", "sgst")},
                     **{k: v for k, v in ledger._split_gst(inv.gst_type, tax).items()}})
    return rows


def gst_report(db: Session, start: str, end: str) -> dict:
    org = settings_store.get_all_settings(db)
    default_sac = org.get("DEFAULT_SAC") or "998314"
    clients = {c.id: c for c in db.query(Client).all()}
    b2b, b2c, cdn, hsn = [], {}, [], {}

    for inv, d in _issued(db, start, end):
        c = clients.get(inv.client_id)
        is_dn = inv.doc_type == "debit_note"
        for r in _inv_rate_rows(inv):
            row = {"gstin": c.gst if c else "", "customer": c.name if c else "", "invoice_no": inv.invoice_no, "date": d,
                   "state_code": c.state_code if c else "", **r, "value": inv.total}
            if is_dn:
                cdn.append({**row, "note_type": "Debit"})
            elif c and c.gst:
                b2b.append(row)
            else:
                k = (c.state_code if c else "", r["rate"])
                a = b2c.setdefault(k, {"state_code": k[0], "rate": k[1], "taxable": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})
                for f in ("taxable", "igst", "cgst", "sgst"):
                    a[f] += r[f]
        for ln in inv.lines or []:
            code = ln.get("hsn_sac") or default_sac
            rate = ln.get("gst_rate", round(inv.gst / inv.subtotal * 100, 2) if inv.subtotal else 0)
            h = hsn.setdefault((code, rate), {"hsn_sac": code, "rate": rate, "qty": 0.0, "taxable": 0.0})
            h["qty"] += ln.get("qty") or 0
            h["taxable"] += ln.get("amount") or 0

    for n in db.query(CreditNote).all():
        d = ledger.parse_date(n.created_at)
        inv = db.query(Invoice).get(n.invoice_id)
        if not d or not (start <= d <= end) or not inv or inv.status == "cancelled" or (n.reason or "").lower().startswith("write"):
            continue
        c = clients.get(inv.client_id)
        gross = inv.subtotal + inv.gst
        net = round(n.amount * inv.subtotal / gross, 2) if gross else n.amount
        split = ledger._split_gst(inv.gst_type, round(n.amount - net, 2))
        cdn.append({"gstin": c.gst if c else "", "customer": c.name if c else "", "invoice_no": f"CN-{n.id} (vs {inv.invoice_no})", "date": d,
                    "state_code": c.state_code if c else "", "rate": round(inv.gst / inv.subtotal * 100, 2) if inv.subtotal else 0,
                    "taxable": net, "igst": split.get("igst", 0.0), "cgst": split.get("cgst", 0.0), "sgst": split.get("sgst", 0.0),
                    "value": n.amount, "note_type": "Credit"})

    itc = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "ineligible": 0.0}
    for b in db.query(Bill).filter(Bill.status != "void").all():
        if not (start <= b.date <= end):
            continue
        for k, v in ledger._split_gst(b.gst_type, b.gst).items():
            itc[k if b.itc_eligible else "ineligible"] += v

    def tot(rows, f, sign=lambda r: 1):
        return round(sum(r[f] * sign(r) for r in rows), 2)
    sgn = lambda r: -1 if r.get("note_type") == "Credit" else 1
    allrows = b2b + list(b2c.values()) + cdn
    outward = {"taxable": tot(allrows, "taxable", sgn), "igst": tot(allrows, "igst", sgn), "cgst": tot(allrows, "cgst", sgn), "sgst": tot(allrows, "sgst", sgn)}
    payable = {k: round(outward[k] - itc[k], 2) for k in ("igst", "cgst", "sgst")}
    for rows in (b2c.values(), hsn.values()):
        for r in rows:
            for f in ("taxable", "igst", "cgst", "sgst", "qty"):
                if f in r: r[f] = round(r[f], 2)
    return {"start": start, "end": end, "b2b": b2b, "b2c": sorted(b2c.values(), key=lambda r: (r["state_code"], r["rate"])),
            "cdn": cdn, "hsn": sorted(hsn.values(), key=lambda r: r["hsn_sac"]),
            "summary": {"outward": outward, "itc": {k: round(v, 2) for k, v in itc.items()}, "net_payable": payable,
                        "total_payable": round(sum(payable.values()), 2)}}
