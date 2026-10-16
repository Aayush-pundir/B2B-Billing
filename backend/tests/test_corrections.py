"""Corrections files and missing-customer creation, on throw-away databases with invented customers."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.database import Base
from app.models import BillingAccount, BillingGroup, BillingGroupMember, Client, SubscriptionInvoice
from app.services import billing_v2, corrections, importer_v2, snapshots


@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def _row(n, name, A, B=None, C=None, **kw):
    return {"row": n, "customer": name, "status": "Active", "A_platform": A, "B_upi": B or {"type": "None"}, "C_users": C or {"type": "None"}, "D_module": {}, **kw}


def _import(db):
    rows = [
        _row(1, "Mahi Logistics", {"type": "None", "rule": "x"}, {"type": "Per txn", "rate": 2.5, "free_txn": 0, "rule": "x"},
             {"type": "User-based", "user_rate": 200, "mg_users": 5, "rule": "x"}, overall_min=15000),
        _row(2, "Manash One", {"type": "Load % (GTV)", "rate_M": 0.0015, "rule": "x"}, overall_min=20000),
        _row(3, "Manash Two", {"type": "Load % (GTV)", "rate_M": 0.0015, "rule": "x"}, overall_min=20000),
        _row(4, "Gopal Fresh", {"type": "Load % (GTV)", "rate_M": 0.015, "min_N": 8000, "rule": "x"}),
    ]
    importer_v2.import_rules(db, "\n".join(json.dumps(r) for r in rows), "t", lock=True)
    acct = {a.source_row: a for a in db.query(BillingAccount)}
    # give Gopal the slab contract the way a rulebook import would
    from app.models import ContractComponent
    c = acct[4].contracts[0]
    for k in list(c.components):
        db.delete(k)
    db.add(ContractComponent(contract_id=c.id, component_type="load_percent_tiered", label="slabs",
                             params={"mode": "whole", "tiers": [{"upto": 1000000, "rate": 0.015, "min": 8000}, {"upto": 1500000, "rate": 0.0125, "min": 12000}, {"upto": None, "rate": 0.01, "min": 15000}]}))
    db.commit()
    return acct


def _bill(db, row, usage):
    from app.models import Client as C
    acct = db.query(BillingAccount).filter(BillingAccount.source_row == row).one()
    eid = acct.client.enterprise_id
    snapshots.save(db, "2026-09", {eid: usage}, "t")
    out = billing_v2.compute_run(db, "2026-09", snapshots.load(db, "2026-09"), {c.id: c for c in db.query(C).all()})
    return next(r for r in out["results"].values() if r["account"].id == acct.id)


FILE = {
    "corrections": [
        {"row": 1, "note": "contract says per driver", "remove": ["per_user"], "assumptions": ["billed per driver"],
         "add": [{"component_type": "per_unit_fleet", "label": "Per driver", "params": {"base": 0, "included_units": 0, "rate_per_extra_unit": 50, "unit": "driver"}}]},
        {"row": 2, "overall_min_amount": None}, {"row": 3, "overall_min_amount": None},
        {"row": 4, "modify": [{"component_type": "load_percent_tiered", "set_params": {"mode": "marginal"}}]},
    ],
    "groups": [{"name": "Manash group", "kind": "pooled", "group_min_amount": 20000, "parent_row": 2, "member_rows": [3]}],
}


def test_corrections_become_drafts_then_lock_and_change_the_bill(db):
    _import(db)
    res = corrections.apply(db, FILE, "fin@x", lock=False)
    assert len(res["drafted"]) == 4 and not res["locked"] and not res["errors"]
    gop = db.query(BillingAccount).filter(BillingAccount.source_row == 4).one()
    assert sorted(c.status for c in gop.contracts) == ["draft", "locked"]                    # the locked version is untouched
    before = _bill(db, 4, {"add_money": 1_202_873})
    assert float(before["subtotal"]) == pytest.approx(15035.91, abs=0.01)                    # still whole-amount until the draft is locked
    corrections.apply(db, FILE, "fin@x", lock=True)                                          # applying again locks the drafts it finds unchanged? no: nothing new
    for a in db.query(BillingAccount).all():
        for c in a.contracts:
            if c.status == "draft":
                billing_v2.lock_contract(c, "fin@x")
    db.commit()
    after = _bill(db, 4, {"add_money": 1_202_873})
    assert float(after["subtotal"]) == pytest.approx(17535.91, abs=0.01)                     # band-wise: 15,000 + 1.25% of the excess
    mahi = _bill(db, 1, {"upi_txn_count": 100, "maker_count": 12, "driver_count": 8})
    assert {l["component_type"] for l in mahi["lines"] if l["amount"]} >= {"upi_per_txn", "mg_topup"}      # the minimum still applies; per-maker charge is gone
    assert not any(l["component_type"] == "per_user" for l in mahi["lines"])


def test_a_pooled_minimum_is_shared_not_charged_twice(db):
    _import(db)
    corrections.apply(db, FILE, "fin@x", lock=True)
    one = _bill(db, 2, {"add_money": 1_000_000})
    two = _bill(db, 3, {"add_money": 0})
    total = float(one["subtotal"]) + float(two["subtotal"])
    assert total == pytest.approx(20000.0)                                                   # one ₹20,000 minimum for both, not ₹40,000
    assert len(db.query(BillingGroupMember).all()) == 2


def test_corrections_are_safe_to_apply_twice_and_report_problems(db):
    _import(db)
    corrections.apply(db, FILE, "fin@x", lock=True)
    again = corrections.apply(db, FILE, "fin@x", lock=True)
    assert again["drafted"] == [] and len(again["unchanged"]) == 4 and not db.query(BillingGroup).filter(BillingGroup.name == "Manash group").count() > 1
    bad = corrections.apply(db, {"corrections": [{"row": 999}, {"row": 4, "modify": [{"component_type": "per_user", "set_params": {}}]},
                                                  {"row": 4, "add": [{"component_type": "made_up"}]}], "groups": [{"name": "X", "kind": "weird"}]}, "fin@x")
    assert len(bad["errors"]) == 4 and not bad["drafted"]


def test_updating_an_existing_group_keeps_its_members(db):
    _import(db)
    corrections.apply(db, {"groups": [{"name": "G", "kind": "net_parent", "rate": 0.005, "group_min_amount": 15000, "parent_row": 2, "member_rows": [3]}]}, "fin@x")
    corrections.apply(db, {"groups": [{"name": "G", "pool_load": True}]}, "fin@x")
    g = db.query(BillingGroup).filter(BillingGroup.name == "G").one()
    assert g.pool_load and g.rate == 0.005 and len(db.query(BillingGroupMember).filter(BillingGroupMember.group_id == g.id).all()) == 2


REGISTER = """#,Bill month,Particulars (as given),Amount (₹),Remarks,Period (as given),Frequency,Period used,Months,Mapped sheet row,Match,Checks
1,July'24,Prabha Enterprises,150000,Annual Subscription,July'24 to June'25,Annual,Jul-24 to Jun-25,12,,not in revenue sheet,
2,Aug'25,PRABHA ENTERPRISES,165000,Annual Subscription,July'25 to Jun'26,Annual,Jul-25 to Jun-26,12,,not in revenue sheet,
3,Apr'25,Jaggannath Polymers,55000,Annual Subscription,Apr25 to Mar 26,Annual,Apr-25 to Mar-26,12,,not in revenue sheet,
4,Apr'26,Jagannath Polymers Private Limited,60500,Annual Subscription,Apr26 to Mar 27,Annual,Apr-26 to Mar-27,12,,not in revenue sheet,
5,June'26,Crafter Consulting and Edutech,600000,Annual Subscription,May26 to April 27,Annual,May-26 to Apr-27,12,,not in revenue sheet,
"""


def test_customers_missing_from_the_rulebook_are_created_from_the_register(db):
    importer_v2.import_register(db, REGISTER)
    assert db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id.is_(None)).count() == 5
    res = importer_v2.create_missing_accounts(db, "fin@x")
    names = sorted(c["name"] for c in res["created"])
    assert names == ["Crafter Consulting and Edutech", "Jagannath Polymers Private Limited", "Prabha Enterprises"]    # name variants merged, caps fixed
    assert res["invoices_linked"] == 5 and not res["linked_to_existing"]
    assert db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id.is_(None)).count() == 0
    prabha = db.query(BillingAccount).filter(BillingAccount.display_name == "Prabha Enterprises").one()
    assert len(db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == prabha.id).all()) == 2
    c = prabha.contracts[0]
    assert c.status == "draft" and not c.components and "usage-based" in c.notes                                      # not billable until Finance completes and locks it
    assert [e.event_type for e in prabha.events] == ["annual_billing"] and prabha.client.enterprise_id.startswith("UNMAPPED-")
    assert "₹1,50,000" in prabha.notes.replace("150,000", "1,50,000") or "150,000" in prabha.notes
    again = importer_v2.create_missing_accounts(db, "fin@x")
    assert again == {"created": [], "linked_to_existing": [], "invoices_linked": 0}                               # nothing left to do


def test_a_register_customer_that_already_has_an_account_is_linked_not_duplicated(db):
    _import(db)
    importer_v2.import_register(db, REGISTER.replace("Crafter Consulting and Edutech", "MANASH ONE"))
    res = importer_v2.create_missing_accounts(db, "fin@x")
    assert [x["name"] for x in res["linked_to_existing"]] == ["Manash One"]
    assert db.query(Client).filter(Client.name == "Manash One").count() == 1


def test_corrections_and_missing_customers_are_finance_only(fin, acc):
    f = lambda: {"file": ("c.json", json.dumps({"corrections": [], "groups": []}).encode())}
    assert acc.post("/api/v2/import/corrections", files=f()).status_code == 403
    assert acc.post("/api/v2/import/register/create-accounts").status_code == 403
    ok = fin.post("/api/v2/import/corrections", files=f())
    assert ok.status_code == 200 and ok.json()["drafted"] == []
    assert fin.post("/api/v2/import/corrections", files={"file": ("c.json", b"not json")}).status_code == 400
    assert fin.post("/api/v2/import/register/create-accounts").status_code == 200
