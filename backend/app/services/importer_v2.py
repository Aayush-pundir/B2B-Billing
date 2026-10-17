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

from app.models import (BaselineAmount, AccountEnterprise, AccountEvent, BillingAccount, BillingGroup, BillingGroupMember, Client, Contract,
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

_LAKH = r"(?:lakhs?|lacs?|lac|l)\b"


def _amount(txt: str) -> float | None:
    """'₹15,000' / '15k' / '8K' / '50L' / '.75' -> float rupees (L = lakh)."""
    m = re.search(r"₹?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(k|lakhs?|lacs?|lac|l)?\b", txt.strip(), re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return v * 1000 if unit == "k" else v * 100000 if unit.startswith("l") else v


def _parse_slabs_rate_first(t: str) -> list[dict] | None:
    """'2% upto 10 lakh, 1.75% 11 lakh to 25 lakh, 1% 51 lakh & above' — each rate is followed by its own boundary."""
    parts = re.split(r"(?<![\d.])(?=\d+(?:\.\d+)?\s*%)", t)
    tiers, prev = [], 0.0
    for part in parts:
        m = re.match(r"(\d+(?:\.\d+)?)\s*%(.*)$", part.strip(), re.S)
        if not m:
            continue
        rate, rest = round(float(m.group(1)) / 100, 6), m.group(2)
        mn = re.search(r"\bor\s*([\d,.]+\s*k?)\b", rest, re.I)
        nums = [_amount(x.group(0)) for x in re.finditer(r"\d[\d,]*(?:\.\d+)?\s*" + _LAKH, rest, re.I)]
        nums = [n for n in nums if n]
        if re.search(r"above|beyond|&\s*above", rest, re.I):
            upto = None
        elif nums:
            upto = max(nums)
        else:
            return None
        if upto is not None:
            if upto <= prev:
                return None
            prev = upto
        tiers.append({"upto": upto, "rate": rate, "min": _amount(mn.group(1)) if mn else 0})
    if len(tiers) < 2 or any(x["upto"] is None for x in tiers[:-1]):
        return None
    tiers[-1]["upto"] = None
    return tiers


def parse_slabs(text: str) -> list[dict] | None:
    t0 = (text or "").replace("–", "-")
    first_rate, first_lakh = re.search(r"\d\s*%", t0), re.search(r"\d\s*" + _LAKH, t0, re.I)
    if first_rate and first_lakh and first_rate.start() < first_lakh.start():
        got = _parse_slabs_rate_first(t0.replace("₹", ""))
        if got:
            return got
    return _parse_slabs_boundary_first(text)


def _parse_slabs_boundary_first(text: str) -> list[dict] | None:
    """Parse contract slab wording like '0-10L 2%; 10-15L 1.8%; >15L 1.5%' or '1% or 15K ... above 50L 0.5% or 30K'
    into [{upto, rate, min}] (upto in rupees, None = unbounded). Returns None unless the result is unambiguous."""
    t = (text or "").replace("–", "-").replace("→", " ").replace("₹", "")
    t = re.sub(r"\bWIH\b|whichever is higher|\(whole[^)]*\)|\.\s*Sheet:.*$", " ", t, flags=re.I)
    # split into one segment per percentage
    pieces = re.split(r"(?<=%)(?:\s+or\s+[\d,.]+\s*k?)?\s*[;,]?\s*", t)
    segs = re.findall(r"([^%]*?)(\d+(?:\.\d+)?|\.\d+)\s*%(?:[^%\d]{0,40}?\bor\s*([\d,.]+\s*k?\b))?", t, flags=re.I)
    if len(segs) < 2:
        return None
    tiers, prev_upto = [], 0.0
    for lead, rate, mn in segs:
        lead = lead.strip(" ;,:→-")
        rate = round(float(rate) / 100, 6)
        above = re.search(r"above|>|beyond|and above|more than", lead, re.I)
        nums = [_amount(m.group(0)) for m in re.finditer(r"\d[\d,]*(?:\.\d+)?\s*" + _LAKH, lead, re.I)]
        nums = [n for n in nums if n]
        if above or (not nums and tiers and segs[-1][1] == str(rate * 100)):
            upto = None
        elif nums:
            upto = max(nums)
        else:
            return None
        tiers.append({"upto": upto, "rate": rate, "min": _amount(mn) if mn else 0})
        if upto is not None:
            if upto <= prev_upto:
                return None
            prev_upto = upto
    if tiers[-1]["upto"] is not None:  # the open-ended top slab must be last
        tiers[-1]["upto"] = None
    if any(t["upto"] is None for t in tiers[:-1]):
        return None
    return tiers


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
            slab_text = A.get("rate_M") if isinstance(A.get("rate_M"), str) else ""
            tiers = parse_slabs(slab_text) or parse_slabs(str(r.get("contract_text") or ""))
            if tiers:
                return [{"component_type": "load_percent_tiered", "label": "Platform fee — slabs on Add Money", "params": {"tiers": tiers, "mode": "whole"}}], ["slabs_parsed"]
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
        if base is None:
            tiers = parse_slabs(str(r.get("contract_text") or "")) or (parse_slabs(A.get("rate_M")) if isinstance(A.get("rate_M"), str) else None)
            if tiers:  # the sheet formula does not match the contract; the contract's slabs win
                return [{"component_type": "load_percent_tiered", "label": "Platform fee — slabs on Add Money", "params": {"tiers": tiers, "mode": "whole"}}], ["slabs_parsed"]
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
        m = re.search(r"₹\s*([\d,]+)(?: fixed)? (?:per month )?up to (\d+) (trucks?|drivers?)[;,]?.*?₹\s*([\d,]+)\s*/\s*(truck|driver)", rule, re.I)
        if m:
            return [{"component_type": "per_unit_fleet", "label": "Platform fee (fleet)", "params": {"base": float(m.group(1).replace(",", "")), "included_units": float(m.group(2)),
                    "rate_per_extra_unit": float(m.group(4).replace(",", "")), "unit": "truck" if m.group(3).lower().startswith("truck") else "driver"}}], notes
        if low.startswith("bbps fee"):
            return [{"component_type": "bbps_fee", "label": "BBPS fee", "params": {"amount": _money(rule)}}], notes
        if "add money × 0.2%" in low and "3,500" in rule:
            m = re.search(r"up to (\d+) trucks?; ₹\s*([\d,]+)/truck", rule)
            comps = [{"component_type": "load_percent", "label": "Platform fee — % of Add Money", "params": {"rate": 0.002}}]
            if m:
                comps.append({"component_type": "per_unit_fleet", "label": "Fleet fee", "params": {"base": 3500.0, "included_units": float(m.group(1)), "rate_per_extra_unit": float(m.group(2).replace(",", "")), "unit": "truck"}})
            else:
                comps.append({"component_type": "fixed_amount", "label": "Fixed fee", "params": {"amount": 3500.0}})
            return comps, []
        m = re.search(r"₹\s*([\d,]+) per month hard-typed \(incl\. ₹\s*([\d,]+) BBPS fee\)", rule, re.I)
        if m:
            tot, bbps = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
            return [{"component_type": "fixed_amount", "label": "Platform fee", "params": {"amount": tot - bbps}}, {"component_type": "bbps_fee", "label": "BBPS fee", "params": {"amount": bbps}}], notes
        if ("slab" in low or "tiered load fee" in low) and ("add money" in low or "contract slab" in low):
            contract_text = str(r.get("contract_text") or "")
            tiers = None if "tiered load fee" in low else (parse_slabs(contract_text) or parse_slabs(rule))
            if tiers is None and "tiered load fee" in low:  # '>₹60,00,000 → 0.70% of ENTIRE L; else 0.80%; minimum ₹30,000'
                m = re.search(r"L > ₹?([\d,]+) → ([\d.]+)% of ENTIRE L; else ([\d.]+)% of L; subject to minimum ₹?([\d,]+)", rule)
                if m:
                    cut, hi, lo, mn = float(m.group(1).replace(",", "")), float(m.group(2)) / 100, float(m.group(3)) / 100, float(m.group(4).replace(",", ""))
                    tiers = [{"upto": cut, "rate": lo, "min": mn}, {"upto": None, "rate": hi, "min": mn}]
            if tiers:
                return [{"component_type": "load_percent_tiered", "label": "Platform fee — slabs on Add Money", "params": {"tiers": tiers, "mode": "whole"}}], ["slabs_parsed"]
            return manual("tiered slabs could not be parsed")
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



def _legacy_baseline(r: dict) -> tuple[str, float] | None:
    """The previous system's last taxable value for this customer, from a '<mon><yy>_actuals' block (e.g. sep26_actuals)."""
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
    for key, block in r.items():
        m = re.fullmatch(r"([a-z]{3})(\d{2})_actuals", key)
        if m and m.group(1) in months and isinstance(block, dict) and isinstance(block.get("Taxable"), (int, float)) and block["Taxable"] > 0:
            return f"20{m.group(2)}-{months[m.group(1)]:02d}", float(block["Taxable"])
    return None


# ------------------------------------------------------------------ placeholders that can be resolved
_NOT_BILLABLE_STATUS = ("dropped", "demo", "not billable")


def _k(num: str, suffix: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if suffix and suffix.lower() == "k" else v


def _text_rate_and_minimum(text: str) -> tuple[float, float | None] | None:
    """'2% of the funds added or 7K whichever is higher' / '1.5% or 5000 MG from 5th month' -> (0.02, 7000.0)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text or "")
    if not m:
        return None
    rest = text[m.end():]
    mn = re.search(r"\bor\s*(?:rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)\s*([kK])?(?![\d%])", rest, re.I)
    return float(m.group(1)) / 100, (_k(mn.group(1), mn.group(2)) if mn else None)


def _clear_placeholders(r: dict, comps: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Resolve 'needs modelling' placeholders the data itself settles. Returns (components, extra tags, assumptions).
    Anything interpreted rather than read is returned as an assumption for Finance to confirm; only genuinely
    undecidable wording keeps its placeholder (which keeps that invoice withheld)."""
    status = (r.get("status") or "").lower()
    A = r.get("A_platform") or {}
    rule, rate_txt, contract = A.get("rule") or "", A.get("rate_M") if isinstance(A.get("rate_M"), str) else "", str(r.get("contract_text") or "")
    notes = " ".join(str(n.get("cat", "")) for n in (r.get("sheet_notes") or [])).lower()
    out: list[dict] = []
    tags: list[str] = []
    assume: list[str] = []
    for c in comps:
        if c["component_type"] != "manual_formula" or "Platform fee" not in c["label"]:
            out.append(c)
            continue
        why = (c["params"].get("rule") or "").lower()
        source = " ".join(x for x in (rate_txt, contract) if x)
        if any(w in status for w in _NOT_BILLABLE_STATUS):
            tags.append("terms_not_captured")  # dropped / demo: nothing will ever bill, so there is nothing to model
            continue
        if "rate is not numeric" in why and (hit := _text_rate_and_minimum(rate_txt)):  # the rate cell only: contract text may hold UPI percentages
            rate, mn = hit
            p = {"rate": rate}
            note = None
            if mn and re.search(r"mg from (\d+)\w* month", source, re.I):
                month_no = re.search(r"mg from (\d+)", source, re.I).group(1)
                note = f"The minimum ₹{mn:,.0f} starts from month {month_no} of service; the go-live date is not on file, so set 'Minimum applies from' on the contract."
            elif mn:
                p["min_amount"] = mn
            out.append({"component_type": "load_percent", "label": "Platform fee — % of Add Money", "params": p})
            if note:
                assume.append(note)
            continue
        fleet = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*/\s*per\s*(truck|driver)", rate_txt, re.I) if "rate is not numeric" in why else None
        if fleet:
            out.append({"component_type": "per_unit_fleet", "label": "Fleet fee", "params": {"base": 0.0, "included_units": 0.0, "rate_per_extra_unit": float(fleet.group(1).replace(",", "")), "unit": fleet.group(2).lower()}})
            clause = re.search(r"after\s*(\d+)\s*cr\w*\s*([\d,]+)\s*/\s*cr", rate_txt, re.I)
            if clause:  # genuinely ambiguous wording: keep it visible and keep the invoice withheld until Finance decides
                out.append({"component_type": "manual_formula", "label": "Platform fee (needs modelling)", "params": {
                    "rule": f"load-fee clause '{clause.group(0)}' (maybe ₹{clause.group(2)} per crore of Add Money above ₹{clause.group(1)} crore): confirm the meaning"}})
            continue
        if re.search(r"annual|periodic|quarterly|half", status + " " + notes):
            tags.append("fee_via_subscription")  # the platform fee is a subscription invoice on the register, not a monthly line
            continue
        if re.search(r"no base amount|rate is not numeric", why):
            assume.append("The legacy sheet has no platform fee for this customer (it billed ₹0 for it). Billed on usage only. Confirm there is no fixed or percentage fee in the contract.")
            continue
        if "fleet fee not coded" in why or "unrecognised platform-fee rule" in why:
            m = re.search(r"₹\s*([\d,]+)\s*(?:per month|monthly)?\s*up to\s*(\d+)\s*(trucks?|drivers?)", rule + " " + contract, re.I)
            if m and "/truck" not in rule.lower() and "/driver" not in rule.lower():
                out.append({"component_type": "fixed_amount", "label": "Platform fee", "params": {"amount": float(m.group(1).replace(",", ""))}})
                assume.append(f"The contract says ₹{m.group(1)} a month 'up to {m.group(2)} {m.group(3)}' and states no rate above that, so it is billed as a flat ₹{m.group(1)}. Confirm what applies above {m.group(2)}.")
                continue
            m = re.search(r"₹\s*([\d,]+)\s*per\s*truck\s*/\s*driver", rule, re.I)
            if m:
                out.append({"component_type": "per_unit_fleet", "label": "Fleet fee", "params": {"base": 0.0, "included_units": 0.0, "rate_per_extra_unit": float(m.group(1).replace(",", "")), "unit": "truck"}})
                assume.append("'Per truck / driver' is billed on the truck count. Confirm whether drivers should be counted too (the usage feed can supply either).")
                continue
        out.append(c)
    return out, tags, assume


def _fix_related_usage_components(r: dict, comps: list[dict]) -> tuple[list[dict], list[str]]:
    """Wordings that live next to the platform fee but belong to the UPI charge."""
    assume: list[str] = []
    A = r.get("A_platform") or {}
    text = " ".join(str(x) for x in (A.get("rule"), r.get("sheet_remarks"), r.get("contract_text")) if x)
    out = list(comps)
    m = re.search(r"([\d,]+)\s*txns? free each", text, re.I) or re.search(r"([\d,]+)\s*free every month", text, re.I)
    if m:
        free = float(m.group(1).replace(",", ""))
        for c in out:
            if c["component_type"] == "upi_per_txn" and not c["params"].get("free_txns"):
                c["params"]["free_txns"] = free
                if re.search(r"collective", text, re.I):
                    assume.append(f"{free:,.0f} UPI transactions are free for each entity. The contract also mentions a collective threshold shared with a sister entity; that pooling is not modelled, so each entity is billed on its own.")
    if re.search(r"UPI\s*₹?\s*(\d+(?:\.\d+)?)\b", text) and not any(c["component_type"].startswith("upi") and c["component_type"] != "upi_free" for c in out) and (r.get("B_upi") or {}).get("type") in (None, "None", "Free"):
        rate = float(re.search(r"UPI\s*₹?\s*(\d+(?:\.\d+)?)\b", text).group(1))
        out = [c for c in out if c["component_type"] != "upi_free"]
        out.append({"component_type": "upi_per_txn", "label": "UPI fee", "params": {"rate": rate, "free_txns": 0}})
        assume.append(f"The contract has a UPI fee of ₹{rate:g} per transaction that the legacy sheet never billed. It is billed now; expect a higher invoice than before.")
    return out, assume


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
    by_row: dict[int, BillingAccount] = {}
    # one lookup up front instead of a query (and an implicit flush) per rulebook row; ids are resolved
    # through relationships so each row no longer needs its own flush either
    existing = {a.source_row: a for a in db.query(BillingAccount).filter(BillingAccount.source_row.isnot(None)).order_by(BillingAccount.id.desc())}
    with db.no_autoflush:
        created, skipped, updated, unmodelled = _import_rules_rows(db, lines, existing, by_row, user_email, lock, replace)
    db.flush()
    groups = _groups(db, lines, by_row)
    db.commit()
    return {"lines": len(lines), "accounts_created": created, "accounts_updated": updated, "skipped_existing": skipped,
            "unmodelled_components": unmodelled, "groups_created": groups, "contracts_locked": lock}


def _import_rules_rows(db, lines, existing, by_row, user_email, lock, replace) -> tuple[int, int, int, int]:
    created = skipped = updated = unmodelled = 0
    for r in lines:
        row = r.get("row")
        acct = existing.get(row)
        if acct and not replace:
            by_row[row] = acct
            skipped += 1
            continue
        name = (r.get("customer") or r.get("zoho_name") or f"Row {row}").strip()
        status = r.get("status") or ""
        if acct is None:
            client = Client(enterprise_id=f"ROW-{row}", name=name, billing_frequency="monthly",
                            status="inactive" if status in ("Dropped", "Demo – not billable") else "active")
            acct = BillingAccount(client=client, display_name=name, source_row=row)
            db.add(acct)
            db.add(AccountEnterprise(account=acct, enterprise_id=f"ROW-{row}", role="primary"))
            existing[row] = acct
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
        baseline = _legacy_baseline(r)
        if baseline:
            for old in db.query(BaselineAmount).filter(BaselineAmount.client_id == acct.client.id).all() if acct.client.id else []:
                db.delete(old)
            db.add(BaselineAmount(client=acct.client, period=baseline[0], taxable=baseline[1], source="legacy rulebook"))

        platform, tags = _platform(r)
        comps = [*platform, *_upi(r), *_users(r), *_module(r)]
        had_placeholder = any(c["component_type"] == "manual_formula" for c in comps)  # only these rows get the extra interpretation
        comps, more_tags, assumptions = _clear_placeholders(r, comps)
        tags = [*tags, *more_tags]
        if had_placeholder and "terms_not_captured" not in more_tags:
            comps, more_assumptions = _fix_related_usage_components(r, comps)
            assumptions = [*assumptions, *more_assumptions]
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
        contract = Contract(account=acct, version=1, valid_from=acct.go_live_date or "2020-01-01", billing_frequency="monthly",
                            overall_min_amount=omin, mg_effective_from="2026-10-01" if "MG deferred" in status else None,
                            status="locked" if lock else "draft", source="import", created_by=user_email,
                            approved_by=user_email if lock else "", approved_at=datetime.datetime.utcnow() if lock else None,
                            assumptions=[{"text": t, "confirmed_by": "", "confirmed_at": None} for t in assumptions],
                            notes=f"Imported from legacy rulebook row {row}. {' '.join(tags)}".strip())
        db.add(contract)
        for i, c in enumerate(comps):
            db.add(ContractComponent(contract=contract, component_type=c["component_type"], label=c.get("label", ""), params=c["params"], sort_order=i))
        for e in _events(r):
            db.add(AccountEvent(account=acct, event_type=e["event_type"], effective_from=e.get("effective_from"), effective_to=e.get("effective_to"),
                                payload=e.get("payload") or {}, source_text=e.get("source_text", ""), created_by=user_email))
            if e["event_type"] == "no_auto_debit":
                acct.auto_debit = False
        if "escalated" in tags and r.get("go_live"):
            gl = datetime.date.fromisoformat(r["go_live"])
            nxt = gl.replace(year=max(gl.year + 1, datetime.date.today().year))
            if nxt < datetime.date.today():
                nxt = nxt.replace(year=nxt.year + 1)
            if acct.id is None:
                db.flush()  # the rule needs the account's id; only escalating accounts reach here
            if not db.query(EscalationRule).filter(EscalationRule.account_id == acct.id).first():
                db.add(EscalationRule(account_id=acct.id, applies_to="fixed_saas", kind="percent", value=0.10, anniversary_basis="go_live",
                                      next_date=nxt.isoformat(), note="10% yearly escalation (from the legacy sheet)"))
    return created, skipped, updated, unmodelled


def resolve_unmodelled(db: Session, text: str, user_email: str) -> dict:
    """Re-read the rulebook for accounts whose current contract still has a 'needs modelling' charge and issue a corrected
    DRAFT version for each one the importer can now settle. Locked versions are never touched; Finance reviews and locks the drafts."""
    lines = [json.loads(l) for l in text.splitlines() if l.strip()]
    by_row = {a.source_row: a for a in db.query(BillingAccount).filter(BillingAccount.source_row.isnot(None))}
    drafted, still_open, skipped_dropped, unchanged = [], [], 0, 0
    for r in lines:
        acct = by_row.get(r.get("row"))
        if acct is None or not acct.contracts:
            continue
        latest = max(acct.contracts, key=lambda c: c.version)
        if not any(k.component_type == "manual_formula" for k in latest.components):
            continue
        if any(w in (r.get("status") or "").lower() for w in _NOT_BILLABLE_STATUS):
            skipped_dropped += 1  # dropped / demo accounts never bill; nothing to correct
            continue
        platform, _ = _platform(r)
        comps, _, assumptions = _clear_placeholders(r, [*platform, *_upi(r), *_users(r), *_module(r)])
        comps, more = _fix_related_usage_components(r, comps)
        assumptions = [*assumptions, *more]
        if sum(c["component_type"] == "manual_formula" for c in comps) >= sum(k.component_type == "manual_formula" for k in latest.components) and \
                [c["component_type"] for c in comps] == [k.component_type for k in latest.components]:
            still_open.append({"row": r.get("row"), "name": acct.display_name})
            unchanged += 1
            continue
        new = Contract(account_id=acct.id, version=latest.version + 1, valid_from=latest.valid_from, billing_frequency=latest.billing_frequency,
                       gst_mode=latest.gst_mode, gst_rate=latest.gst_rate, overall_min_amount=latest.overall_min_amount, overall_cap_amount=latest.overall_cap_amount,
                       mg_effective_from=latest.mg_effective_from, prorate_mg=latest.prorate_mg, subscription_covers=latest.subscription_covers, status="draft", source="import",
                       assumptions=[{"text": t, "confirmed_by": "", "confirmed_at": None} for t in assumptions], created_by=user_email,
                       notes=f"Re-read from the legacy rulebook row {r.get('row')} to clear a charge that needed modelling.")
        db.add(new)
        db.flush()
        for i, c in enumerate(comps):
            db.add(ContractComponent(contract_id=new.id, component_type=c["component_type"], label=c.get("label", ""), params=c["params"], sort_order=i))
        item = {"row": r.get("row"), "name": acct.display_name, "version": new.version, "contract_id": new.id, "assumptions": len(assumptions),
                "still_has_unmodelled": any(c["component_type"] == "manual_formula" for c in comps)}
        (still_open if item["still_has_unmodelled"] else drafted).append(item)
    db.commit()
    return {"drafts_created": len([d for d in drafted]), "drafted": drafted, "still_open": still_open, "skipped_dropped": skipped_dropped}


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


def _slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24] or "CUSTOMER"


def create_missing_accounts(db: Session, user_email: str) -> dict:
    """Subscription invoices on the periodic register whose customer is not in the rulebook: create the customer and billing
    account (name variants of one customer are merged), link the invoices, and leave a DRAFT contract for Finance to complete
    with any usage-based charges, since the rulebook says nothing about them."""
    from app.services.zoho_sync import normalise, similarity
    orphans = db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id.is_(None)).order_by(SubscriptionInvoice.period_start).all()
    clusters: list[list[SubscriptionInvoice]] = []
    for inv in orphans:
        key = normalise(inv.particulars)
        for cl in clusters:
            if similarity(key, normalise(cl[0].particulars)) >= 0.88:
                cl.append(inv)
                break
        else:
            clusters.append([inv])
    existing = {normalise(a.display_name): a for a in db.query(BillingAccount).all()}
    created, linked_only = [], []
    for cl in clusters:
        newest = max(cl, key=lambda i: i.period_end or "")
        name = newest.particulars.strip()
        name = name.title() if name.isupper() else name           # 'PRABHA ENTERPRISES' -> 'Prabha Enterprises'
        acct = existing.get(normalise(name))
        history = "; ".join(f"{i.period_start or '?'} to {i.period_end or '?'}: ₹{i.amount:,.0f}" for i in cl)
        if acct is None:
            eid = f"UNMAPPED-{_slug(name)}"
            n = 1
            while db.query(Client).filter(Client.enterprise_id == eid).first():
                n += 1
                eid = f"UNMAPPED-{_slug(name)}-{n}"
            client = Client(enterprise_id=eid, name=name, billing_frequency="annual", status="active")
            db.add(client)
            db.flush()
            acct = BillingAccount(client_id=client.id, display_name=name, notes=f"Created from subscription invoices on the periodic register (not in the rulebook). Invoices: {history}.")
            db.add(acct)
            db.flush()
            db.add(AccountEnterprise(account_id=acct.id, enterprise_id=eid, role="primary"))
            db.add(AccountEvent(account_id=acct.id, event_type="annual_billing", effective_from=min((i.period_start for i in cl if i.period_start), default=None),
                                payload={}, source_text="Annual subscription on the periodic register", created_by=user_email))
            db.add(Contract(account_id=acct.id, version=1, valid_from=min((i.period_start for i in cl if i.period_start), default="2020-01-01"), status="draft", source="import",
                            created_by=user_email, notes="No rulebook row for this customer. The annual subscription is invoiced from the register. Add any usage-based charges the agreement has (UPI, users, load), then lock."))
            existing[normalise(name)] = acct
            created.append({"name": name, "invoices": len(cl), "variants": sorted({i.particulars for i in cl})})
        else:
            linked_only.append({"name": acct.display_name, "invoices": len(cl)})
        for inv in cl:
            inv.account_id = acct.id
    db.commit()
    return {"created": created, "linked_to_existing": linked_only, "invoices_linked": sum(len(c) for c in clusters)}


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
