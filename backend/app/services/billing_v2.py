"""Billing v2 orchestration: lifecycle windows, subscription coverage, usage aggregation
across an account's enterprises, the engine, group minimums and the validators (spec §10)."""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal as D

from sqlalchemy.orm import Session, selectinload

from app.models import (AccountEvent, BillingAccount, BillingGroup, BillingGroupMember, Client, Contract,
                        SubscriptionInvoice)
from app.services import engine2, snapshots
from app.services.engine2 import dec, q

NONBILLABLE = {"demo", "dropped", "on_hold", "frozen", "closure_requested", "fee_waived", "billed_elsewhere"}
FLAG_EVENTS = {"no_auto_debit", "convenience_fee_model", "annual_billing", "quarterly_billing"}
BLOCKING_FLAG_EVENTS = {"pending_check"}


def _d(v) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def period_bounds(period: str) -> tuple[datetime.date, datetime.date, int]:
    y, m = int(period[:4]), int(period[5:7])
    days = calendar.monthrange(y, m)[1]
    return datetime.date(y, m, 1), datetime.date(y, m, days), days


# ------------------------------- lifecycle -------------------------------

def lifecycle(account: BillingAccount, period: str) -> dict:
    start, end, days = period_bounds(period)
    live_from = _d(account.go_live_date)
    windows, flags, reasons, mg_from = [], [], [], None
    dropped = None
    for ev in account.events:
        a, b = _d(ev.effective_from), _d(ev.effective_to)
        t = ev.event_type
        if t == "not_live":
            windows.append((datetime.date.min, b or datetime.date.max, "not live"))
        elif t == "dropped":
            dropped = a or datetime.date.min
            windows.append((dropped, datetime.date.max, "dropped"))
        elif t in NONBILLABLE:
            windows.append((a or datetime.date.min, b or datetime.date.max, t.replace("_", " ")))
        elif t == "not_using":
            if (ev.payload or {}).get("policy", "suspend") != "bill":
                windows.append((a or datetime.date.min, b or datetime.date.max, "not using the platform"))
            flags.append("not_using")
        elif t == "mg_deferred":
            mg_from = _d((ev.payload or {}).get("mg_effective_from")) or a
        elif t in FLAG_EVENTS or t in BLOCKING_FLAG_EVENTS:
            if (not a or a <= end) and (not b or b >= start):
                flags.append(t)
    if live_from:
        windows.append((datetime.date.min, live_from - datetime.timedelta(days=1), "not yet live"))
    active = 0
    for n in range(days):
        day = start + datetime.timedelta(days=n)
        hit = next((w for w in windows if w[0] <= day <= w[1]), None)
        if hit is None:
            active += 1
        elif hit[2] not in reasons:
            reasons.append(hit[2])
    return {"billable": active > 0, "active_days": active, "days_in_month": days, "fraction": D(active) / D(days),
            "reason": ", ".join(reasons) if not active else "", "partial_reasons": reasons if 0 < active < days else [],
            "flags": flags, "mg_from": mg_from}


# ------------------------------- subscriptions -------------------------------

def raised_subscriptions(db: Session) -> dict[int, list[SubscriptionInvoice]]:
    """Every raised subscription invoice grouped by account — one query for a whole run instead of one per account."""
    out: dict[int, list[SubscriptionInvoice]] = {}
    for s in db.query(SubscriptionInvoice).filter(SubscriptionInvoice.status == "raised", SubscriptionInvoice.account_id.isnot(None)).order_by(SubscriptionInvoice.id).all():
        out.setdefault(s.account_id, []).append(s)
    return out


def covering_subscriptions(db: Session, account_id: int, period: str, prefetched: dict | None = None) -> list[SubscriptionInvoice]:
    start, end, _ = period_bounds(period)
    out = []
    rows = prefetched.get(account_id, []) if prefetched is not None else db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == account_id, SubscriptionInvoice.status == "raised").all()
    for s in rows:
        a, b = _d(s.period_start), _d(s.period_end)
        if a and b and a <= start and b >= end:
            out.append(s)
    return out


# ------------------------------- contract & usage -------------------------------

def active_contract(account: BillingAccount, period: str) -> Contract | None:
    start, end, _ = period_bounds(period)
    best = None
    for c in account.contracts:
        if c.status not in ("locked", "superseded"):  # superseded versions still govern their own past periods
            continue
        a, b = _d(c.valid_from), _d(c.valid_to)
        if (a and a > end) or (b and b < start):
            continue
        if best is None or c.version > best.version:
            best = c
    return best


def usage_for(account: BillingAccount, period: str, snaps: dict[str, dict]) -> tuple[dict, list[str], bool]:
    """Sum an account's enterprises (respecting include_* flags and validity). Returns (usage, manual_fields, any_seen)."""
    start, end, _ = period_bounds(period)
    usage: dict = {}
    manual: set[str] = set()
    seen = False
    gates = {"add_money": "include_add_money", "upi_txn_count": "include_upi", "upi_txn_amount": "include_upi", "upi_cnt_le_2500": "include_upi",
             "upi_amt_gt_2500": "include_upi", "upi_spend_after_free": "include_upi", "card_share_pct": "include_upi",
             "maker_count": "include_users", "admin_count": "include_users", "checker_count": "include_users", "approver_count": "include_users",
             "truck_count": "include_users", "driver_count": "include_users"}
    for ae in account.enterprises:
        a, b = _d(ae.valid_from), _d(ae.valid_to)
        if (a and a > end) or (b and b < start):
            continue
        row = snaps.get(ae.enterprise_id)
        if row is None:
            continue
        seen = True
        manual |= set(row.get("_manual", []))
        for f, gate in gates.items():
            if not getattr(ae, gate):
                continue
            v = row.get(f)
            if v is None:
                continue
            usage[f] = (usage.get(f) or 0) + v if f != "card_share_pct" else v
    return usage, sorted(manual), seen


def compute_account(db: Session, account: BillingAccount, period: str, snaps: dict[str, dict], *, ignore_overall_min: bool = False, subs_by_account: dict | None = None) -> dict | None:
    """Returns None when the account has no locked v2 contract (caller falls back to v1 rules)."""
    contract = active_contract(account, period)
    if contract is None:
        return None
    start, end, days = period_bounds(period)
    life = lifecycle(account, period)
    usage, manual, seen = usage_for(account, period, snaps)
    if seen is False:
        # absent from every usage source = no activity: metrics are 0, not "not supplied"
        usage = {f: 0 for f in snapshots.FIELDS if f not in ("upi_spend_after_free", "card_share_pct", "truck_count", "driver_count", "upi_cnt_le_2500", "upi_amt_gt_2500")}
    comps = []
    for c in contract.components:
        a, b = _d(c.valid_from), _d(c.valid_to)
        if (a and a > end) or (b and b < start):
            continue
        comps.append({"component_type": c.component_type, "label": c.label, "params": c.params or {}, "sort_order": c.sort_order})
    subs = covering_subscriptions(db, account.id, period, subs_by_account)
    covers = set(contract.subscription_covers or ["A"]) if subs else set()
    mg_from = _d(contract.mg_effective_from) or life["mg_from"]
    ctx = {"period_start": start, "days_in_month": days, "active_days": life["active_days"], "active_fraction": life["fraction"],
           "gst_rate": D(str(contract.gst_rate or 0.18))}
    res = engine2.compute_account(
        comps, usage, ctx, overall_min=None if ignore_overall_min else contract.overall_min_amount,
        overall_cap=contract.overall_cap_amount, mg_effective_from=mg_from, prorate_mg=bool(contract.prorate_mg), covered_groups=covers)
    return {"account": account, "contract": contract, "life": life, "usage": usage, "manual": manual, "seen": seen, "subs": subs, "components": comps,
            "lines": res["lines"], "subtotal": res["subtotal"], "flags": res["flags"], "exceptions": []}


# ------------------------------- groups -------------------------------

def _group_a(r: dict) -> D:
    return sum((l["amount"] for l in r["lines"] if l.get("group") == "A"), D("0"))


def apply_groups(db: Session, results: dict[int, dict]) -> None:
    """Resolve group minimums after members are computed. Deterministic order: members by id, then parent."""
    for g in db.query(BillingGroup).order_by(BillingGroup.id).all():
        members = db.query(BillingGroupMember).filter(BillingGroupMember.group_id == g.id).all()
        have = sorted((m for m in members if m.account_id in results and results[m.account_id]["life"]["billable"]), key=lambda m: m.account_id)
        if not have:
            continue
        parent_m = next((m for m in have if m.is_parent), None)
        floor_amt = dec(g.group_min_amount) if g.group_min_amount is not None else None

        if g.kind == "net_parent" and parent_m:
            parent = results[parent_m.account_id]
            kids = [results[m.account_id] for m in have if not m.is_parent]
            L = dec(parent["usage"].get("add_money"))
            if g.pool_load:
                L += sum((dec(k["usage"].get("add_money")) for k in kids), D("0"))
            raw = L * dec(g.rate) if g.rate else D("0")
            group_amt = max(raw, floor_amt or D("0"))
            sub_basis = (lambda k: _group_a(k)) if g.subtract == "A" else (lambda k: k["subtotal"])
            deducted = sum((sub_basis(k) for k in kids), D("0"))
            fee = max(group_amt - deducted, D("0"))
            for l in parent["lines"]:
                if l.get("group") == "A" and l["amount"] != 0:
                    l["detail"] += " (replaced by group fee)"
                    parent["subtotal"] -= l["amount"]
                    l["amount"] = D("0")
            parent["lines"].append({"component_type": "group_fee", "label": f"Group platform fee — {g.name}", "amount": q(fee), "group": "A", "flags": [],
                                    "detail": f"max({(dec(g.rate) * 100).normalize() if g.rate else 0}% × {engine2.fmt(L)} {'pooled' if g.pool_load else 'parent'} load, min {engine2.fmt(floor_amt or D('0'))}) = {engine2.fmt(group_amt)} − members' {g.subtract} {engine2.fmt(deducted)}"})
            parent["subtotal"] = q(parent["subtotal"] + q(fee))
            parent["flags"] = sorted(set(parent["flags"]) | {"group_applied"})

        elif g.kind in ("equal_split", "per_entity_min") and floor_amt is not None:
            n = (g.units if g.kind == "per_entity_min" and g.units else len(members)) or 1
            each = q(floor_amt / n)
            remainder = q(floor_amt - each * n) if g.kind == "equal_split" else D("0")
            for i, m in enumerate(have):
                r = results[m.account_id]
                floor = each + (remainder if i == 0 else D("0"))
                if r["subtotal"] < floor:
                    top = q(floor - r["subtotal"])
                    r["lines"].append({"component_type": "mg_topup", "label": f"Group minimum share — {g.name}", "amount": top, "group": "MG", "flags": [],
                                       "detail": f"{engine2.fmt(floor_amt)} ÷ {n} = {engine2.fmt(floor)} floor"})
                    r["subtotal"] = q(r["subtotal"] + top)

        elif g.kind == "pooled" and floor_amt is not None:
            total = sum((results[m.account_id]["subtotal"] for m in have), D("0"))
            if total < floor_amt:
                target = results[(parent_m or have[0]).account_id]
                top = q(floor_amt - total)
                target["lines"].append({"component_type": "mg_topup", "label": f"Pooled group minimum — {g.name}", "amount": top, "group": "MG", "flags": [],
                                        "detail": f"Group minimum {engine2.fmt(floor_amt)} − members' combined {engine2.fmt(total)}"})
                target["subtotal"] = q(target["subtotal"] + top)


def group_membership(db: Session) -> dict[int, BillingGroup]:
    groups = {g.id: g for g in db.query(BillingGroup).all()}
    return {m.account_id: groups[m.group_id] for m in db.query(BillingGroupMember).all() if m.group_id in groups}


# ------------------------------- validators (spec §10) -------------------------------

def _ex(out, code, severity, message, blocks=False):
    out.append({"code": code, "severity": severity, "message": message, "blocks": blocks})


def validate_account(db: Session, r: dict, period: str) -> list[dict]:
    out: list[dict] = []
    acct: BillingAccount = r["account"]
    life, flags = r["life"], set(r["flags"])
    name = acct.display_name
    covered = bool(r["subs"])

    if any(e.enterprise_id.startswith(("UNMAPPED", "ROW-")) for e in acct.enterprises):
        _ex(out, "ACCOUNT_NOT_MAPPED", "high", f"{name}: no platform enterprise_id is mapped yet — usage cannot be matched. Confirm the mapping before billing.", True)
    if "pending_check" in life["flags"]:
        _ex(out, "PENDING_CHECK", "high", f"{name}: marked 'pending check' — resolve before billing.", True)
    if "unmodelled" in flags:
        txt = next((l["detail"] for l in r["lines"] if "unmodelled" in l.get("flags", [])), "")
        _ex(out, "UNMODELLED_COMPONENT", "high", f"{name}: a charge could not be modelled and would bill ₹0 ({txt[:120]}). Model it before billing.", True)
    for f in flags:
        if f.startswith("input_missing:"):
            field = f.split(":", 1)[1]
            sev = "high" if field in ("upi_spend_after_free", "upi_amount_split", "card_share_pct", "truck_count", "driver_count") else "medium"
            _ex(out, "INPUT_MISSING", sev, f"{name}: needs '{field}' for this period but none was supplied — that charge is ₹0. Enter it as a manual usage input or extend the usage query.")
    if "floored_at_zero" in flags:
        _ex(out, "NEGATIVE_COMPONENT", "medium", f"{name}: a component computed below zero and was floored at ₹0 (free allowance exceeds usage or credit exceeds base).")
    if r["manual"]:
        _ex(out, "MANUAL_INPUT", "medium", f"{name}: usage typed by hand for {', '.join(r['manual'])} (no system snapshot).")
    if any(c["component_type"] == "load_percent_tiered" and (c.get("params") or {}).get("mode", "whole") == "whole" for c in r["components"]) and (acct.import_flags is not None) and any(
            c.source == "import" for c in [r["contract"]]):
        _ex(out, "TIER_MODE_ASSUMED", "info", f"{name}: slab contract applied as whole-amount (the slab reached applies to all of Add Money). Switch to marginal on the contract if the agreement says band-wise.")
    if "approximation" in flags:
        _ex(out, "APPROXIMATE_CALC", "medium", f"{name}: UPI conditional fee is approximated from monthly totals — verify against per-transaction data.")
    if "cap_applied" in flags:
        _ex(out, "CAP_APPLIED", "info", f"{name}: monthly cap reduced the invoice.")
    if covered and not any(e.event_type in ("annual_billing", "quarterly_billing") for e in acct.events):
        _ex(out, "SUBSCRIPTION_COVERAGE_ASSUMED", "medium", f"{name}: the platform fee is treated as covered by a subscription invoice, but the account is not marked annual/quarterly — confirm the subscription covers it.")
    if "waiver_unapproved" in flags:
        _ex(out, "WAIVER_NO_APPROVAL", "medium", f"{name}: fee waived without an approval reference.")
    if life["fraction"] < 1 and life["billable"] and r["contract"].overall_min_amount:
        _ex(out, "PRORATA_MG_POLICY", "medium", f"{name}: partial month ({life['active_days']}/{life['days_in_month']} days) with a minimum — minimum is {'pro-rated' if r['contract'].prorate_mg else 'NOT pro-rated'} per contract setting; confirm policy.")
    if any("tiered slabs" in str(f).lower() for f in (acct.import_flags or [])) and not any(c["component_type"] == "load_percent_tiered" for c in r["components"]):
        _ex(out, "TIER_NOT_APPLIED", "medium", f"{name}: contract has tiered slabs but only a single rate is modelled — it under/over-bills when Add Money crosses a slab.")
    taxable = r["subtotal"]
    has_commit = any(c["component_type"] in ("fixed_saas", "fixed_amount", "fixed_saas_gst_inclusive", "bbps_fee", "module_package") for c in r["components"]) or bool(r["contract"].overall_min_amount)
    waived = "waived" in flags
    if life["billable"] and taxable == 0 and has_commit and not covered and not waived and "mg_deferred" not in flags:
        _ex(out, "ZERO_WITH_COMMITMENT", "medium", f"{name}: bills ₹0 although a fixed fee or minimum exists.")
    if life["billable"] and taxable == 0 and (r["usage"].get("add_money") or 0) > 0 and not covered and not waived:
        _ex(out, "LOADED_BUT_ZERO", "high", f"{name}: funds were added ({engine2.fmt(dec(r['usage'].get('add_money')))}) but the invoice is ₹0 and no subscription covers the month.")
    if not life["billable"] and taxable > 0:
        _ex(out, "NONBILLABLE_BUT_AMOUNT", "medium", f"{name}: not billable ({life['reason']}) but the contract would bill {engine2.fmt(taxable)} — withheld.")
    if not r["seen"] and has_commit and life["billable"]:
        _ex(out, "LOOKUP_MISSING_WITH_COMMITMENT", "medium", f"{name}: no usage found for its enterprise(s) — fixed fee / minimum billed regardless. Confirm the enterprise mapping.")
    return out


def subscription_checks(db: Session, period: str, accounts: list[BillingAccount], subs_by_account: dict | None = None) -> list[dict]:
    start, end, _ = period_bounds(period)
    out: list[dict] = []
    nxt = (end + datetime.timedelta(days=1))
    next_end = datetime.date(nxt.year, nxt.month, calendar.monthrange(nxt.year, nxt.month)[1])
    freq_months = {"annual": 12, "quarterly": 3, "half_yearly": 6}
    for a in accounts:
        if subs_by_account is not None:
            subs = sorted(subs_by_account.get(a.id, []), key=lambda x: (x.period_start or "", x.id))
        else:
            subs = db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == a.id, SubscriptionInvoice.status == "raised").order_by(SubscriptionInvoice.period_start).all()
        life = lifecycle(a, period)
        periodic = any(e.event_type in ("annual_billing", "quarterly_billing") for e in a.events)
        if periodic and not subs and life["billable"]:
            _ex(out, "ANNUAL_WITHOUT_INVOICE", "medium", f"{a.display_name}: marked annual/quarterly but no subscription invoice is on record.")
        for i, s in enumerate(subs):
            sa, sb = _d(s.period_start), _d(s.period_end)
            if sa and sb:
                months = (sb.year - sa.year) * 12 + sb.month - sa.month + 1
                want = freq_months.get(s.frequency)
                if want and months != want:
                    _ex(out, "PERIOD_TEXT_ANOMALY", "low", f"{a.display_name}: {s.frequency} invoice covers {months} months ({s.period_start} → {s.period_end}).")
                for t in subs[i + 1:]:
                    ta, tb = _d(t.period_start), _d(t.period_end)
                    if ta and tb and ta <= sb and tb >= sa:
                        code = "SUBSCRIPTION_DOUBLE_BILL" if (ta, tb) == (sa, sb) else "SUBSCRIPTION_OVERLAP"
                        _ex(out, code, "high" if code == "SUBSCRIPTION_DOUBLE_BILL" else "medium", f"{a.display_name}: subscription invoices overlap ({s.period_start}→{s.period_end} and {t.period_start}→{t.period_end}).")
        if subs and life["billable"] and (periodic or subs):
            last = _d(subs[-1].period_end)
            if last and last < start and periodic:
                _ex(out, "RENEWAL_OVERDUE", "high", f"{a.display_name}: subscription ended {subs[-1].period_end} with no renewal on record.")
            elif last and start <= last <= next_end and periodic:
                _ex(out, "RENEWAL_DUE", "info", f"{a.display_name}: subscription renewal due (ends {subs[-1].period_end}).")
    for s in db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id.is_(None)).all():
        _ex(out, "ACCOUNT_NOT_IN_MASTER", "medium", f"Subscription invoice for '{s.particulars}' has no billing account.")
    return out


# ------------------------------- run-level -------------------------------

def compute_run(db: Session, period: str, snaps: dict[str, dict], clients_by_id: dict[int, Client]) -> dict:
    """Compute every v2 account. Returns {client_id: result} plus run-level exceptions."""
    accounts = db.query(BillingAccount).options(
        selectinload(BillingAccount.enterprises), selectinload(BillingAccount.events),
        selectinload(BillingAccount.contracts).selectinload(Contract.components)).all()
    subs_by_account = raised_subscriptions(db)
    membership = group_membership(db)
    results: dict[int, dict] = {}
    by_account: dict[int, dict] = {}
    for a in accounts:
        g = membership.get(a.id)
        ignore = bool(g and g.kind in ("equal_split", "per_entity_min", "pooled") and g.group_min_amount is not None)
        r = compute_account(db, a, period, snaps, ignore_overall_min=ignore, subs_by_account=subs_by_account)
        if r is not None:
            by_account[a.id] = r
    apply_groups(db, by_account)
    run_ex: list[dict] = []
    for aid, r in by_account.items():
        r["exceptions"] = validate_account(db, r, period)
        r["blocked"] = any(e["blocks"] for e in r["exceptions"])
        r["billable"] = r["life"]["billable"]
        results[r["account"].client_id] = r
    run_ex += subscription_checks(db, period, accounts, subs_by_account)
    # systemic: a usage column the feed doesn't supply while many accounts need it
    for field in ("maker_count", "admin_count"):
        needing = [r for r in by_account.values() if any(f"input_missing:{field}" in l.get("flags", []) for l in r["lines"])]
        if len(needing) >= 3:
            _ex(run_ex, "USAGE_FIELD_MISSING", "high", f"The usage feed does not supply '{field}' — {len(needing)} accounts with user-based charges would under-bill. Update the usage query.")
    return {"results": results, "run_exceptions": run_ex}


def apply_escalations(db: Session, through: str, user_email: str) -> list[dict]:
    """Create DRAFT contract versions with escalated prices for every rule due on or before `through`.
    History is never mutated; Finance reviews and locks each draft."""
    from app.models import ContractComponent, EscalationRule
    created = []
    for r in db.query(EscalationRule).filter(EscalationRule.active.is_(True), EscalationRule.next_date <= through).all():
        a = db.query(BillingAccount).get(r.account_id)
        base = active_contract(a, r.next_date) or max((c for c in a.contracts if c.status in ("locked", "superseded")), key=lambda c: c.version, default=None)
        if base is None or any(c.status == "draft" and c.source == "escalation" and c.valid_from == r.next_date for c in a.contracts):
            continue
        label = f"+₹{r.value:g}" if r.kind == "fixed_increment" else f"+{r.value * 100:g}%"
        nv = Contract(account_id=a.id, version=max(c.version for c in a.contracts) + 1, valid_from=r.next_date, status="draft", source="escalation",
                      created_by=user_email, notes=f"Escalation {label} from {r.next_date}", billing_frequency=base.billing_frequency, gst_mode=base.gst_mode,
                      gst_rate=base.gst_rate, overall_min_amount=base.overall_min_amount, overall_cap_amount=base.overall_cap_amount,
                      mg_effective_from=base.mg_effective_from, prorate_mg=base.prorate_mg, subscription_covers=base.subscription_covers)
        db.add(nv)
        db.flush()
        for k in base.components:
            params = engine2.scale_params(k.component_type, k.params or {}, r.kind, r.value) if r.applies_to in ("all", k.component_type) else dict(k.params or {})
            db.add(ContractComponent(contract_id=nv.id, component_type=k.component_type, label=k.label, params=params, sort_order=k.sort_order, valid_from=k.valid_from, valid_to=k.valid_to))
        r.last_applied_date = r.next_date
        nd = datetime.date.fromisoformat(r.next_date)
        r.next_date = nd.replace(year=nd.year + 1).isoformat()
        created.append({"account": a.display_name, "version": nv.version, "effective": nv.valid_from})
    db.commit()
    return created
