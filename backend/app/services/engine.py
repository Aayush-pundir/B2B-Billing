"""
Slab billing engine.

A commercial is a list of "rules". Each rule independently produces zero or
more invoice line items from a client's monthly usage object:

    usage = {
        "loading": 5000000.0,
        "upi_transactions": 847,
        "upi_amount": 211750.0,
        "admin_users": 3,
        "spender_users": 12,
        "total_users": 15,
    }

Rule shape (all fields optional except metric/rate_type):
{
    "label": "Loading fee",
    "metric": "loading",                 # key into usage dict, or an addon code
    "rate_type": "percent" | "flat" | "per_unit" | "hybrid" | "annual_per_unit" | "addon",
    "slab_mode": "cliff" | "tiered" | "none",
    "rate_pct": 1.5,                      # percent rate_type
    "floor_amount": 5000,                 # percent/cliff floor
    "flat_amount": 10000,                 # flat rate_type
    "unit_rate": 100,                     # per_unit / annual_per_unit
    "minimum_qty": 5,                     # per_unit minimum billable qty
    "hybrid_fixed_amount": 2500,
    "hybrid_included_qty": 100,
    "hybrid_over_rate": 100,
    "tiers": [{"min": 700000, "max": 1000000, "rate_pct": 1.5, "floor": 8000}, ...],
    "invoice_as": "monthly" | "annual_lump",
    "escalation_pct_annual": 10,
}

Every rule produces a line: {label, metric, qty, rate_detail, amount}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LineItem:
    label: str
    metric: str
    qty: float
    calc_detail: str
    amount: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "metric": self.metric,
            "qty": self.qty,
            "calc_detail": self.calc_detail,
            "amount": round(self.amount, 2),
        }


def _find_tier(tiers: list[dict], qty: float) -> dict | None:
    """Find the matching tier for qty. If qty is below the lowest tier's min,
    fall back to the lowest tier (using its rate/floor as the catch-all) —
    mirrors the Gopala fix: a 3L loading client still gets *a* rate rather
    than falling through to the top tier.
    """
    if not tiers:
        return None
    sorted_tiers = sorted(tiers, key=lambda t: t.get("min", 0))
    for tier in sorted_tiers:
        lo = tier.get("min", 0)
        hi = tier.get("max")
        if qty >= lo and (hi is None or qty <= hi):
            return tier
    if qty < sorted_tiers[0].get("min", 0):
        return sorted_tiers[0]
    return sorted_tiers[-1]


def compute_rule(rule: dict, usage: dict[str, float]) -> LineItem | None:
    metric = rule.get("metric", "")
    rate_type = rule.get("rate_type", "flat")
    slab_mode = rule.get("slab_mode", "none")
    label = rule.get("label") or metric or rate_type
    qty = float(usage.get(metric, 0) or 0)

    if rate_type == "flat":
        amount = float(rule.get("flat_amount", 0))
        return LineItem(label, metric, qty, f"Flat fee ₹{amount:,.2f}", amount)

    if rate_type == "percent" and slab_mode == "tiered":
        tier = _find_tier(rule.get("tiers", []), qty)
        if tier is None:
            return LineItem(label, metric, qty, "No tier matched", 0.0)
        rate_pct = float(tier.get("rate_pct", 0))
        floor = float(tier.get("floor", 0))
        computed = qty * rate_pct / 100.0
        amount = max(computed, floor)
        detail = f"{rate_pct}% × ₹{qty:,.2f} = ₹{computed:,.2f}; floor ₹{floor:,.2f} -> ₹{amount:,.2f}"
        return LineItem(label, metric, qty, detail, amount)

    if rate_type == "percent":  # cliff / flat-floor percent
        rate_pct = float(rule.get("rate_pct", 0))
        floor = float(rule.get("floor_amount", 0))
        computed = qty * rate_pct / 100.0
        amount = max(computed, floor)
        detail = f"{rate_pct}% × ₹{qty:,.2f} = ₹{computed:,.2f}"
        if floor:
            detail += f"; floor ₹{floor:,.2f}"
        return LineItem(label, metric, qty, detail, amount)

    if rate_type == "per_unit":
        unit_rate = float(rule.get("unit_rate", 0))
        min_qty = float(rule.get("minimum_qty", 0))
        billable_qty = max(qty, min_qty)
        amount = billable_qty * unit_rate
        detail = f"{billable_qty:,.0f} units × ₹{unit_rate:,.2f}"
        if min_qty and qty < min_qty:
            detail += f" (minimum {min_qty:,.0f} enforced, actual {qty:,.0f})"
        return LineItem(label, metric, qty, detail, amount)

    if rate_type == "hybrid":
        fixed_amount = float(rule.get("hybrid_fixed_amount", 0))
        included_qty = float(rule.get("hybrid_included_qty", 0))
        over_rate = float(rule.get("hybrid_over_rate", 0))
        over_qty = max(0.0, qty - included_qty)
        amount = fixed_amount + over_qty * over_rate
        detail = f"₹{fixed_amount:,.2f} fixed upto {included_qty:,.0f}"
        if over_qty:
            detail += f" + {over_qty:,.0f} × ₹{over_rate:,.2f} above"
        return LineItem(label, metric, qty, detail, amount)

    if rate_type == "annual_per_unit":
        unit_rate = float(rule.get("unit_rate", 0))
        amount = qty * unit_rate
        detail = f"{qty:,.0f} units × ₹{unit_rate:,.2f}/year"
        return LineItem(label, metric, qty, detail, amount)

    if rate_type == "addon":
        addon_mode = rule.get("addon_mode", "flat")
        if addon_mode == "per_unit":
            unit_rate = float(rule.get("unit_rate", 0))
            amount = qty * unit_rate
            detail = f"{qty:,.0f} × ₹{unit_rate:,.2f}"
        else:
            amount = float(rule.get("flat_amount", 0))
            detail = f"Add-on flat ₹{amount:,.2f}"
        return LineItem(label, metric, qty, detail, amount)

    return LineItem(label, metric, qty, "Unrecognised rule", 0.0)


def compute_commercial(rules: list[dict], usage: dict[str, float], is_annual_due: bool = True) -> list[LineItem]:
    """Run every rule against usage. Rules marked invoice_as=annual_lump are
    skipped unless is_annual_due is True (the client's anniversary month).
    """
    lines: list[LineItem] = []
    for rule in rules:
        if rule.get("invoice_as") == "annual_lump" and not is_annual_due:
            continue
        line = compute_rule(rule, usage)
        if line is not None:
            lines.append(line)
    return lines


def escalate_rules(rules: list[dict], pct: float) -> list[dict]:
    """Bump every rate/floor/flat value across all rules by pct%. Used for
    annual escalation clauses (e.g. "increased by 10% every year")."""
    factor = 1 + pct / 100.0
    new_rules = []
    for rule in rules:
        r = dict(rule)
        for key in ("rate_pct", "floor_amount", "flat_amount", "unit_rate",
                    "hybrid_fixed_amount", "hybrid_over_rate"):
            if key in r and r[key] is not None:
                r[key] = round(float(r[key]) * factor, 4)
        if r.get("tiers"):
            new_tiers = []
            for tier in r["tiers"]:
                t = dict(tier)
                for key in ("rate_pct", "floor"):
                    if key in t and t[key] is not None:
                        t[key] = round(float(t[key]) * factor, 4)
                new_tiers.append(t)
            r["tiers"] = new_tiers
        new_rules.append(r)
    return new_rules


def gst_breakup(subtotal: float, client_state_code: str, org_state_code: str, gst_rate: float) -> dict:
    if client_state_code and client_state_code == org_state_code:
        half = gst_rate / 2.0
        cgst = round(subtotal * half / 100.0, 2)
        sgst = round(subtotal * half / 100.0, 2)
        return {"gst_type": "CGST_SGST", "cgst": cgst, "sgst": sgst, "igst": 0.0, "gst_total": round(cgst + sgst, 2)}
    igst = round(subtotal * gst_rate / 100.0, 2)
    return {"gst_type": "IGST", "cgst": 0.0, "sgst": 0.0, "igst": igst, "gst_total": igst}


def compute_invoice(rules: list[dict], usage: dict[str, float], client_state_code: str,
                     org_state_code: str, gst_rate: float, tds_rate: float = 0.0,
                     is_annual_due: bool = True) -> dict:
    lines = compute_commercial(rules, usage, is_annual_due=is_annual_due)
    subtotal = round(sum(l.amount for l in lines), 2)
    gst = gst_breakup(subtotal, client_state_code, org_state_code, gst_rate)
    tds_amount = round(subtotal * tds_rate / 100.0, 2)
    total = round(subtotal + gst["gst_total"] - tds_amount, 2)
    return {
        "lines": [l.to_dict() for l in lines],
        "subtotal": subtotal,
        "gst_type": gst["gst_type"],
        "cgst": gst["cgst"],
        "sgst": gst["sgst"],
        "igst": gst["igst"],
        "gst": gst["gst_total"],
        "tds": tds_amount,
        "total": total,
    }
