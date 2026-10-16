"""Pull the customer master from Zoho Books: link each billing account to its existing Zoho customer and fill in the
GSTIN, PAN, state and e-mail that invoices need. Only exact matches are applied; near matches come back as suggestions."""
from __future__ import annotations

import difflib
import json
import re

from sqlalchemy.orm import Session

from app.models import BillingAccount, Client, Setting
from app.services import gst_states, zoho

STATE_KEY = "ZOHO_STATE_MAP"
_NOISE = re.compile(r"\b(private|pvt|limited|ltd|llp|llc|inc|co|company|corporation|corp|the|and)\b|[^a-z0-9 ]")


def normalise(name: str) -> str:
    """'ABC Foods Pvt. Ltd.' and 'abc foods private limited' compare equal."""
    return " ".join(_NOISE.sub(" ", (name or "").lower().replace("&", " and ")).split())


def similarity(a: str, b: str) -> float:
    """0..1. Beyond character similarity, 'Gamma Parent' vs 'Gamma Parent Holdings' (all words of one inside the other) counts as close."""
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return max(ratio, 0.9 if len(short) >= 2 and short <= long_ else 0.0)


def learned_state_map(db: Session) -> dict[str, str]:
    row = db.query(Setting).filter(Setting.key == STATE_KEY).first()
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _save_state_map(db: Session, mapping: dict[str, str]) -> None:
    row = db.query(Setting).filter(Setting.key == STATE_KEY).first()
    if row is None:
        db.add(Setting(key=STATE_KEY, value=json.dumps(mapping)))
    else:
        row.value = json.dumps(mapping)


def pull_customers(db: Session, customers=None, apply: bool = True) -> dict:
    """customers: Zoho contact dicts (defaults to reading them live). With apply=False nothing is written."""
    zoho_rows = list(customers if customers is not None else zoho.list_customers())
    by_gst, by_name = {}, {}
    for c in zoho_rows:
        if c.get("gst_no"):
            by_gst.setdefault(c["gst_no"].strip().upper(), c)
        for n in (c.get("contact_name"), c.get("company_name")):
            if n:
                by_name.setdefault(normalise(n), c)

    learned = learned_state_map(db)
    new_states = dict(learned)
    for c in zoho_rows:  # Zoho's own spelling of each state, taught by customers that have both a GSTIN and a place of contact
        code = gst_states.state_code_from_gstin(c.get("gst_no") or "")
        if code and c.get("place_of_contact"):
            new_states[code] = c["place_of_contact"]
    if apply and new_states != learned:
        _save_state_map(db, new_states)

    report = {"zoho_customers": len(zoho_rows), "linked_by_gstin": 0, "linked_by_name": 0, "already_linked": 0, "filled": {"gst": 0, "state": 0, "email": 0, "pan": 0},
              "suggestions": [], "unmatched_accounts": [], "states_learned": len(new_states) - len(learned), "applied": apply}
    used: set[str] = set()
    zoho_names = list(by_name.keys())
    for acct in db.query(BillingAccount).all():
        client: Client = acct.client
        match, how = None, ""
        if client.gst and client.gst.strip().upper() in by_gst:
            match, how = by_gst[client.gst.strip().upper()], "gstin"
        else:
            for candidate in (acct.zoho_billing_name, acct.display_name, client.name):
                if candidate and normalise(candidate) in by_name:
                    match, how = by_name[normalise(candidate)], "name"
                    break
        if match is None:
            best = max(((similarity(normalise(acct.zoho_billing_name or acct.display_name), z), z) for z in zoho_names), default=(0, ""))
            if best[0] >= 0.88:
                report["suggestions"].append({"account": acct.display_name, "zoho_customer": by_name[best[1]]["contact_name"], "score": round(best[0], 2)})
            else:
                report["unmatched_accounts"].append(acct.display_name)
            continue
        used.add(match["contact_id"])
        if client.zoho_contact_id == match["contact_id"]:
            report["already_linked"] += 1
        else:
            report["linked_by_" + how] += 1
        if not apply:
            continue
        client.zoho_contact_id = match["contact_id"]
        gst = (match.get("gst_no") or "").strip().upper()
        if gst and not client.gst:
            client.gst, report["filled"]["gst"] = gst, report["filled"]["gst"] + 1
        if match.get("pan_no") and not client.pan:
            client.pan, report["filled"]["pan"] = match["pan_no"], report["filled"]["pan"] + 1
        code = gst_states.state_code_from_gstin(gst) or gst_states.gst_code_from_short(match.get("place_of_contact") or "", new_states)
        if code and not client.state_code:
            client.state_code, client.state = code, match.get("place_of_contact_formatted") or gst_states.NAMES.get(code, "")
            report["filled"]["state"] += 1
        if match.get("email") and not client.billing_contact_email:
            client.billing_contact_email, report["filled"]["email"] = match["email"], report["filled"]["email"] + 1
        if (client.gst_treatment or "registered") == "registered" and match.get("gst_treatment") and match["gst_treatment"] != "business_gst":
            client.gst_treatment = {"business_none": "unregistered", "consumer": "unregistered", "overseas": "overseas", "special_economic_zone": "sez"}.get(match["gst_treatment"], client.gst_treatment)
    report["unmatched_zoho"] = len([c for c in zoho_rows if c["contact_id"] not in used])
    report["unmatched_account_count"] = len(report["unmatched_accounts"])
    report["unmatched_accounts"] = report["unmatched_accounts"][:50]
    if apply:
        db.commit()
    return report
