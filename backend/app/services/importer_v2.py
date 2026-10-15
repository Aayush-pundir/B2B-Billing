"""Import the legacy rulebook (JSONL), the periodic register (CSV) and an enterprise-id mapping (CSV)
into billing v2. The importer translates what it can recognise with certainty; anything it cannot
model becomes a `manual_formula` component, which blocks that account's invoice until a person
models it — it never guesses and never silently bills ₹0."""
from __future__ import annotations

import csv
import datetime
import io
import json
import re

from sqlalchemy.orm import Session

from app.models import (AccountEnterprise, AccountEvent, BillingAccount, BillingGroup, BillingGroupMember, Client, Contract,
                        ContractComponent, EscalationRule, SubscriptionInvoice)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _money(text: str) -> float | None:
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _date_dmy(text: str) -> str | None:
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", text or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


# ------------------------------------------------------------------ components

def _platform(r: dict) -> tuple[list[dict], list[str]]:
    A = r.get("A_platform") or {}
    t, rule = A.get("type") or "None", A.get("rule") or ""
    base, rate = _num(A.get("base_P")), _num(A.get("rate_M"))
    notes: list[str] = []
    manual = lambda why: ([{"component_type": "manual_formula", "label": "Platform fee (needs modelling)", "params": {"rule": f"{why}: {rule}"}}], [])

    if t == "None":
        return [], notes
    if t == "Load % (GTV)":
        if rate is None:
            return manual("rate is not numeric in the legacy sheet")
        p = {"rate": rate}
        if _num(A.get("min_N")):
            p["min_amount"] = A["min_N"]
        return [{"component_type": "load_percent", "label": "Platform fee — % of Add Money", "params": p}], notes
    if t == "Fixed SaaS":
        return ([{"component_type": "fixed_saas", "label": "Platform fee", "params": {"base_amount": base}}], notes) if base is not None else manual("no base amount")
    if t == "Fixed SaaS + 10% escalation":
        return ([{"component_type": "fixed_saas", "label": "Platform fee", "params": {"base_amount": base, "escalations_applied": [1.10]}}], ["escalated"]) if base is not None else manual("no base amount")
    if t == "Nil (hard-coded 0)":
        if "per truck" in rule.lower() or "per truck/driver" in rule.lower():
            return manual("fleet fee not coded")
        return [{"component_type": "waived", "label": "Platform fee waived", "params": {"reason": "Hard-coded 0 in the legacy sheet — attach an approval reference"}}], notes
    if t == "Custom formula":
        m = re.search(r"=\s*(\d+(?:\.\d+)?)\s*$", rule)
        if m:
            return [{"component_type": "fixed_amount", "label": "Platform fee", "params": {"amount": float(m.group(1))}}], notes
        if "P-S*0.5" in rule.replace(" ", "") and base is not None:
            return [{"component_type": "fixed_minus_upi_credit", "label": "Platform fee less UPI credit", "params": {"base": base, "credit_per_upi_txn": 0.5}}], notes
        return manual("custom formula")
    if t in ("Special", "Fixed (hard-typed)", "Load % (custom)", "Load % (pro-rata)", "Special / group"):
        low = rule.lower()
        if t == "Special / group":
            return [], ["group_parent"]  # the group's own fee component is created from the group definition
        if "inclusive of gst" in low:
            amt = _money(rule)
            return ([{"component_type": "fixed_saas_gst_inclusive", "label": "Platform fee (GST inclusive)", "params": {"gross_amount": amt}}], notes) if amt else manual("gst-inclusive amount")
        if re.search(r"less ₹?\s*0?\.?\d+(?:\.\d+)? per upi txn", low):
            cm = re.search(r"less ₹\s*(\d+(?:\.\d+)?)", rule, re.I)
            b = _money(rule)
            return ([{"component_type": "fixed_minus_upi_credit", "label": "Platform fee less UPI credit", "params": {"base": b, "credit_per_upi_txn": float(cm.group(1))}}], notes) if cm and b else manual("upi credit")
        m = re.search(r"₹\s*([\d,]+) fixed (?:per month )?up to (\d+) (trucks?|drivers?)[;,]?.*?₹\s*([\d,]+)\s*/\s*(truck|driver)", rule, re.I)
        if m:
            return [{"component_type": "per_unit_fleet", "label": "Platform fee (fleet)", "params": {"base": float(m.group(1).replace(",", "")), "included_units": float(m.group(2)),
                    "rate_per_extra_unit": float(m.group(4).replace(",", "")), "unit": "truck" if m.group(3).lower().startswith("truck") else "driver"}}], notes
        if low.startswith("bbps fee"):
            return [{"component_type": "bbps_fee", "label": "BBPS fee", "params": {"amount": _money(rule)}}], notes
        if "add money × 0.2%" in low and "3,500" in rule:
            return [{"component_type": "load_percent_plus_fixed", "label": "Platform fee", "params": {"rate": 0.002, "fixed": 3500.0}}], ["fleet_excess_not_modelled"]
        if "slab" in low and ("add money" in low or "contract slab" in low):
            return manual("tiered slabs — sheet applied only the first slab")
        if low.startswith("fixed saas:") and "×" in rule:
            amt = _money(rule)
            facs = [float(x) for x in re.findall(r"×\s*(1\.\d+)", rule)]
            return ([{"component_type": "fixed_saas", "label": "Platform fee", "params": {"base_amount": amt, "escalations_applied": facs}}], ["escalated"]) if amt else manual("fixed with escalations")
        if low.startswith("pro-rata:") and "×" in rule:
            amt = _money(rule)
            facs = [float(x) for x in re.findall(r"×\s*(1\.\d+)", rule)]
            return ([{"component_type": "fixed_saas", "label": "Platform fee", "params": {"base_amount": amt, "escalations_applied": facs}}], ["escalated", "prorata_first_month"]) if amt else manual("pro-rata")
        if low.startswith("pro-rata first month"):
            pm = re.search(r"L × ([\d.]+)%(?:, ₹([\d,]+))?", rule)
            if pm:
                p = {"rate": float(pm.group(1)) / 100}
                if pm.group(2):
                    p["min_amount"] = float(pm.group(2).replace(",", ""))
                return [{"component_type": "load_percent", "label": "Platform fee — % of Add Money", "params": p}], ["prorata_first_month"]
        if low.startswith("fixed monthly fee") and _money(rule):
            return [{"component_type": "fixed_amount", "label": "Platform fee", "params": {"amount": _money(rule)}}], notes
        return manual("unrecognised platform-fee rule")
    return manual(f"unknown platform type '{t}'")


def _upi(r: dict) -> list[dict]:
    B = r.get("B_upi") or {}
    t, rule = B.get("type") or "None", B.get("rule") or ""
    if t == "None":
        return []
    if t == "Free":
        return [{"component_type": "upi_free", "label": "UPI", "params": {}}]
    if t == "Per txn" and _num(B.get("rate")) is not None:
        return [{"component_type": "upi_per_txn", "label": "UPI fee", "params": {"rate": B["rate"], "free_txns": _num(B.get("free_txn")) or 0}}]
    low = rule.lower()
    if "spend-based" in low or "1% of spend" in low:
        return [{"component_type": "spend_percent_after_free_txns", "label": "UPI/card spend fee", "params": {"free_txns": _num(B.get("free_txn")) or 0, "pct": 1.0}}]
    if "flat per txn" in low and "2,500" in rule:
        return [{"component_type": "upi_amount_slab", "label": "UPI fee", "params": {"flat_per_txn_upto": 2.5, "threshold": 2500, "pct_above": 0.1}}]
    if "volume slab" in low:
        return [{"component_type": "upi_volume_slab", "label": "UPI fee (volume slabs)", "params": {"mode": "whole", "slabs": [
            {"upto_count": 80000, "rate": 0.40}, {"upto_count": 200000, "rate": 0.60}, {"upto_count": 400000, "rate": 0.80}, {"upto_count": None, "rate": 1.00}]}}]
    if "75% of spends via card" in low:
        return [{"component_type": "upi_conditional_pct", "label": "UPI fee (conditional)", "params": {"card_share_threshold": 75, "pct": 0.5, "min_per_txn": 1}}]
    return [{"component_type": "manual_formula", "label": "UPI fee (needs modelling)", "params": {"rule": f"unrecognised UPI rule: {rule}"}}]


def _module(r: dict) -> list[dict]:
    D = r.get("D_module") or {}
    m = re.search(r"(\d+(?:\.\d+)?) unit\(s\) × ₹\s*([\d,]+(?:\.\d+)?)", D.get("rule") or "")
    if not m:
        return []
    return [{"component_type": "module_package", "label": "Trip Management / module", "params": {"units": float(m.group(1)), "price": float(m.group(2).replace(",", ""))}}]


def _users(r: dict) -> list[dict]:
    C = r.get("C_users") or {}
    out = []
    if C.get("type") != "User-based":
        return out
    if _num(C.get("user_rate")) is not None:
        out.append({"component_type": "per_user", "label": "Users (makers)", "params": {"rate": C["user_rate"], "min_users": _num(C.get("mg_users")) or 0}})
    if _num(C.get("admin_rate")) is not None:
        out.append({"component_type": "per_admin", "label": "Admins / co-admins", "params": {"rate": C["admin_rate"], "free_admins": _num(C.get("free_admins")) or 0}})
    return out


# ------------------------------------------------------------------ events

def _events(r: dict) -> list[dict]:
    status, remarks = r.get("status") or "", (r.get("sheet_remarks") or "")
    go, low = r.get("go_live"), (r.get("status") or "").lower()
    ev = []
    if low.startswith("dropped"):
        ev.append({"event_type": "dropped", "effective_from": _date_dmy(remarks), "source_text": status})
    elif low.startswith("demo"):
        ev.append({"event_type": "demo", "source_text": status})
    elif low.startswith("on hold"):
        ev.append({"event_type": "on_hold", "source_text": status})
    elif low.startswith("frozen"):
        ev.append({"event_type": "frozen", "source_text": status})
    elif low.startswith("closure"):
        ev.append({"event_type": "closure_requested", "source_text": status})
    elif low.startswith("fee waived"):
        ev.append({"event_type": "fee_waived", "source_text": status})
    elif low.startswith("billed via"):
        ev.append({"event_type": "billed_elsewhere", "source_text": status})
    elif low.startswith("not using"):
        ev.append({"event_type": "not_using", "payload": {"policy": "suspend"}, "source_text": status})
    elif low.startswith("not yet live"):
        ev.append({"event_type": "not_live", "effective_to": (datetime.date.fromisoformat(go) - datetime.timedelta(days=1)).isoformat() if go else None, "source_text": status})
    elif "convenience" in low:
        ev.append({"event_type": "convenience_fee_model", "source_text": status})
    elif "quarterly" in low or "periodic" in low:
        ev.append({"event_type": "quarterly_billing", "source_text": status})
    elif "annual" in low or "next invoice" in low:
        ev.append({"event_type": "annual_billing", "source_text": status})
    if "mg deferred" in low:
        ev.append({"event_type": "mg_deferred", "effective_from": "2026-10-01", "payload": {"mg_effective_from": "2026-10-01"}, "source_text": status})
    if "do not auto debit" in remarks.lower():
        ev.append({"event_type": "no_auto_debit", "source_text": remarks[:120]})
    if any(w in remarks.lower() for w in ("check loading manually", "need to check", "name to be change", "required commercial")):
        ev.append({"event_type": "pending_check", "source_text": remarks[:120]})
    return ev


# ------------------------------------------------------------------ groups

def _groups(db: Session, lines: list[dict], by_row: dict[int, BillingAccount]) -> int:
    n = 0
    for r in lines:
        A = r.get("A_platform") or {}
        if A.get("type") != "Special / group" or r["row"] not in by_row:
            continue
        rule, ctext = A.get("rule") or "", str(r.get("contract_text") or "")
        parent = by_row[r["row"]]
        rows = [int(x) for x in re.findall(r"\[(?:row )?(\d+)\]", rule)]
        members = [by_row[x] for x in rows if x in by_row and x != r["row"]]
        if not members:
            continue
        rate_m = re.search(r"Add Money × ([\d.]+)%", rule)
        min_m = re.search(r"₹\s*([\d,]+)", rule)
        name = (parent.display_name.split(" ")[0] + " group").strip()
        if db.query(BillingGroup).filter(BillingGroup.name == name).first():
            continue
        g = BillingGroup(name=name, kind="net_parent", group_min_amount=float(min_m.group(1).replace(",", "")) if min_m else None,
                         rate=float(rate_m.group(1)) / 100 if rate_m else None, pool_load=False, subtract="A",
                         notes=f"Imported. Contract text: {ctext[:200]}. Review whether the load leg should use pooled Add Money.")
        db.add(g)
        db.flush()
        db.add(BillingGroupMember(group_id=g.id, account_id=parent.id, is_parent=True))
        for m in members:
            db.add(BillingGroupMember(group_id=g.id, account_id=m.id, is_parent=False))
        n += 1
    return n


# ------------------------------------------------------------------ public API

def import_rules(db: Session, text: str, user_email: str, lock: bool = False, replace: bool = False) -> dict:
    lines = [json.loads(l) for l in text.splitlines() if l.strip()]
    created = skipped = updated = 0
    by_row: dict[int, BillingAccount] = {}
    unmodelled = 0
    for r in lines:
        row = r.get("row")
        acct = db.query(BillingAccount).filter(BillingAccount.source_row == row).first()
        if acct and not replace:
            by_row[row] = acct
            skipped += 1
            continue
        name = (r.get("customer") or r.get("zoho_name") or f"Row {row}").strip()
        status = r.get("status") or ""
        if acct is None:
            client = Client(enterprise_id=f"ROW-{row}", name=name, billing_frequency="monthly",
                            status="inactive" if status in ("Dropped", "Demo – not billable") else "active")
            db.add(client)
            db.flush()
            acct = BillingAccount(client_id=client.id, display_name=name, source_row=row)
            db.add(acct)
            db.flush()
            db.add(AccountEnterprise(account_id=acct.id, enterprise_id=f"ROW-{row}", role="primary"))
            created += 1
        else:
            for c in list(acct.contracts):
                if c.source == "import" and c.status != "locked":
                    db.delete(c)
            for e in list(acct.events):
                db.delete(e)
            updated += 1
        acct.zoho_billing_name, acct.kam, acct.sales_manager = r.get("zoho_name") or "", r.get("kam") or "", r.get("sales_manager") or ""
        acct.go_live_date = r.get("go_live")
        acct.import_flags = [*(r.get("flags") or []), *(r.get("sheet_notes") or [])]
        acct.notes = "\n".join(x for x in [str(r.get("contract_text") or ""), r.get("sheet_remarks") or ""] if x and x != "0")
        by_row[row] = acct

        platform, tags = _platform(r)
        comps = [*platform, *_upi(r), *_users(r), *_module(r)]
        unmodelled += sum(c["component_type"] == "manual_formula" for c in comps)
        omin = _num(r.get("overall_min"))
        if "MG deferred" in status:
            # the component minimum IS the deferred MG: it must only start applying on the effective date
            for c in comps:
                if c["component_type"] == "load_percent" and c["params"].get("min_amount"):
                    omin = omin or c["params"].pop("min_amount")
        if "prorata_first_month" in tags and not r.get("go_live"):
            pm = re.search(r"×\s*(\d+)/(\d+)\s*days", (r.get("A_platform") or {}).get("rule", ""))
            if pm and int(pm.group(2)) == 30:  # the file is the Sep'26 run (30 days): N active days => live from day 30-N+1
                acct.go_live_date = f"2026-09-{30 - int(pm.group(1)) + 1:02d}"
        contract = Contract(account_id=acct.id, version=1, valid_from=acct.go_live_date or "2020-01-01", billing_frequency="monthly",
                            overall_min_amount=omin, mg_effective_from="2026-10-01" if "MG deferred" in status else None,
                            status="locked" if lock else "draft", source="import", created_by=user_email,
                            approved_by=user_email if lock else "", approved_at=datetime.datetime.utcnow() if lock else None,
                            notes=f"Imported from legacy rulebook row {row}. {' '.join(tags)}".strip())
        db.add(contract)
        db.flush()
        for i, c in enumerate(comps):
            db.add(ContractComponent(contract_id=contract.id, component_type=c["component_type"], label=c.get("label", ""), params=c["params"], sort_order=i))
        for e in _events(r):
            db.add(AccountEvent(account_id=acct.id, event_type=e["event_type"], effective_from=e.get("effective_from"), effective_to=e.get("effective_to"),
                                payload=e.get("payload") or {}, source_text=e.get("source_text", ""), created_by=user_email))
            if e["event_type"] == "no_auto_debit":
                acct.auto_debit = False
        if "escalated" in tags and r.get("go_live"):
            gl = datetime.date.fromisoformat(r["go_live"])
            nxt = gl.replace(year=max(gl.year + 1, datetime.date.today().year))
            if nxt < datetime.date.today():
                nxt = nxt.replace(year=nxt.year + 1)
            if not db.query(EscalationRule).filter(EscalationRule.account_id == acct.id).first():
                db.add(EscalationRule(account_id=acct.id, applies_to="fixed_saas", kind="percent", value=0.10, anniversary_basis="go_live",
                                      next_date=nxt.isoformat(), note="10% yearly escalation (from the legacy sheet)"))
    db.flush()
    groups = _groups(db, lines, by_row)
    db.commit()
    return {"lines": len(lines), "accounts_created": created, "accounts_updated": updated, "skipped_existing": skipped,
            "unmodelled_components": unmodelled, "groups_created": groups, "contracts_locked": lock}


def _period_dates(text: str) -> tuple[str | None, str | None]:
    m = re.match(r"(\w{3})-(\d{2}) to (\w{3})-(\d{2})", (text or "").strip())
    if not m:
        return None, None
    mon = {n: i for i, n in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
    try:
        a = datetime.date(2000 + int(m.group(2)), mon[m.group(1)], 1)
        y, mo = 2000 + int(m.group(4)), mon[m.group(3)]
        import calendar
        b = datetime.date(y, mo, calendar.monthrange(y, mo)[1])
        return a.isoformat(), b.isoformat()
    except (KeyError, ValueError):
        return None, None


def import_register(db: Session, text: str) -> dict:
    created = unmapped = 0
    freq = {"annual": "annual", "quarterly": "quarterly", "half-yearly": "half_yearly", "annual add-on": "annual"}
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        n = int(row["#"]) if (row.get("#") or "").isdigit() else None
        if n and db.query(SubscriptionInvoice).filter(SubscriptionInvoice.source_row == n).first():
            continue
        start, end = _period_dates(row.get("Period used"))
        mapped = (row.get("Mapped sheet row") or "").strip()
        acct = db.query(BillingAccount).filter(BillingAccount.source_row == int(mapped)).first() if mapped.isdigit() else None
        unmapped += acct is None
        try:
            amount = float(row.get("Amount (₹)") or 0)
        except ValueError:
            amount = 0.0
        db.add(SubscriptionInvoice(account_id=acct.id if acct else None, particulars=row.get("Particulars (as given)", ""),
                                   frequency=freq.get((row.get("Frequency") or "").lower(), "periodic"), bill_month=row.get("Bill month"),
                                   period_start=start, period_end=end, amount=amount, description=row.get("Remarks") or "Subscription", source_row=n))
        created += 1
    db.commit()
    return {"invoices": created, "unmapped_to_account": unmapped}


def mapping_template(db: Session) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_row", "customer", "current_enterprise_id", "enterprise_id (use ; to add more)"])
    for a in db.query(BillingAccount).order_by(BillingAccount.source_row).all():
        w.writerow([a.source_row or "", a.display_name, a.client.enterprise_id, ""])
    return buf.getvalue()


def import_mapping(db: Session, text: str) -> dict:
    """CSV with source_row (or customer) and enterprise_id. Several ids separated by ';' link clubbed enterprises."""
    done, errors = 0, []
    for i, row in enumerate(csv.DictReader(io.StringIO(text.lstrip("﻿"))), start=2):
        ids = [x.strip() for x in re.split(r"[;,]", row.get("enterprise_id (use ; to add more)") or row.get("enterprise_id") or "") if x.strip()]
        if not ids:
            continue
        sr = (row.get("source_row") or "").strip()
        acct = db.query(BillingAccount).filter(BillingAccount.source_row == int(sr)).first() if sr.isdigit() else \
            db.query(BillingAccount).filter(BillingAccount.display_name == (row.get("customer") or "").strip()).first()
        if not acct:
            errors.append(f"row {i}: account not found")
            continue
        clash = db.query(Client).filter(Client.enterprise_id == ids[0], Client.id != acct.client_id).first()
        if clash:
            errors.append(f"row {i}: {ids[0]} is already used by {clash.name}")
            continue
        acct.client.enterprise_id = ids[0]
        for e in list(acct.enterprises):
            db.delete(e)
        db.flush()
        for k, eid in enumerate(ids):
            db.add(AccountEnterprise(account_id=acct.id, enterprise_id=eid, role="primary" if k == 0 else "secondary"))
        done += 1
    db.commit()
    return {"mapped": done, "errors": errors}
