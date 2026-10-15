"""Billing engine v2 — typed, parameterised charge components.

Everything is data: a contract is a list of components ({component_type, params});
no customer-specific code. Each component is a pure function

    compute(params, usage, ctx) -> Result(amount, detail, flags)

Money uses Decimal and every line is rounded to 2 dp (half-up) before summing,
matching how Zoho rounds per line. Missing usage is treated as 0 (never an error);
inputs a component *needs* but the snapshot does not carry are reported as flags
so validators can raise an exception instead of silently under-billing.

Groups:  A = platform / SaaS / load fee, B = UPI fee, C = users / admins, D = module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

D = Decimal
ZERO = D("0")
CENT = D("0.01")


def dec(v, default=ZERO) -> Decimal:
    if v is None or v == "":
        return default
    try:
        return D(str(v))
    except Exception:
        return default


def q(v: Decimal) -> Decimal:
    return dec(v).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(v: Decimal) -> str:
    return f"₹{q(v):,.2f}"


@dataclass
class Result:
    amount: Decimal
    detail: str = ""
    flags: list[str] = field(default_factory=list)  # e.g. "input_missing:upi_spend_after_free", "unmodelled"
    qty: Decimal | None = None
    rate: Decimal | None = None


def _frac(params: dict, ctx: dict, default_prorate: bool) -> tuple[Decimal, str]:
    if not params.get("prorate", default_prorate):
        return D("1"), ""
    f = dec(ctx.get("active_fraction"), D("1"))
    if f >= D("1"):
        return D("1"), ""
    return f, f" × {ctx.get('active_days')}/{ctx.get('days_in_month')} days (pro-rata)"


def _u(usage: dict, key: str, default=ZERO) -> Decimal:
    return dec(usage.get(key), default)


# --------------------------------------------------------------------------
# A — platform / SaaS / load
# --------------------------------------------------------------------------

def fixed_saas(p, usage, ctx):
    base = dec(p.get("base_amount"))
    factors = [dec(x) for x in p.get("escalations_applied", [])]
    amount = base
    for f in factors:
        amount *= f
    detail = f"{fmt(base)}" + "".join(f" × {f.normalize()}" for f in factors)
    frac, note = _frac(p, ctx, True)
    return Result(amount * frac, detail + (f" = {fmt(amount)}" if factors else "") + note)


def fixed_amount(p, usage, ctx):
    frac, note = _frac(p, ctx, True)
    a = dec(p.get("amount"))
    return Result(a * frac, f"Fixed {fmt(a)}{note}")


def fixed_saas_gst_inclusive(p, usage, ctx):
    gross, rate = dec(p.get("gross_amount")), dec(ctx.get("gst_rate"), D("0.18"))
    return Result(gross / (1 + rate), f"{fmt(gross)} incl. GST ÷ {1 + rate}")


def load_percent(p, usage, ctx):
    L, rate, mn = _u(usage, "add_money"), dec(p.get("rate")), dec(p.get("min_amount"))
    raw = L * rate
    amount = max(raw, mn) if mn else raw
    frac, note = _frac(p, ctx, True)
    detail = f"{(rate * 100).normalize()}% × {fmt(L)} = {fmt(raw)}" + (f"; min {fmt(mn)}" if mn else "")
    return Result(amount * frac, detail + note, qty=L, rate=rate)


def _pick_tier(tiers: list[dict], value: Decimal) -> dict | None:
    for t in sorted(tiers, key=lambda t: (t.get("upto") is None, dec(t.get("upto")))):
        if t.get("upto") is None or value <= dec(t["upto"]):
            return t
    return tiers[-1] if tiers else None


def load_percent_tiered(p, usage, ctx):
    L, tiers = _u(usage, "add_money"), p.get("tiers", [])
    if not tiers:
        return Result(ZERO, "No tiers defined", ["unmodelled"])
    mode = p.get("mode", "whole")
    tier = _pick_tier(tiers, L)
    if mode == "marginal":
        total, prev = ZERO, ZERO
        for t in sorted(tiers, key=lambda t: (t.get("upto") is None, dec(t.get("upto")))):
            top = L if t.get("upto") is None else min(L, dec(t["upto"]))
            if top > prev:
                total += (top - prev) * dec(t.get("rate"))
            prev = max(prev, top)
            if L <= prev:
                break
        amount = max(total, dec(tier.get("min")))
        detail = f"Marginal slabs on {fmt(L)} = {fmt(total)}; min {fmt(dec(tier.get('min')))}"
    else:
        raw = L * dec(tier.get("rate"))
        amount = max(raw, dec(tier.get("min")))
        detail = f"Slab {(dec(tier.get('rate')) * 100).normalize()}% (whole amount) × {fmt(L)} = {fmt(raw)}; min {fmt(dec(tier.get('min')))}"
    frac, note = _frac(p, ctx, True)
    return Result(amount * frac, detail + note, qty=L)


def load_percent_plus_fixed(p, usage, ctx):
    L, rate, fixed = _u(usage, "add_money"), dec(p.get("rate")), dec(p.get("fixed"))
    frac, note = _frac(p, ctx, True)
    return Result((L * rate + fixed) * frac, f"{(rate * 100).normalize()}% × {fmt(L)} + {fmt(fixed)}{note}", qty=L, rate=rate)


def fixed_minus_upi_credit(p, usage, ctx):
    base, credit, S = dec(p.get("base")), dec(p.get("credit_per_upi_txn")), _u(usage, "upi_txn_count")
    raw = base - S * credit
    flags = ["floored_at_zero"] if raw < 0 else []
    frac, note = _frac(p, ctx, True)
    return Result(max(raw, ZERO) * frac, f"{fmt(base)} − {S} × {fmt(credit)} = {fmt(raw)}{note}", flags)


def per_unit_fleet(p, usage, ctx):
    unit = p.get("unit", "truck")
    key = "truck_count" if unit == "truck" else "driver_count"
    base, inc, rate = dec(p.get("base")), dec(p.get("included_units")), dec(p.get("rate_per_extra_unit"))
    if usage.get(key) is None:
        return Result(base, f"{fmt(base)} base; {unit} count not supplied", [f"input_missing:{key}"])
    n = _u(usage, key)
    extra = max(n - inc, ZERO)
    return Result(base + extra * rate, f"{fmt(base)} up to {inc} {unit}s + {extra} × {fmt(rate)}", qty=n, rate=rate)


def bbps_fee(p, usage, ctx):
    return Result(dec(p.get("amount")), f"BBPS fee {fmt(dec(p.get('amount')))}")


def waived(p, usage, ctx):
    return Result(ZERO, f"Waived: {p.get('reason', 'no reason recorded')}", ["waived"] + ([] if p.get("approval_ref") else ["waiver_unapproved"]))


# --------------------------------------------------------------------------
# B — UPI
# --------------------------------------------------------------------------

def _years_since(anchor: str | None, ctx: dict) -> int:
    if not anchor:
        return 0
    try:
        a, b = ctx["period_start"].replace(day=1), None
        import datetime as dt
        ad = dt.date.fromisoformat(anchor)
        years = a.year - ad.year - (1 if (a.month, a.day) < (ad.month, ad.day) else 0)
        return max(years, 0)
    except Exception:
        return 0


def upi_per_txn(p, usage, ctx):
    S, free, rate = _u(usage, "upi_txn_count"), dec(p.get("free_txns")), dec(p.get("rate"))
    esc = p.get("rate_escalation") or {}
    if esc:
        rate += dec(esc.get("increment_per_year")) * _years_since(esc.get("anchor_date"), ctx)
    chargeable = max(S - free, ZERO)
    return Result(chargeable * rate, f"({S} − {free} free) × {fmt(rate)}", qty=chargeable, rate=rate)


def upi_free(p, usage, ctx):
    return Result(ZERO, "UPI free")


def upi_amount_slab(p, usage, ctx):
    thr = dec(p.get("threshold"), D("2500"))
    if usage.get("upi_cnt_le_2500") is None or usage.get("upi_amt_gt_2500") is None:
        return Result(ZERO, "UPI split by amount not supplied", ["input_missing:upi_amount_split"])
    cnt, amt = _u(usage, "upi_cnt_le_2500"), _u(usage, "upi_amt_gt_2500")
    flat, pct = dec(p.get("flat_per_txn_upto")), dec(p.get("pct_above"))
    return Result(cnt * flat + amt * pct / 100,
                  f"{cnt} txns ≤ ₹{thr} × {fmt(flat)} + {pct}% × {fmt(amt)}", qty=cnt)


def upi_volume_slab(p, usage, ctx):
    S, slabs = _u(usage, "upi_txn_count"), p.get("slabs", [])
    if not slabs:
        return Result(ZERO, "No slabs defined", ["unmodelled"])
    ordered = sorted(slabs, key=lambda s: (s.get("upto_count") is None, dec(s.get("upto_count"))))
    if p.get("mode", "whole") == "marginal":
        total, prev = ZERO, ZERO
        for s in ordered:
            top = S if s.get("upto_count") is None else min(S, dec(s["upto_count"]))
            if top > prev:
                total += (top - prev) * dec(s.get("rate"))
            prev = max(prev, top)
        return Result(total, f"{S} txns, marginal slabs", qty=S)
    tier = next((s for s in ordered if s.get("upto_count") is None or S <= dec(s["upto_count"])), ordered[-1])
    return Result(S * dec(tier.get("rate")), f"{S} txns × {fmt(dec(tier.get('rate')))} (slab, whole)", qty=S, rate=dec(tier.get("rate")))


def spend_percent_after_free_txns(p, usage, ctx):
    if usage.get("upi_spend_after_free") is None:
        return Result(ZERO, "Spend after free transactions not supplied", ["input_missing:upi_spend_after_free"])
    spend, pct = _u(usage, "upi_spend_after_free"), dec(p.get("pct"))
    return Result(spend * pct / 100, f"{pct}% × spend {fmt(spend)} after {p.get('free_txns')} free txns", qty=spend)


def upi_conditional_pct(p, usage, ctx):
    share = usage.get("card_share_pct")
    if share is None:
        return Result(ZERO, "Card spend share not supplied", ["input_missing:card_share_pct"])
    if dec(share) >= dec(p.get("card_share_threshold"), D("75")):
        return Result(ZERO, f"Card share {share}% ≥ threshold — UPI free")
    S, amt = _u(usage, "upi_txn_count"), _u(usage, "upi_txn_amount")
    pct, mn = dec(p.get("pct"), D("0.5")), dec(p.get("min_per_txn"), D("1"))
    # per-transaction MAX(pct×amount, min) is approximated from totals (aggregate input only)
    return Result(max(amt * pct / 100, S * mn), f"Card share {share}% below threshold: max({pct}% × {fmt(amt)}, {S} × {fmt(mn)})", ["approximation"])


# --------------------------------------------------------------------------
# C — users / admins,  D — module
# --------------------------------------------------------------------------

def per_user(p, usage, ctx):
    flags = []
    if usage.get("maker_count") is None:
        flags.append("input_missing:maker_count")
    W, free, mn, rate = _u(usage, "maker_count"), dec(p.get("free_users")), dec(p.get("min_users")), dec(p.get("rate"))
    billable = max(W - free, mn)
    return Result(billable * rate, f"max({W} makers − {free} free, min {mn}) × {fmt(rate)}", flags, qty=billable, rate=rate)


def per_admin(p, usage, ctx):
    flags = [] if usage.get("admin_count") is not None else ["input_missing:admin_count"]
    AA, free, rate = _u(usage, "admin_count"), dec(p.get("free_admins")), dec(p.get("rate"))
    raw = AA - free
    if raw < 0:
        flags.append("floored_at_zero")
    billable = max(raw, ZERO)
    return Result(billable * rate, f"({AA} admins − {free} free) × {fmt(rate)}", flags, qty=billable, rate=rate)


def module_package(p, usage, ctx):
    units, price = dec(p.get("units")), dec(p.get("price"))
    return Result(units * price, f"{units} × {fmt(price)}", qty=units, rate=price)


def manual_formula(p, usage, ctx):
    return Result(ZERO, f"Not modelled: {p.get('rule', '')[:160]}", ["unmodelled"])


# type -> (group, function). Groups drive subscription coverage and presentation order.
COMPONENTS: dict[str, tuple[str, Callable]] = {
    "fixed_saas": ("A", fixed_saas), "fixed_amount": ("A", fixed_amount), "fixed_saas_gst_inclusive": ("A", fixed_saas_gst_inclusive),
    "load_percent": ("A", load_percent), "load_percent_tiered": ("A", load_percent_tiered),
    "load_percent_plus_fixed": ("A", load_percent_plus_fixed), "fixed_minus_upi_credit": ("A", fixed_minus_upi_credit),
    "per_unit_fleet": ("A", per_unit_fleet), "bbps_fee": ("A", bbps_fee), "waived": ("A", waived),
    "upi_per_txn": ("B", upi_per_txn), "upi_free": ("B", upi_free), "upi_amount_slab": ("B", upi_amount_slab),
    "upi_volume_slab": ("B", upi_volume_slab), "spend_percent_after_free_txns": ("B", spend_percent_after_free_txns),
    "upi_conditional_pct": ("B", upi_conditional_pct),
    "per_user": ("C", per_user), "per_admin": ("C", per_admin),
    "module_package": ("D", module_package),
    "manual_formula": ("A", manual_formula),
}

# Human-readable catalogue used by the UI to build forms (key -> label, params).
CATALOGUE = {
    "fixed_saas": ("Fixed SaaS fee", [("base_amount", "Base amount ₹", "number"), ("escalations_applied", "Escalation factors (e.g. 1.10)", "list")]),
    "fixed_amount": ("Fixed monthly fee", [("amount", "Amount ₹", "number")]),
    "fixed_saas_gst_inclusive": ("Fixed fee (GST inclusive)", [("gross_amount", "Gross amount incl. GST ₹", "number")]),
    "load_percent": ("% of Add Money", [("rate", "Rate (0.02 = 2%)", "number"), ("min_amount", "Minimum ₹", "number")]),
    "load_percent_tiered": ("% of Add Money — slabs", [("tiers", "Slabs [{upto, rate, min}]", "json"), ("mode", "whole | marginal", "text")]),
    "load_percent_plus_fixed": ("% of Add Money + fixed", [("rate", "Rate (0.002 = 0.2%)", "number"), ("fixed", "Fixed ₹", "number")]),
    "fixed_minus_upi_credit": ("Fixed fee less UPI credit", [("base", "Base ₹", "number"), ("credit_per_upi_txn", "Credit per UPI txn ₹", "number")]),
    "per_unit_fleet": ("Fleet: base + per extra unit", [("base", "Base ₹", "number"), ("included_units", "Included units", "number"), ("rate_per_extra_unit", "₹ per extra unit", "number"), ("unit", "truck | driver", "text")]),
    "bbps_fee": ("BBPS fee", [("amount", "Amount ₹", "number")]),
    "waived": ("Waived", [("reason", "Reason", "text"), ("approval_ref", "Approval ref", "text")]),
    "upi_per_txn": ("UPI per transaction", [("rate", "₹ per txn", "number"), ("free_txns", "Free txns / month", "number")]),
    "upi_free": ("UPI free", []),
    "upi_amount_slab": ("UPI by amount slab", [("flat_per_txn_upto", "Flat ₹ per txn", "number"), ("threshold", "Threshold ₹", "number"), ("pct_above", "% of value above threshold", "number")]),
    "upi_volume_slab": ("UPI volume slabs", [("slabs", "Slabs [{upto_count, rate}]", "json"), ("mode", "whole | marginal", "text")]),
    "spend_percent_after_free_txns": ("% of spend after N free txns", [("free_txns", "Free txns", "number"), ("pct", "% of spend", "number")]),
    "upi_conditional_pct": ("UPI % if card share low", [("card_share_threshold", "Card share threshold %", "number"), ("pct", "% of amount", "number"), ("min_per_txn", "Min ₹ per txn", "number")]),
    "per_user": ("Per user (makers)", [("rate", "₹ per user", "number"), ("min_users", "Minimum users", "number"), ("free_users", "Free users", "number")]),
    "per_admin": ("Per admin / co-admin", [("rate", "₹ per admin", "number"), ("free_admins", "Free admins", "number")]),
    "module_package": ("Module / package", [("units", "Units", "number"), ("price", "₹ per unit", "number")]),
    "manual_formula": ("Not modelled (needs review)", [("rule", "Rule text", "text")]),
}

# Price-bearing parameters per component type, used when escalating a contract.
PRICE_PARAMS = {
    "fixed_saas": ["base_amount"], "fixed_amount": ["amount"], "fixed_saas_gst_inclusive": ["gross_amount"],
    "load_percent": ["min_amount"], "load_percent_plus_fixed": ["fixed"], "fixed_minus_upi_credit": ["base"],
    "per_unit_fleet": ["base", "rate_per_extra_unit"], "bbps_fee": ["amount"], "upi_per_txn": ["rate"],
    "per_user": ["rate"], "per_admin": ["rate"], "module_package": ["price"], "upi_amount_slab": ["flat_per_txn_upto"],
}


def scale_params(component_type: str, params: dict, kind: str, value) -> dict:
    """Return params with price fields escalated (percent: × (1+v); fixed_increment: + v)."""
    out = dict(params)
    v = dec(value)
    for key in PRICE_PARAMS.get(component_type, []):
        if out.get(key) in (None, ""):
            continue
        cur = dec(out[key])
        new = cur * (1 + v) if kind == "percent" else cur + v
        out[key] = float(q(new))
    if component_type == "load_percent_tiered":
        out["tiers"] = [{**t, "min": float(q(dec(t["min"]) * (1 + v) if kind == "percent" else dec(t["min"]) + v))} if t.get("min") else t for t in out.get("tiers", [])]
    return out


# --------------------------------------------------------------------------
# Orchestration of one account's contract
# --------------------------------------------------------------------------

def compute_account(components: list[dict], usage: dict, ctx: dict, *, overall_min=None, overall_cap=None,
                    mg_effective_from=None, prorate_mg: bool = False, covered_groups: set[str] | None = None) -> dict:
    """components: [{component_type, label?, params, sort_order?}]. Returns lines, subtotal, flags, trace."""
    covered_groups = covered_groups or set()
    lines, flags = [], []
    for comp in sorted(components, key=lambda c: c.get("sort_order", 0)):
        ctype = comp["component_type"]
        if ctype not in COMPONENTS:
            lines.append({"component_type": ctype, "label": comp.get("label") or ctype, "amount": ZERO, "detail": f"Unknown component type '{ctype}'", "flags": ["unmodelled"], "group": "A"})
            flags.append("unmodelled")
            continue
        group, fn = COMPONENTS[ctype]
        label = comp.get("label") or CATALOGUE.get(ctype, (ctype,))[0]
        if group in covered_groups:
            lines.append({"component_type": ctype, "label": label, "amount": ZERO, "detail": "Covered by an active subscription invoice", "flags": ["covered_by_subscription"], "group": group})
            continue
        r = fn(comp.get("params") or {}, usage, ctx)
        amt = q(r.amount)
        lines.append({"component_type": ctype, "label": label, "amount": amt, "detail": r.detail, "flags": r.flags, "group": group,
                      "qty": r.qty, "rate": r.rate, "params": comp.get("params") or {}})
        flags += r.flags

    subtotal = sum((l["amount"] for l in lines), ZERO)
    mg_applied = None
    start = ctx.get("period_start")
    if overall_min and not covered_groups:
        eff_ok = not mg_effective_from or (start and start >= mg_effective_from)
        if eff_ok:
            floor = dec(overall_min)
            if prorate_mg:
                floor = q(floor * dec(ctx.get("active_fraction"), D("1")))
            if subtotal < floor:
                top = q(floor - subtotal)
                lines.append({"component_type": "mg_topup", "label": "Minimum commitment top-up", "amount": top, "group": "MG",
                              "detail": f"Overall minimum {fmt(floor)} − usage charges {fmt(subtotal)}", "flags": []})
                subtotal += top
                mg_applied = float(floor)
        else:
            flags.append("mg_deferred")
    if overall_cap and subtotal > dec(overall_cap):
        cut = q(subtotal - dec(overall_cap))
        lines.append({"component_type": "cap_adjustment", "label": "Monthly cap adjustment", "amount": -cut, "group": "CAP",
                      "detail": f"Capped at {fmt(dec(overall_cap))}", "flags": ["cap_applied"]})
        subtotal -= cut
        flags.append("cap_applied")
    return {"lines": lines, "subtotal": q(subtotal), "flags": sorted(set(flags)), "mg_applied": mg_applied}
