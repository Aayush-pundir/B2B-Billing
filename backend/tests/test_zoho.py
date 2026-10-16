"""Zoho Books integration against a local stand-in that enforces GST consistency like the real thing."""
import datetime

import pytest
from sqlalchemy import inspect

from app.database import SessionLocal
from app.models import BillingAccount, BillingRun, Client, Invoice
from app.services import zoho, zoho_push, zoho_sync
from tests.fake_zoho import FakeZoho


@pytest.fixture
def scratch():
    """Throw-away run, customers and draft invoices, removed afterwards so other tests never see them."""
    db = SessionLocal()
    run = BillingRun(period="2099-01", status="approved", created_by="t")
    db.add(run)
    db.flush()
    made = {"run": run, "clients": [], "invoices": []}

    def client(eid, name, gst="", state_code="", **kw):
        c = Client(enterprise_id=eid, name=name, gst=gst, state_code=state_code, state="", status="inactive", **kw)
        db.add(c)
        db.flush()
        made["clients"].append(c)
        return c

    def invoice(c, gst_type, subtotal=1000.0):
        gst = round(subtotal * 0.18, 2)
        inv = Invoice(run_id=run.id, client_id=c.id, invoice_no=f"ZT-{c.id}-{len(made['invoices'])}", period="2099-01", status="draft",
                      lines=[{"component_type": "fixed_saas", "label": "Platform fee", "amount": subtotal, "detail": "fixed"}], subtotal=subtotal, gst_type=gst_type,
                      gst=gst, total=subtotal + gst, due_date="2099-02-15", created_at=datetime.datetime(2099, 1, 31))
        db.add(inv)
        db.flush()
        made["invoices"].append(inv)
        return inv

    made["client"], made["invoice"], made["db"] = client, invoice, db
    yield made
    db.rollback()                                       # anything never committed simply disappears
    for group in (made["invoices"], made["clients"], [run]):
        for obj in group:
            if inspect(obj).persistent:                 # a test that committed leaves rows behind: remove them
                db.delete(obj)
        db.flush()
    db.commit()
    db.close()


def test_login_failure_is_reported_clearly(fake_zoho, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ZOHO_REFRESH_TOKEN", "wrong")
    zoho.reset_caches()
    r = zoho.test_connection()
    assert r["configured"] and not r["ok"] and "Zoho" in r["detail"]


def test_connection_checks_the_gst_taxes_exist(fake_zoho):
    ok = zoho.test_connection()
    assert ok["ok"] and ok["organization"] == "Test Org" and ok["home_state"] == "UP"
    fake_zoho.taxes = [t for t in fake_zoho.taxes if t["tax_name"] != "IGST18"]
    zoho.reset_caches()
    bad = zoho.test_connection()
    assert not bad["ok"] and "IGST18" in bad["detail"]


def test_intra_state_invoice_gets_the_gst_group_and_inter_state_gets_igst(fake_zoho, scratch):
    db = scratch["db"]
    intra = scratch["client"]("ZT-A", "Lucknow Traders", gst="09AAACL1234A1Z5", state_code="09", billing_contact_email="a@x.in", city="Lucknow")
    inter = scratch["client"]("ZT-B", "Mumbai Traders", gst="27AAACM1234A1Z5", state_code="27")
    for c, gst_type in ((intra, "CGST_SGST"), (inter, "IGST")):
        zoho_push.push_one(db, scratch["run"], scratch["invoice"](c, gst_type))
    by_ref = {i["reference_number"]: i for i in fake_zoho.invoices.values()}
    a, b = [by_ref[i.invoice_no] for i in scratch["invoices"]]
    assert {li["tax_id"] for li in a["line_items"]} == {"T-GST18"} and a["place_of_supply"] == "UP" and a["gst_no"] == "09AAACL1234A1Z5"
    assert {li["tax_id"] for li in b["line_items"]} == {"T-IGST18"} and b["place_of_supply"] == "MH"
    assert a["line_items"][0]["hsn_or_sac"] == "998314" and a["gst_treatment"] == "business_gst"


def test_the_customer_is_created_once_and_reused(fake_zoho, scratch):
    db = scratch["db"]
    c = scratch["client"]("ZT-C", "Delhi Traders", gst="07AAACD1234A1Z5", state_code="07", billing_contact_email="d@x.in")
    zoho_push.push_one(db, scratch["run"], scratch["invoice"](c, "IGST"))
    zoho_push.push_one(db, scratch["run"], scratch["invoice"](c, "IGST"))
    assert len(fake_zoho.contacts) == 1 and next(iter(fake_zoho.contacts.values()))["place_of_contact"] == "DL"
    other = scratch["client"]("ZT-D", "Delhi Traders Same GSTIN", gst="07AAACD1234A1Z5", state_code="07")     # an existing Zoho customer is matched on GSTIN
    zoho_push.push_one(db, scratch["run"], scratch["invoice"](other, "IGST"))
    assert len(fake_zoho.contacts) == 1


def test_a_second_push_never_duplicates_an_invoice(fake_zoho, scratch):
    db = scratch["db"]
    c = scratch["client"]("ZT-E", "Chennai Traders", gst="33AAACC1234A1Z5", state_code="33")
    inv = scratch["invoice"](c, "IGST")
    zoho_push.push_one(db, scratch["run"], inv)
    first = inv.zoho_invoice_id
    inv.zoho_invoice_id = None                                    # as if our record of the push was lost
    zoho_push.push_one(db, scratch["run"], inv)
    assert inv.zoho_invoice_id == first and len(fake_zoho.invoices) == 1


def test_wrong_tax_for_the_place_of_supply_is_caught_before_zoho_does(fake_zoho, scratch):
    """The customer is in UP (same state as the organisation) but the invoice says IGST: Zoho refuses it and the reason is kept."""
    db = scratch["db"]
    c = scratch["client"]("ZT-F", "Kanpur Traders", gst="09AAACK1234A1Z5", state_code="09")
    inv = scratch["invoice"](c, "IGST")
    with pytest.raises(RuntimeError, match="IGST cannot be applied"):
        zoho_push.push_one(db, scratch["run"], inv)
    assert not inv.zoho_invoice_id


def test_missing_tax_in_zoho_gives_an_actionable_message(fake_zoho, scratch):
    fake_zoho.taxes = [t for t in fake_zoho.taxes if t["tax_name"] != "GST18"]
    zoho.reset_caches()
    c = scratch["client"]("ZT-G", "Agra Traders", gst="09AAACA1234A1Z5", state_code="09")
    with pytest.raises(RuntimeError, match="no active tax named 'GST18'"):
        zoho_push.push_one(scratch["db"], scratch["run"], scratch["invoice"](c, "CGST_SGST"))


def test_rate_limiting_is_retried(fake_zoho, scratch):
    fake_zoho.throttle = 2
    c = scratch["client"]("ZT-H", "Pune Traders", gst="27AAACP1234A1Z5", state_code="27")
    inv = scratch["invoice"](c, "IGST")
    zoho_push.push_one(scratch["db"], scratch["run"], inv)
    assert inv.zoho_invoice_id


def test_customers_are_pulled_from_zoho_and_linked(fake_zoho, scratch):
    db = scratch["db"]
    names = [a.display_name for a in db.query(BillingAccount).order_by(BillingAccount.id)][:3]
    assert len(names) == 3
    n1, n2, n3 = names
    zoho_rows = [
        {"contact_id": "Z1", "contact_name": n1.upper() + " PVT. LTD.", "gst_no": "27AAAAA1111A1Z1", "place_of_contact": "MH", "place_of_contact_formatted": "Maharashtra", "email": "bill@one.in", "pan_no": "AAAAA1111A"},
        {"contact_id": "Z2", "contact_name": n2, "gst_no": "09BBBBB2222B1Z2", "place_of_contact": "UP", "email": "x@two.in"},
        {"contact_id": "Z3", "contact_name": n3 + " Holdings", "gst_no": "21CCCCC3333C1Z3", "place_of_contact": "OR"},     # near match only: suggestion, not linked
        {"contact_id": "Z4", "contact_name": "Someone Else Entirely", "gst_no": "", "place_of_contact": ""},
    ]
    before = {c.id: (c.zoho_contact_id, c.gst, c.state_code) for c in db.query(Client)}
    dry = zoho_sync.pull_customers(db, zoho_rows, apply=False)
    assert dry["linked_by_name"] >= 2 and {c.id: (c.zoho_contact_id, c.gst, c.state_code) for c in db.query(Client)} == before      # a dry run writes nothing
    rep = zoho_sync.pull_customers(db, zoho_rows, apply=True)
    assert rep["zoho_customers"] == 4 and rep["unmatched_zoho"] >= 1
    c1 = db.query(BillingAccount).filter(BillingAccount.display_name == n1).one().client
    assert (c1.zoho_contact_id, c1.gst, c1.state_code, c1.billing_contact_email, c1.pan) == ("Z1", "27AAAAA1111A1Z1", "27", "bill@one.in", "AAAAA1111A")
    assert zoho_sync.learned_state_map(db)["21"] == "OR"          # Zoho's own spelling of Odisha, learned from its customers
    assert any(s["zoho_customer"].startswith(n3) for s in rep["suggestions"])
    again = zoho_sync.pull_customers(db, zoho_rows, apply=True)
    assert again["already_linked"] >= 2 and again["filled"] == {"gst": 0, "state": 0, "email": 0, "pan": 0}      # idempotent
    for c in db.query(Client).filter(Client.zoho_contact_id.in_(["Z1", "Z2"])):                              # leave the shared data as we found it
        c.zoho_contact_id, c.gst, c.state_code, c.state, c.billing_contact_email, c.pan = before[c.id][0], before[c.id][1], before[c.id][2], "", "", ""
    db.commit()


def test_sync_endpoint_needs_credentials_and_permission(fin, acc, sales_user):
    assert sales_user.post("/api/zoho/sync-customers").status_code == 403
    r = fin.post("/api/zoho/sync-customers?dry_run=true")
    assert r.status_code == 400 and "not connected" in r.json()["detail"]


def test_gst_rate_is_recovered_from_rounded_amounts_as_a_standard_slab():
    from app.services.zoho_push import _gst_slab
    assert [_gst_slab(x) for x in (17.99, 18.02, 17.5, 18.0, 5.01, 0.0, 12.2, 27.9)] == [18.0, 18.0, 18.0, 18.0, 5.0, 0.0, 12.0, 28.0]
