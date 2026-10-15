"""Zoho Books integration. Falls back to an offline simulation mode
(zb_sim_..., zc_sim_...) whenever ZOHO_REFRESH_TOKEN / ZOHO_ORG_ID are not
configured, so the rest of the app can be exercised without real credentials."""
import time
import uuid

import requests

from app.config import get_settings

_token_cache: dict[str, float | str] = {"access_token": "", "expires_at": 0}


def _is_configured() -> bool:
    s = get_settings()
    return bool(s.ZOHO_CLIENT_ID and s.ZOHO_CLIENT_SECRET and s.ZOHO_REFRESH_TOKEN and s.ZOHO_ORG_ID)


def get_access_token() -> str | None:
    """OAuth2 refresh_token rotation — auto-renews the access token."""
    if not _is_configured():
        return None
    if _token_cache["access_token"] and time.time() < float(_token_cache["expires_at"]) - 30:
        return str(_token_cache["access_token"])

    s = get_settings()
    resp = requests.post(
        "https://accounts.zoho.in/oauth/v2/token",
        params={
            "refresh_token": s.ZOHO_REFRESH_TOKEN,
            "client_id": s.ZOHO_CLIENT_ID,
            "client_secret": s.ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return str(_token_cache["access_token"])


def create_contact(client_name: str, gstin: str = "") -> dict:
    """Create a Zoho Books contact for a client. Returns {"contact_id": ...}."""
    if not _is_configured():
        return {"contact_id": f"zc_sim_{uuid.uuid4().hex[:10]}", "simulated": True}

    s = get_settings()
    token = get_access_token()
    resp = requests.post(
        f"{s.ZOHO_API_BASE}/contacts",
        params={"organization_id": s.ZOHO_ORG_ID},
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
        json={"contact_name": client_name, "gst_no": gstin},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"contact_id": data["contact"]["contact_id"], "simulated": False}


def push_draft_invoice(contact_id: str, period_label: str, total: float, notes: str) -> dict:
    """Push a single-line-item DRAFT invoice. Full billing breakdown goes in
    the notes field. Never auto-approved — accounts reviews in Zoho."""
    if not _is_configured():
        return {"invoice_id": f"zb_sim_{uuid.uuid4().hex[:10]}", "simulated": True}

    s = get_settings()
    token = get_access_token()
    resp = requests.post(
        f"{s.ZOHO_API_BASE}/invoices",
        params={"organization_id": s.ZOHO_ORG_ID},
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
        json={
            "customer_id": contact_id,
            "line_items": [{"name": period_label, "rate": total, "quantity": 1}],
            "notes": notes,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"invoice_id": data["invoice"]["invoice_id"], "simulated": False}


def push_invoice_lines(contact_id: str, reference: str, date: str, due_date: str | None, lines: list[dict], notes: str = "") -> dict:
    """Push a DRAFT invoice with one Zoho line item per billing component (spec §13). `reference` (our invoice
    number) makes the push idempotent: an invoice with the same reference_number is returned instead of duplicated."""
    if not _is_configured():
        return {"invoice_id": f"zb_sim_{uuid.uuid4().hex[:10]}", "simulated": True, "lines": len(lines)}
    s = get_settings()
    headers = {"Authorization": f"Zoho-oauthtoken {get_access_token()}"}
    params = {"organization_id": s.ZOHO_ORG_ID}
    found = requests.get(f"{s.ZOHO_API_BASE}/invoices", params={**params, "reference_number": reference}, headers=headers, timeout=15)
    if found.ok and found.json().get("invoices"):
        return {"invoice_id": found.json()["invoices"][0]["invoice_id"], "simulated": False, "existing": True}
    body = {"customer_id": contact_id, "reference_number": reference, "date": date, "notes": notes,
            "line_items": [{"name": l["label"][:100], "description": l.get("detail", "")[:500], "rate": l["amount"], "quantity": 1} for l in lines]}
    if due_date:
        body["due_date"] = due_date
    resp = requests.post(f"{s.ZOHO_API_BASE}/invoices", params=params, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    return {"invoice_id": resp.json()["invoice"]["invoice_id"], "simulated": False}
