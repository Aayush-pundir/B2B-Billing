"""A stand-in for Zoho Books (India edition) that enforces the rules which matter for invoices: a real login, tax lookup,
customers, reference-number idempotency, and GST consistency (inter-state tax only on inter-state supply)."""
import itertools
import json

import requests

HOME_STATE = "UP"
TAXES = [
    {"tax_id": "T-GST18", "tax_name": "GST18", "tax_percentage": 18, "tax_type": "tax_group", "tax_specification": "intra"},
    {"tax_id": "T-IGST18", "tax_name": "IGST18", "tax_percentage": 18, "tax_type": "tax", "tax_specification": "inter"},
    {"tax_id": "T-GST0", "tax_name": "GST0", "tax_percentage": 0, "tax_type": "tax_group", "tax_specification": "intra"},
]


def _resp(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.headers["content-type"] = "application/json"
    return r


class FakeZoho:
    def __init__(self, contacts=None, taxes=None, org_id="ORG1", throttle_first=0):
        self.org_id, self.taxes = org_id, list(TAXES if taxes is None else taxes)
        self.contacts = {c["contact_id"]: dict(c) for c in (contacts or [])}
        self.invoices, self.calls, self._ids, self.throttle = {}, [], itertools.count(1000), throttle_first

    # requests.post (token) and requests.request (everything else) both land here
    def post(self, url, params=None, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/oauth/v2/token"):
            ok = params.get("refresh_token") == "refresh" and params.get("client_id") == "cid"
            return _resp(200, {"access_token": "tok", "expires_in": 3600} if ok else {"error": "invalid_code"})
        return self.request("POST", url, params=params, **kw)

    def request(self, method, url, params=None, headers=None, json=None, **kw):
        self.calls.append((method, url))
        if self.throttle > 0:
            self.throttle -= 1
            return _resp(429, {"message": "Too many requests"})
        if (headers or {}).get("Authorization") != "Zoho-oauthtoken tok":
            return _resp(401, {"code": 57, "message": "You are not authorized to perform this operation"})
        path = url.split("/books/v3")[-1]
        params = params or {}
        if path == "/organizations":
            return _resp(200, {"organizations": [{"organization_id": self.org_id, "name": "Test Org", "state_code": HOME_STATE}]})
        if path == "/settings/taxes":
            return _resp(200, {"taxes": self.taxes})
        if path == "/contacts" and method == "GET":
            rows = list(self.contacts.values())
            if params.get("gst_no"):
                rows = [c for c in rows if c.get("gst_no") == params["gst_no"]]
            if params.get("contact_name"):
                rows = [c for c in rows if c["contact_name"] == params["contact_name"]]
            page, per = int(params.get("page", 1)), int(params.get("per_page", 200))
            chunk = rows[(page - 1) * per: page * per]
            return _resp(200, {"contacts": chunk, "page_context": {"has_more_page": page * per < len(rows)}})
        if path == "/contacts" and method == "POST":
            cid = f"C{next(self._ids)}"
            self.contacts[cid] = {"contact_id": cid, "contact_type": "customer", **json}
            return _resp(201, {"contact": {"contact_id": cid}})
        if path == "/invoices" and method == "GET":
            ref = params.get("reference_number")
            return _resp(200, {"invoices": [i for i in self.invoices.values() if i["reference_number"] == ref]})
        if path == "/invoices" and method == "POST":
            if json["customer_id"] not in self.contacts:
                return _resp(400, {"code": 2, "message": "Customer does not exist"})
            tax_by_id = {t["tax_id"]: t for t in self.taxes}
            pos = json.get("place_of_supply", "")
            for li in json["line_items"]:
                t = tax_by_id.get(li.get("tax_id"))
                if t is None:
                    return _resp(400, {"code": 4, "message": "Please select a tax for each line item"})
                if t["tax_specification"] == "inter" and pos == HOME_STATE:
                    return _resp(400, {"code": 1209, "message": "IGST cannot be applied when the place of supply is the same state as the organisation"})
                if t["tax_specification"] == "intra" and pos and pos != HOME_STATE:
                    return _resp(400, {"code": 1210, "message": "CGST/SGST cannot be applied for an inter-state supply"})
            iid = f"I{next(self._ids)}"
            self.invoices[iid] = {"invoice_id": iid, "status": "draft", **json}
            return _resp(201, {"invoice": {"invoice_id": iid}})
        return _resp(404, {"message": f"no route {method} {path}"})
