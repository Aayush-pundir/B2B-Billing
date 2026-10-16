"""Zoho Books integration (India edition). Falls back to an offline simulation mode (zb_sim_..., zc_sim_...) whenever the
four credentials are not all set, so the rest of the app can be exercised without a Zoho account.

What a pushed invoice carries, so Zoho computes the same GST this app does:
  * a tax on every line: the intra-state group (e.g. GST18) or the inter-state tax (e.g. IGST18), looked up by name,
  * place of supply, GST treatment and the customer's GSTIN,
  * the SAC code, and our invoice number as the reference (Zoho keeps its own numbering and e-invoicing).
"""
import time
import uuid

import requests

from app.config import get_settings

_token_cache: dict[str, float | str] = {"access_token": "", "expires_at": 0}
_tax_cache: dict = {"at": 0.0, "by_name": {}}


def _is_configured() -> bool:
    s = get_settings()
    return bool(s.ZOHO_CLIENT_ID and s.ZOHO_CLIENT_SECRET and s.ZOHO_REFRESH_TOKEN and s.ZOHO_ORG_ID)


def configured() -> bool:
    return _is_configured()


def get_access_token() -> str | None:
    """OAuth2 refresh_token rotation — auto-renews the access token."""
    if not _is_configured():
        return None
    if _token_cache["access_token"] and time.time() < float(_token_cache["expires_at"]) - 30:
        return str(_token_cache["access_token"])

    s = get_settings()
    resp = requests.post(
        f"{s.ZOHO_ACCOUNTS_BASE.rstrip('/')}/oauth/v2/token",
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
    if "access_token" not in data:
        raise RuntimeError(f"Zoho refused the login: {data.get('error', data)}")
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return str(_token_cache["access_token"])


def _headers() -> dict:
    return {"Authorization": f"Zoho-oauthtoken {get_access_token()}"}


def _call(method: str, url: str, **kw) -> requests.Response:
    """One Zoho call, retried with a pause when Zoho rate-limits us (HTTP 429) or hiccups (5xx)."""
    for attempt in range(4):
        resp = requests.request(method, url, **kw)
        if resp.status_code != 429 and resp.status_code < 500:
            return resp
        time.sleep(1.5 * (attempt + 1))
    return resp


def _fail(resp: requests.Response, what: str) -> RuntimeError:
    try:
        msg = resp.json().get("message") or resp.text[:200]
    except ValueError:
        msg = resp.text[:200]
    return RuntimeError(f"Zoho said ({what}): {msg}")


def reset_caches() -> None:
    _token_cache.update(access_token="", expires_at=0)
    _tax_cache.update(at=0.0, by_name={})


def test_connection() -> dict:
    """Prove the saved credentials work, say which Zoho organisation they reach, and check the GST taxes are there."""
    if not _is_configured():
        return {"configured": False, "ok": False, "detail": "ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN and ZOHO_ORG_ID are not all set — invoices are only simulated."}
    s = get_settings()
    try:
        resp = _call("GET", f"{s.ZOHO_API_BASE}/organizations", headers=_headers(), timeout=15)
        resp.raise_for_status()
        orgs = resp.json().get("organizations", [])
        mine = next((o for o in orgs if str(o.get("organization_id")) == str(s.ZOHO_ORG_ID)), None)
        if mine is None:
            return {"configured": True, "ok": False, "detail": f"Connected, but organisation {s.ZOHO_ORG_ID} is not among this login's organisations."}
        problems = []
        for gst_type in ("CGST_SGST", "IGST"):
            try:
                tax_for(gst_type, 18.0)
            except RuntimeError as exc:
                problems.append(str(exc))
        if problems:
            return {"configured": True, "ok": False, "organization": mine.get("name"), "detail": f"Connected to {mine.get('name')}, but: " + " ".join(problems)}
        return {"configured": True, "ok": True, "detail": f"Connected to {mine.get('name')}; GST18 and IGST18 taxes found.", "organization": mine.get("name"),
                "home_state": mine.get("state_code")}
    except Exception as exc:
        return {"configured": True, "ok": False, "detail": f"Could not reach Zoho: {exc}"}


# --------------------------------------------------------------------- taxes

def taxes() -> dict[str, dict]:
    """Active Zoho taxes by name (cached for ten minutes)."""
    if _tax_cache["by_name"] and time.time() - float(_tax_cache["at"]) < 600:
        return _tax_cache["by_name"]
    s = get_settings()
    resp = _call("GET", f"{s.ZOHO_API_BASE}/settings/taxes", params={"organization_id": s.ZOHO_ORG_ID, "per_page": 200}, headers=_headers(), timeout=15)
    if not resp.ok:
        raise _fail(resp, "reading taxes")
    by_name = {t["tax_name"]: t for t in resp.json().get("taxes", []) if not t.get("is_inactive")}
    _tax_cache.update(at=time.time(), by_name=by_name)
    return by_name


def tax_for(gst_type: str, rate: float) -> tuple[str, str]:
    """(tax_id, tax_name) to put on every line: IGST for inter-state invoices, the CGST+SGST group for intra-state ones."""
    s = get_settings()
    pattern = s.ZOHO_TAX_INTER if gst_type == "IGST" else s.ZOHO_TAX_INTRA
    rate_text = f"{rate:g}"
    name = pattern.format(rate=rate_text)
    found = taxes().get(name)
    if not found:
        raise RuntimeError(f"Zoho has no active tax named '{name}'. Create it in Zoho Books, or set ZOHO_TAX_{'INTER' if gst_type == 'IGST' else 'INTRA'} to the name you use.")
    return found["tax_id"], name


# --------------------------------------------------------------------- customers

def list_customers():
    """Every active customer in Zoho Books (name, GSTIN, place of contact, e-mail, payment terms), page by page."""
    s = get_settings()
    page = 1
    while True:
        resp = _call("GET", f"{s.ZOHO_API_BASE}/contacts", params={"organization_id": s.ZOHO_ORG_ID, "contact_type": "customer", "page": page, "per_page": 200,
                                                                  "filter_by": "Status.Active"}, headers=_headers(), timeout=30)
        if not resp.ok:
            raise _fail(resp, "reading customers")
        body = resp.json()
        yield from body.get("contacts", [])
        if not (body.get("page_context") or {}).get("has_more_page"):
            return
        page += 1


def find_contact(name: str, gstin: str = "") -> str | None:
    """Reuse an existing Zoho customer (matched on GSTIN, then exact name) instead of creating a duplicate."""
    if not _is_configured():
        return None
    s = get_settings()
    for params in ([{"gst_no": gstin}] if gstin else []) + [{"contact_name": name}]:
        resp = _call("GET", f"{s.ZOHO_API_BASE}/contacts", params={"organization_id": s.ZOHO_ORG_ID, **params}, headers=_headers(), timeout=15)
        if resp.ok:
            for c in resp.json().get("contacts", []):
                if c.get("contact_type", "customer") == "customer":
                    return c["contact_id"]
    return None


def create_contact(client_name: str, gstin: str = "", *, state_short: str = "", email: str = "", address: dict | None = None, payment_terms_days: int | None = None) -> dict:
    """Create a Zoho Books customer. Returns {"contact_id": ...}. An existing customer with the same GSTIN or name is reused."""
    if not _is_configured():
        return {"contact_id": f"zc_sim_{uuid.uuid4().hex[:10]}", "simulated": True}

    s = get_settings()
    existing = find_contact(client_name, gstin)
    if existing:
        return {"contact_id": existing, "simulated": False, "existing": True}
    body: dict = {"contact_name": client_name, "contact_type": "customer", "gst_treatment": "business_gst" if gstin else "consumer"}
    if gstin:
        body["gst_no"] = gstin
    if state_short:
        body["place_of_contact"] = state_short
    if address:
        body["billing_address"] = {k: v for k, v in address.items() if v}
    if email:
        body["contact_persons"] = [{"email": email, "is_primary_contact": True}]
    if payment_terms_days is not None:
        body["payment_terms"] = payment_terms_days
    resp = _call("POST", f"{s.ZOHO_API_BASE}/contacts", params={"organization_id": s.ZOHO_ORG_ID}, headers=_headers(), json=body, timeout=20)
    if not resp.ok:
        raise _fail(resp, f"creating customer {client_name}")
    return {"contact_id": resp.json()["contact"]["contact_id"], "simulated": False}


# --------------------------------------------------------------------- invoices

def push_draft_invoice(contact_id: str, period_label: str, total: float, notes: str) -> dict:
    """Push a single-line-item DRAFT invoice (legacy v1 runs). The full breakdown goes in the notes."""
    if not _is_configured():
        return {"invoice_id": f"zb_sim_{uuid.uuid4().hex[:10]}", "simulated": True}

    s = get_settings()
    resp = _call("POST", f"{s.ZOHO_API_BASE}/invoices", params={"organization_id": s.ZOHO_ORG_ID}, headers=_headers(),
                 json={"customer_id": contact_id, "line_items": [{"name": period_label, "rate": total, "quantity": 1}], "notes": notes}, timeout=20)
    if not resp.ok:
        raise _fail(resp, "creating invoice")
    return {"invoice_id": resp.json()["invoice"]["invoice_id"], "simulated": False}


def push_invoice_lines(contact_id: str, reference: str, date: str, due_date: str | None, lines: list[dict], notes: str = "", *,
                       gst_type: str = "IGST", gst_rate: float = 18.0, place_of_supply: str = "", gstin: str = "", sac: str = "") -> dict:
    """Push a DRAFT invoice with one Zoho line item per billing component. `reference` (our invoice number) makes the push
    idempotent: an invoice with the same reference_number is returned instead of duplicated. Zoho assigns its own invoice number."""
    if not _is_configured():
        return {"invoice_id": f"zb_sim_{uuid.uuid4().hex[:10]}", "simulated": True, "lines": len(lines)}
    s = get_settings()
    params = {"organization_id": s.ZOHO_ORG_ID}
    found = _call("GET", f"{s.ZOHO_API_BASE}/invoices", params={**params, "reference_number": reference}, headers=_headers(), timeout=15)
    if found.ok and found.json().get("invoices"):
        return {"invoice_id": found.json()["invoices"][0]["invoice_id"], "simulated": False, "existing": True}
    tax_id, _ = tax_for(gst_type, gst_rate)
    items = []
    for l in lines:
        item = {"name": l["label"][:100], "description": l.get("detail", "")[:500], "rate": l["amount"], "quantity": 1, "tax_id": tax_id}
        if sac:
            item["hsn_or_sac"] = sac
        items.append(item)
    body = {"customer_id": contact_id, "reference_number": reference, "date": date, "notes": notes, "line_items": items,
            "gst_treatment": "business_gst" if gstin else "consumer"}
    if gstin:
        body["gst_no"] = gstin
    if place_of_supply:
        body["place_of_supply"] = place_of_supply
    if due_date:
        body["due_date"] = due_date
    resp = _call("POST", f"{s.ZOHO_API_BASE}/invoices", params=params, headers=_headers(), json=body, timeout=20)
    if not resp.ok:
        raise _fail(resp, f"creating invoice {reference}")
    return {"invoice_id": resp.json()["invoice"]["invoice_id"], "simulated": False}
