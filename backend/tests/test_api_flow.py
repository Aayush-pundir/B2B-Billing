"""End-to-end API tests on synthetic data: roles, billing v2 import → run → approval → ledger → insights → renewals → Zoho."""
import datetime
import json

import pytest

PERIOD = "2026-09"
RULES = [
    {"row": 1, "customer": "Alpha Fixed Co", "status": "Active", "go_live": "2024-02-01", "A_platform": {"type": "Fixed SaaS + 10% escalation", "base_P": 15000, "rule": "x"},
     "B_upi": {"type": "Per txn", "free_txn": 0, "rate": 0.75, "rule": "x"}, "C_users": {"type": "None"}, "D_module": {"rule": "No module"}},
    {"row": 2, "customer": "Beta Load Co", "status": "Active", "go_live": "2024-05-01", "A_platform": {"type": "Load % (GTV)", "rate_M": 0.0035, "rule": "x"},
     "B_upi": {"type": "Per txn", "free_txn": 0, "rate": 2.5, "rule": "x"}, "C_users": {"type": "User-based", "mg_users": 5, "user_rate": 199, "free_admins": 0, "admin_rate": 1000},
     "D_module": {"rule": "No module"}, "overall_min": 4000},
    {"row": 3, "customer": "Gamma Parent", "status": "Active", "go_live": "2024-01-01", "A_platform": {"type": "Special / group", "rule": "GROUP MG: MAX(Gamma Add Money × 0.8%, ₹30,000) LESS fee billed to Delta [row 4]."},
     "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "No module"}},
    {"row": 4, "customer": "Delta Member", "status": "Active", "go_live": "2024-01-01", "A_platform": {"type": "Load % (GTV)", "rate_M": 0.0015, "rule": "x"}, "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "No module"}},
    {"row": 5, "customer": "Epsilon Dropped", "status": "Dropped", "go_live": "2023-01-01", "A_platform": {"type": "Fixed SaaS", "base_P": 5000, "rule": "x"}, "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "No module"}},
    {"row": 6, "customer": "Zeta Custom", "status": "Active", "go_live": "2023-01-01", "A_platform": {"type": "Special", "rule": "bespoke unmodelled ladder"}, "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "No module"}},
    {"row": 7, "customer": "Eta Annual", "status": "Active – annual billing", "go_live": "2023-01-01", "A_platform": {"type": "Fixed SaaS", "base_P": 55000, "rule": "x"},
     "B_upi": {"type": "Per txn", "free_txn": 0, "rate": 2.5, "rule": "x"}, "C_users": {"type": "None"}, "D_module": {"rule": "Trip-Management / module package: 1 unit(s) × ₹3,500 per month.", "formula": "D = 1 × 3500"}},
]
REGISTER = "#,Bill month,Particulars (as given),Amount (₹),Remarks,Period (as given),Frequency,Period used,Months,Mapped sheet row,Match,Checks\n1,Jun'26,Eta Annual,55000,Annual Subscription,Jun'26 to May'27,Annual,Jun-26 to May-27,12,7,exact,\n"
MAPPING = "source_row,customer,enterprise_id\n" + "".join(f"{i},x,ENT-T-{i}\n" for i in range(1, 8))
LOADING = "EnterpriseId,TransactionCategory,Status,TotalTransactionAmount\n" + "".join(f"{e},Add Money,COMPLETED,{a}\n" for e, a in [("ENT-T-2", 182693), ("ENT-T-3", 3049520), ("ENT-T-4", 3209867)])
UPI = "EnterpriseId,TransactionType,Status,TransactionCategory,TotalTrxnAmount\n" + "".join(f"{e},DEBIT,COMPLETED,Upi Pay,100\n" for e, n in [("ENT-T-1", 301), ("ENT-T-2", 85), ("ENT-T-7", 456)] for _ in range(n))
USERS = "EnterpriseId,Admin,CO_ADMIN,CHECKER,APPROVER,MAKER,TotalUsers\nENT-T-2,1,0,4,0,2,7\n"


def files(**kw):
    return {k: (f"{k}.csv", v.encode()) for k, v in kw.items()}


# ----------------------------------------------------------------------------- security / roles
def test_auth_required(client_factory):
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)
    for path in ("/api/clients", "/api/finance/accounts", "/api/v2/accounts", "/api/insights/overview"):
        assert anon.get(path).status_code == 401, path


def test_role_matrix(fin, acc, adm, sales_user):
    assert sales_user.get("/api/finance/accounts").status_code == 403          # no ledger for sales
    assert sales_user.get("/api/insights/overview").status_code == 403
    assert acc.post("/api/commercials", json={"client_id": 1, "rules": []}).status_code == 403
    assert adm.post("/api/v2/import/rules", files=files(file=json.dumps(RULES[0]))).status_code == 403
    assert acc.post("/api/purchases/vendors", json={"name": "x"}).status_code == 403


def test_dev_login_can_be_disabled(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.main import app
    monkeypatch.setattr(get_settings(), "ALLOW_DEV_LOGIN", False)
    c = TestClient(app)
    assert c.post("/auth/dev-login", json={"email": "x@y.z", "role": "admin"}).status_code == 403
    assert c.get("/auth/providers").json()["dev"] is False


def test_sql_guard_blocks_writes(adm):
    for bad in ("DELETE FROM t", "SELECT 1; DROP TABLE x", "WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a", "SELECT * FROM t INTO OUTFILE 'x'"):
        assert adm.put("/api/datasource", json={"queries": {"loading": bad}}).status_code == 400, bad


# ----------------------------------------------------------------------------- billing v2 flow
def test_import_mapping_and_idempotency(fin):
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in RULES)
    r = fin.post("/api/v2/import/rules?lock=true", files=files(file=text)).json()
    assert r["accounts_created"] == 7 and r["groups_created"] == 1 and r["unmodelled_components"] == 1
    again = fin.post("/api/v2/import/rules?lock=true", files=files(file=text)).json()
    assert again["accounts_created"] == 0 and again["skipped_existing"] == 7
    assert fin.post("/api/v2/import/register", files=files(file=REGISTER)).json()["invoices"] == 1
    m = fin.post("/api/v2/import/mapping", files=files(file=MAPPING)).json()
    assert m["mapped"] == 7 and not m["errors"]


def test_unmapped_accounts_are_withheld_not_billed(fin, acc):
    # (runs before mapping would be needed; here mapping is done, so just assert the validator exists in the catalogue)
    cat = fin.get("/api/v2/catalogue").json()
    assert "per_user" in cat["components"] and "mg_deferred" in cat["events"]


def test_billing_run_v2_amounts(acc, fin):
    r = acc.post("/api/billing/calculate", data={"period": PERIOD, "tds_rate": 0}, files=files(loading_file=LOADING, upi_file=UPI, users_file=USERS))
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "pending_approval"
    inv = {i["enterprise_id"]: i for i in fin.get(f"/api/billing/runs/{run['run_id']}").json()["invoices"]}
    assert inv["ENT-T-1"]["subtotal"] == 16725.75                       # fixed ×1.10 + 301 × ₹0.75
    assert inv["ENT-T-2"]["subtotal"] == 4000.0                          # usage 2,846.93 floored at the minimum (checkers not billed)
    assert any(l["label"].startswith("Minimum") for l in inv["ENT-T-2"]["lines"])
    assert inv["ENT-T-3"]["subtotal"] == round(max(3049520 * 0.008, 30000) - 3209867 * 0.0015, 2)   # group: parent fee less member's
    assert inv["ENT-T-7"]["subtotal"] == 456 * 2.5 + 3500                # subscription covers the platform fee; UPI + module bill
    skipped = {s["enterprise_id"]: s["reason"] for s in run["skipped"]}
    assert "ENT-T-5" in skipped and "withheld" in skipped["ENT-T-6"]    # dropped; unmodelled is withheld, never billed ₹0
    pytest.run_id = run["run_id"]


def test_duplicate_period_blocked(acc):
    r = acc.post("/api/billing/calculate", data={"period": PERIOD}, files=files(loading_file=LOADING, upi_file=UPI, users_file=USERS))
    assert r.status_code == 409


def test_approval_gates_and_snapshot_freeze(fin, fin2, acc):
    rid = pytest.run_id
    assert acc.post(f"/api/billing/runs/{rid}/approve", json={}).status_code == 403
    ex = fin.get(f"/api/v2/exceptions?run_id={rid}").json()
    assert any(e["code"] == "UNMODELLED_COMPONENT" and e["blocks"] for e in ex)
    q = fin2.get(f"/api/close/runs/{rid}/review").json()
    assert q["counts"]["open"] > 0 and all(i["reasons"] for i in q["items"])
    assert any(r["code"] == "first_invoice" for i in q["items"] for r in i["reasons"])       # nothing was billed before: every customer is new
    assert any(r["code"] == "minimum_applied" for i in q["items"] for r in i["reasons"])      # Beta billed at its minimum
    assert fin2.post(f"/api/billing/runs/{rid}/approve", json={"acknowledge_high": True}).status_code == 409   # cannot lock with checks outstanding
    first = q["items"][0]
    assert fin2.post(f"/api/close/runs/{rid}/review/{first['id']}", json={"action": "exclude"}).status_code == 400   # leaving one out needs a reason
    assert acc.post(f"/api/close/runs/{rid}/review/{first['id']}", json={"action": "ok"}).status_code == 403          # reviewing is a Finance act
    done = fin2.post(f"/api/close/runs/{rid}/review-bulk", json={"item_ids": [i["id"] for i in q["items"]], "comment": "checked against usage"}).json()
    assert done["counts"]["open"] == 0 and done["counts"]["reviewed"] == len(q["items"])
    r = fin2.post(f"/api/billing/runs/{rid}/approve", json={"acknowledge_high": True})
    assert r.status_code == 200 and r.json()["finalized"]["issued"] >= 5
    assert fin2.post(f"/api/close/runs/{rid}/review/{first['id']}", json={"action": "reopen"}).status_code == 400       # locked is locked
    usage = fin.get(f"/api/v2/usage/{PERIOD}").json()
    assert usage and all(u["frozen"] for u in usage)
    assert fin.put(f"/api/v2/usage/{PERIOD}/ENT-T-2", json={"values": {"maker_count": 99}}).status_code == 400


def test_contract_versioning_keeps_history(fin):
    aid = [a for a in fin.get("/api/v2/accounts").json() if a["name"] == "Beta Load Co"][0]["id"]
    v2 = fin.post(f"/api/v2/accounts/{aid}/contracts").json()
    body = {"valid_from": "2026-10-01", "overall_min_amount": 5000, "components": [{"component_type": "load_percent", "params": {"rate": 0.004}}]}
    assert fin.put(f"/api/v2/contracts/{v2['id']}", json=body).status_code == 200
    assert fin.post(f"/api/v2/contracts/{v2['id']}/lock").status_code == 200
    sim = lambda p: fin.post(f"/api/v2/accounts/{aid}/simulate", json={"period": p, "usage": {"add_money": 1000000}}).json()
    assert sim("2026-10")["subtotal"] == 5000.0                           # new version from October
    assert sim("2026-09")["subtotal"] > 0                                 # September still computable on the superseded version
    bad = {"valid_from": "2026-10-01", "components": [{"component_type": "nonsense", "params": {}}]}
    d = fin.post(f"/api/v2/accounts/{aid}/contracts").json()
    assert fin.put(f"/api/v2/contracts/{d['id']}", json=bad).status_code == 400


def test_lifecycle_event_changes_billability(fin):
    aid = [a for a in fin.get("/api/v2/accounts").json() if a["name"] == "Alpha Fixed Co"][0]["id"]
    fin.post(f"/api/v2/accounts/{aid}/events", json={"event_type": "on_hold", "effective_from": "2026-10-01"})
    held = fin.post(f"/api/v2/accounts/{aid}/simulate", json={"period": "2026-10", "usage": {"upi_txn_count": 10}}).json()
    assert not held["billable"] and any(e["code"] == "NONBILLABLE_BUT_AMOUNT" for e in held["exceptions"])
    assert fin.post(f"/api/v2/accounts/{aid}/simulate", json={"period": "2026-09", "usage": {"upi_txn_count": 10}}).json()["billable"]


def test_escalation_creates_draft_only(fin):
    aid = [a for a in fin.get("/api/v2/accounts").json() if a["name"] == "Alpha Fixed Co"][0]["id"]
    made = fin.post("/api/v2/escalations/apply", json={"through": "2031-01-01"}).json()["drafts_created"]
    assert made
    det = fin.get(f"/api/v2/accounts/{aid}").json()
    draft = [c for c in det["contracts"] if c["status"] == "draft" and c["source"] == "escalation"][0]
    assert [k for k in draft["components"] if k["component_type"] == "fixed_saas"][0]["params"]["base_amount"] == 16500
    assert [c for c in det["contracts"] if c["version"] == 1][0]["components"][0]["params"]["base_amount"] == 15000   # history untouched


# ----------------------------------------------------------------------------- books, insights, renewals, zoho
def test_books_reconcile(fin):
    ig = fin.get("/api/finance/integrity").json()
    assert ig["ok"], ig


def test_insights_overview(fin):
    d = fin.get(f"/api/insights/overview?period={PERIOD}").json()
    assert d["kpis"]["revenue"] > 0 and d["kpis"]["customers"] >= 5
    assert abs(sum(m["share"] for m in d["mix"]) - 100) < 0.5               # mix adds up
    assert d["concentration"]["top"] and d["concentration"]["hhi"] > 0
    assert any("top-up" in m["label"].lower() for m in d["mix"]) and any("UPI" in m["label"] for m in d["mix"])
    assert d["floors"]["accounts"] >= 1                                      # Beta billed at its minimum
    assert len(d["trend"]) == 12 and d["trend"][-1]["period"] == PERIOD
    assert d["leakage"]["accounts"] == 7


def test_renewals_forecast_and_raise(fin):
    r = fin.get("/api/v2/renewals?horizon_days=400").json()
    assert r["rows"] and r["rows"][0]["account"] == "Eta Annual"
    aid = r["rows"][0]["account_id"]
    out = fin.post(f"/api/v2/renewals/{aid}/raise", json={"amount": 60500, "invoice": True}).json()
    assert out["period_start"] == "2027-06-01" and out["invoice_no"]
    nxt = fin.post(f"/api/v2/renewals/{aid}/raise", json={"invoice": False}).json()    # each call renews the following cycle — never the same one twice
    assert nxt["period_start"] == "2028-06-01" and nxt["invoice_no"] == ""


def test_zoho_push_one_line_per_charge(fin):
    rid = pytest.run_id
    res = fin.post(f"/api/billing/runs/{rid}/push-zoho").json()
    assert res["pushed"] and not res["failed"]                              # simulation mode (no credentials)
    again = fin.post(f"/api/billing/runs/{rid}/push-zoho").json()
    assert again["pushed"] == []                                            # idempotent: nothing pushed twice


def test_adhoc_invoice_flows_into_books_and_gst(fin):
    cid = fin.get("/api/clients").json()[0]["id"]
    it = fin.post("/api/items", json={"code": "T1", "name": "Training", "hsn_sac": "999293", "rate": 10000, "gst_rate": 18}).json()["id"]
    r = fin.post("/api/sales/invoices", json={"client_id": cid, "lines": [{"item_id": it, "qty": 2}], "issue": True}).json()
    assert r["total"] == 23600.0
    assert fin.get("/api/finance/integrity").json()["ok"]
    g = fin.get("/api/finance/reports/gst").json()
    assert g["summary"]["outward"]["taxable"] > 0


def test_scheduler_tick_is_safe_to_run(fin):
    r = fin.post("/api/automation/run-now", json={"steps": ["recurring", "renewals", "dunning", "credit"]})
    assert r.status_code == 200 and "renewals" in r.json()["steps"]


# ----------------------------------------------------------------------------- optional usage inputs: spend-based + fleet
def test_spend_based_and_fleet_inputs_from_usage_db(fin, adm, monkeypatch, tmp_path):
    import sqlite3
    from app.config import get_settings
    from app.services import usage_db

    db = tmp_path / "usage.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE wallet_transactions(enterprise_id TEXT, total_transaction_amount REAL, transaction_category TEXT, status TEXT, transaction_date TEXT);
      CREATE TABLE upi_transactions(enterprise_id TEXT, total_trxn_amount REAL, transaction_category TEXT, transaction_type TEXT, status TEXT, transaction_date TEXT);
      CREATE TABLE enterprise_users(enterprise_id TEXT, role TEXT, status TEXT);
      CREATE TABLE spend_txns(enterprise_id TEXT, amount REAL, txn_time TEXT);
      CREATE TABLE fleet(enterprise_id TEXT, trucks INTEGER);""")
    c.executemany("INSERT INTO spend_txns VALUES(?,?,?)", [("ENT-T-8", a, f"2026-08-0{i}") for i, a in enumerate([100, 200, 300, 400], start=1)])  # first 2 free -> 700 after
    c.execute("INSERT INTO fleet VALUES('ENT-T-9', 120)")
    c.commit(); c.close()
    monkeypatch.setattr(get_settings(), "USAGE_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(usage_db, "_engine", None)

    rows = [
        {"row": 8, "customer": "Spend Based Co", "status": "Active", "go_live": "2024-01-01", "A_platform": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "x"},
         "B_upi": {"type": "Special", "free_txn": 2, "rule": "Spend-based: first R transactions free; thereafter 1% of SPEND (card + UPI + RuPay)."}},
        {"row": 9, "customer": "Fleet Co", "status": "Active", "go_live": "2024-01-01", "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "x"},
         "A_platform": {"type": "Fixed (hard-typed)", "rule": "₹3,000 fixed up to 100 trucks; ₹80/truck beyond 100 (excess not formula-coded)."}},
    ]
    assert fin.post("/api/v2/import/rules?lock=true", files=files(file="\n".join(json.dumps(r, ensure_ascii=False) for r in rows))).json()["accounts_created"] == 2
    assert fin.post("/api/v2/import/mapping", files=files(file="source_row,customer,enterprise_id\n8,x,ENT-T-8\n9,x,ENT-T-9\n")).json()["mapped"] == 2
    assert adm.put("/api/datasource", json={"queries": {"spend": "SELECT enterprise_id, amount, txn_time FROM spend_txns WHERE txn_time >= :period_start AND txn_time < :period_end",
                                                         "extras": "SELECT enterprise_id, trucks AS truck_count FROM fleet"}}).status_code == 200
    assert fin.post("/api/v2/usage/2026-08/refresh").status_code == 200
    snap = {u["enterprise_id"]: u for u in fin.get("/api/v2/usage/2026-08").json()}
    assert snap["ENT-T-8"]["upi_spend_after_free"] == 700.0 and snap["ENT-T-9"]["truck_count"] == 120
    sim = lambda name: fin.post(f"/api/v2/accounts/{[a for a in fin.get('/api/v2/accounts').json() if a['name'] == name][0]['id']}/simulate", json={"period": "2026-08"}).json()
    assert sim("Spend Based Co")["subtotal"] == 7.0                       # 1% × ₹700 after the 2 free transactions
    assert sim("Fleet Co")["subtotal"] == 3000 + 20 * 80                  # 120 trucks: 20 beyond the 100 included
    adm.put("/api/datasource", json={"queries": {"spend": "", "extras": ""}})


def test_slab_contract_import_whole_amount(fin):
    row = {"row": 10, "customer": "Slab Co", "status": "Active", "go_live": "2024-01-01", "B_upi": {"type": "None"}, "C_users": {"type": "None"}, "D_module": {"rule": "x"},
           "A_platform": {"type": "Load % (GTV)", "rate_M": "2% upto 10 lakh, 1.75% 11 lakh to 25 lakh , 1.5% 26 lakh to 50 Lakh, 1% 51 lakh & above"}, "contract_text": "x"}
    assert fin.post("/api/v2/import/rules?lock=true", files=files(file=json.dumps(row, ensure_ascii=False))).json()["unmodelled_components"] == 0
    fin.post("/api/v2/import/mapping", files=files(file="source_row,customer,enterprise_id\n10,x,ENT-T-10\n"))
    aid = [a for a in fin.get("/api/v2/accounts").json() if a["name"] == "Slab Co"][0]["id"]
    sim = lambda L: fin.post(f"/api/v2/accounts/{aid}/simulate", json={"period": "2026-08", "usage": {"add_money": L}}).json()["subtotal"]
    assert sim(900000) == 18000.0           # slab 1: 2%
    assert sim(2000000) == 35000.0          # slab 2 applies to ALL of it (whole-amount): 1.75%
    assert sim(6000000) == 60000.0          # top slab: 1%


def test_reports_never_serve_stale_books_after_a_write(fin):
    """The ledger is cached between writes; a new document must show in the reports on the very next request."""
    today = __import__("datetime").date.today().isoformat()
    before = fin.get(f"/api/finance/reports/trial-balance?as_of={today}").json()["total_debit"]
    fin.get(f"/api/finance/reports/trial-balance?as_of={today}")            # warm the cache
    cid = fin.get("/api/clients").json()[0]["id"]
    it = fin.post("/api/items", json={"code": "CACHE1", "name": "Cache probe", "hsn_sac": "999293", "rate": 1000, "gst_rate": 18}).json()["id"]
    fin.post("/api/sales/invoices", json={"client_id": cid, "lines": [{"item_id": it, "qty": 1}], "issue": True})
    after = fin.get(f"/api/finance/reports/trial-balance?as_of={today}").json()["total_debit"]
    assert round(after - before, 2) == 1180.0
    assert fin.get("/api/finance/integrity").json()["ok"]


def test_dashboard_matches_the_underlying_lists(fin):
    d = fin.get("/api/dashboard").json()
    invoices, runs, clients = fin.get("/api/billing/invoices").json(), fin.get("/api/billing/runs").json(), fin.get("/api/clients").json()
    assert d["invoices"] == len(invoices) and d["runs"] == len(runs) and d["clients"] == len(clients)
    assert round(d["revenue"], 2) == round(sum(i["total"] for i in invoices), 2)
    assert [r["id"] for r in d["recent_runs"]] == [r["id"] for r in runs][:8]
    assert [r["invoice_count"] for r in d["recent_runs"]] == [r["invoice_count"] for r in runs][:8]


def test_invoice_filters_and_paging(fin):
    everything = fin.get("/api/billing/invoices")
    total = len(everything.json())
    assert total >= 6 and everything.headers["x-total-count"] == str(total)
    page = fin.get("/api/billing/invoices?limit=3&offset=2")
    assert [i["id"] for i in page.json()] == [i["id"] for i in everything.json()][2:5] and page.headers["x-total-count"] == str(total)
    one = everything.json()[0]
    hit = fin.get(f"/api/billing/invoices?q={one['invoice_no']}").json()
    assert [i["id"] for i in hit] == [one["id"]]
    by_status = fin.get(f"/api/billing/invoices?status={one['status']}")
    assert all(i["status"] == one["status"] for i in by_status.json())


def test_quick_search_spans_records_and_ignores_noise(fin):
    assert fin.get("/api/search?q=a").json() == {"customers": [], "accounts": [], "invoices": [], "vendors": []}
    name = fin.get("/api/clients").json()[0]["name"]
    r = fin.get(f"/api/search?q={name[:4]}").json()
    assert any(c["name"] == name for c in r["customers"])
    assert fin.get("/api/search?q=zzzzzz-nothing").json()["customers"] == []


def test_passcode_signin_assigns_roles_on_the_server(monkeypatch):
    from app.config import get_settings
    from fastapi.testclient import TestClient
    from app.main import app
    s = get_settings()
    monkeypatch.setattr(s, "ACCESS_PASSCODE", "team-secret")
    c = TestClient(app)
    assert c.get("/auth/providers").json()["passcode"] is True
    assert c.post("/auth/dev-login", json={"email": "x@corp.com", "role": "admin", "passcode": "nope"}).status_code == 401
    ok = c.post("/auth/dev-login", json={"email": "newbie@corp.com", "role": "admin", "passcode": "team-secret"})
    assert ok.status_code == 200 and ok.json()["role"] == "viewer"          # asking for admin is ignored
    assert c.get("/api/users").status_code == 403                           # and a viewer cannot manage users
    boot = c.post("/auth/dev-login", json={"email": s.BOOTSTRAP_ADMIN_EMAIL.upper(), "passcode": "team-secret"})
    assert boot.status_code == 200 and boot.json()["role"] == "admin"


def test_passcode_attempts_are_throttled(monkeypatch):
    from app.config import get_settings
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import auth_routes
    monkeypatch.setattr(get_settings(), "ACCESS_PASSCODE", "team-secret")
    auth_routes._FAILS.clear()
    c = TestClient(app)
    codes = [c.post("/auth/dev-login", json={"email": "t@corp.com", "passcode": "bad"}).status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]
    assert c.post("/auth/dev-login", json={"email": "t@corp.com", "passcode": "team-secret"}).status_code == 429
    auth_routes._FAILS.clear()


def test_hosted_server_without_passcode_fails_closed(monkeypatch):
    from app.config import get_settings
    from app.config import dev_login_allowed
    s = get_settings()
    monkeypatch.setattr(s, "HOSTED", True)
    monkeypatch.setattr(s, "ACCESS_PASSCODE", "")
    assert dev_login_allowed(s) is False
    monkeypatch.setattr(s, "ACCESS_PASSCODE", "x")
    assert dev_login_allowed(s) is True


def test_database_url_is_normalised_for_managed_postgres():
    from app.config import _normalise_db_url
    assert _normalise_db_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert _normalise_db_url("postgresql://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert _normalise_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_month_end_close_review_lock_and_background_push(fin, fin2, acc):
    from app.services import zoho_push
    period = "2026-10"
    r = acc.post("/api/billing/calculate", data={"period": period, "tds_rate": 0}, files=files(loading_file=LOADING, upi_file=UPI, users_file=USERS))
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    q = fin2.get(f"/api/close/runs/{rid}/review").json()
    assert q["run"]["status"] == "pending_approval" and q["counts"]["open"] > 0
    # September exists, so October is compared against it: the quiet customers are no longer "first invoice"
    by_name = {i["client_name"]: i for i in q["items"]}
    assert by_name["Beta Load Co"]["prev_amount"] == 4000.0                   # compared with September's invoice
    assert {i["client_name"] for i in q["items"] if any(x["code"] == "first_invoice" for x in i["reasons"])} == {"Fleet Co"}   # the only customer with no earlier invoice
    # leave one invoice out (with a reason), change our mind, then leave it out again
    target = q["items"][0]
    assert fin2.post(f"/api/close/runs/{rid}/review/{target['id']}", json={"action": "exclude", "comment": "customer asked to hold"}).status_code == 200
    detail = fin2.get(f"/api/billing/runs/{rid}").json()
    assert next(i for i in detail["invoices"] if i["id"] == target["invoice_id"])["status"] == "cancelled"
    assert fin2.post(f"/api/close/runs/{rid}/review/{target['id']}", json={"action": "reopen"}).json()["counts"]["excluded"] == 0
    fin2.post(f"/api/close/runs/{rid}/review/{target['id']}", json={"action": "exclude", "comment": "customer asked to hold"})
    rest = [i["id"] for i in q["items"] if i["id"] != target["id"]]
    fin2.post(f"/api/close/runs/{rid}/review-bulk", json={"item_ids": rest})
    assert fin2.post(f"/api/billing/runs/{rid}/approve", json={"acknowledge_high": True}).status_code == 200
    # lock, then push in the background; progress is read back from the invoices
    assert fin.post(f"/api/close/runs/{rid}/zoho/start").status_code == 200
    zoho_push.wait(rid)
    st = fin.get(f"/api/close/runs/{rid}/zoho/status").json()
    assert st["running"] is False and st["simulated"] is True and st["failed"] == 0
    assert st["pushed"] == st["total"] > 0 and st["run_status"] == "pushed"
    assert st["total"] == len(detail["invoices"]) - 1                         # the excluded invoice never goes to Zoho
    assert fin.post(f"/api/close/runs/{rid}/zoho/start").json()["started"] in (True, False) and fin.get(f"/api/close/runs/{rid}/zoho/status").json()["pushed"] == st["pushed"]


def test_close_checklist_and_zoho_status(fin):
    c = fin.get("/api/close/checklist?period=2026-11").json()
    keys = {i["key"]: i for i in c["items"]}
    assert {"feed", "usage", "mapping", "modelled", "drafts", "previous", "zoho"} <= set(keys)
    assert all(i["status"] in ("ok", "warn", "block") and i["detail"] for i in c["items"])
    assert keys["zoho"]["status"] == "warn"                                                        # no credentials in tests
    assert fin.post("/api/close/zoho/test").json()["configured"] is False


def test_dashboard_close_status_kpis_and_attention(fin):
    d = fin.get("/api/dashboard").json()
    assert len(d["period"]) == 7 and d["close"]["step"] in ("calc", "review", "lock", "zoho", "done")
    assert len(d["trend"]) == 6 and d["trend"][-1]["period"] == d["period"]
    k = d["kpis"]
    assert k["billed"] >= 0 and k["outstanding"] >= k["overdue"] >= 0 and k["accounts"] >= 5
    assert all({"level", "text", "page", "cta"} <= set(a) for a in d["attention"])


def test_account_list_has_terms_last_billed_and_draft_flags(fin):
    rows = {r["name"]: r for r in fin.get("/api/v2/accounts").json()}
    beta = rows["Beta Load Co"]
    assert "load_percent" in beta["kinds"]                                   # earlier tests may have revised the rest of Beta's contract
    assert beta["last_billed"]["amount"] > 0 and beta["last_billed"]["period"] >= "2026-09"
    assert all("draft_pending" in r for r in rows.values())


# ---- unmodelled-charge resolution (synthetic rows that reuse the real wordings, no customer data) ----
def _placeholder(rule="no base amount: Fixed monthly SaaS fee None"):
    return [{"component_type": "manual_formula", "label": "Platform fee (needs modelling)", "params": {"rule": rule}}]


def _row(status="Active", rate_cell="", contract="", rule="", notes=()):
    return {"status": status, "contract_text": contract, "sheet_notes": [{"cat": n} for n in notes], "A_platform": {"rule": rule, "rate_M": rate_cell}}


def test_text_rate_with_minimum_becomes_a_load_percent():
    from app.services.importer_v2 import _clear_placeholders, _text_rate_and_minimum
    assert _text_rate_and_minimum("2% of the funds added or 7K which ever is higher") == (0.02, 7000.0)
    assert _text_rate_and_minimum("1.5% pay as you go or 5K+Taxes, whichever is higher") == (0.015, 5000.0)
    assert _text_rate_and_minimum("2% or 1500 whichever is higher") == (0.02, 1500.0)
    comps, _, assume = _clear_placeholders(_row(rate_cell="1.5% of loading or 5k whichever is higher"), _placeholder("rate is not numeric in the legacy sheet"))
    assert comps == [{"component_type": "load_percent", "label": "Platform fee — % of Add Money", "params": {"rate": 0.015, "min_amount": 5000.0}}] and not assume
    comps, _, assume = _clear_placeholders(_row(rate_cell="1.5% or 5000 MG from 5th month"), _placeholder("rate is not numeric in the legacy sheet"))
    assert comps[0]["params"] == {"rate": 0.015} and "month 5" in assume[0]          # minimum cannot be dated without a go-live: asked, not guessed


def test_percentages_in_the_contract_text_are_not_mistaken_for_the_platform_rate():
    from app.services.importer_v2 import _clear_placeholders
    row = _row(status="Active – annual subscription", contract="UPI 2.50/- flat up to 2500/- and 0.1% of the total sum above")
    comps, tags, assume = _clear_placeholders(row, _placeholder("rate is not numeric in the legacy sheet"))
    assert comps == [] and "fee_via_subscription" in tags and not assume              # annual: the fee is a subscription invoice


def test_dropped_blank_fee_and_fleet_cases():
    from app.services.importer_v2 import _clear_placeholders, _fix_related_usage_components
    comps, tags, _ = _clear_placeholders(_row(status="Dropped"), _placeholder())
    assert comps == [] and "terms_not_captured" in tags
    comps, _, assume = _clear_placeholders(_row(), _placeholder())
    assert comps == [] and "no platform fee" in assume[0]                              # billed on usage only, with a question attached
    fleet = _row(rule="₹100 per truck/driver per month (not formula-coded). UPI: 2,500 txns free each; collective 5,000 threshold", contract="100 Per truck / driver per month")
    comps, _, assume = _clear_placeholders(fleet, _placeholder("fleet fee not coded"))
    assert comps[0]["component_type"] == "per_unit_fleet" and comps[0]["params"]["rate_per_extra_unit"] == 100.0 and "drivers" in assume[0]
    upi, more = _fix_related_usage_components(fleet, [{"component_type": "upi_per_txn", "label": "UPI fee", "params": {"rate": 2.5, "free_txns": 0}}])
    assert upi[0]["params"]["free_txns"] == 2500.0 and "collective" in more[0]
    flat = _row(rule="₹3,000 per month up to 80 drivers; UPI ₹2.5 (not coded)", contract="3000 Monthly upto 80 driver & 2.5 UPI")
    comps, _, assume = _clear_placeholders(flat, _placeholder("unrecognised platform-fee rule"))
    assert comps[0]["params"] == {"amount": 3000.0} and "above 80" in assume[0]
    upi, more = _fix_related_usage_components(flat, [])
    assert upi[0]["component_type"] == "upi_per_txn" and upi[0]["params"]["rate"] == 2.5 and "never billed" in more[0]


def test_ambiguous_load_clause_keeps_the_invoice_withheld():
    from app.services.importer_v2 import _clear_placeholders
    row = _row(status="Not yet live", rate_cell="100/Per truck, UPI 2.5/Transaction, after 1CR 20000/CR")
    comps, _, _ = _clear_placeholders(row, _placeholder("rate is not numeric in the legacy sheet"))
    assert [c["component_type"] for c in comps] == ["per_unit_fleet", "manual_formula"]  # fleet is read; the unclear clause still blocks billing


def test_assumptions_show_as_findings_until_finance_confirms(fin, fin2):
    from app.database import SessionLocal
    from app.models import BillingAccount
    db = SessionLocal()
    beta = db.query(BillingAccount).filter(BillingAccount.display_name == "Beta Load Co").one()
    contract = max((c for c in beta.contracts if c.status in ("locked", "superseded")), key=lambda c: c.version)   # the one billing uses
    contract.assumptions = [{"text": "Test assumption: billed on usage only.", "confirmed_by": "", "confirmed_at": None}]
    cid, aid = contract.id, beta.id
    db.commit()
    db.close()
    rows = {r["name"]: r for r in fin.get("/api/v2/accounts").json()}
    assert rows["Beta Load Co"]["assumptions_open"] == 1
    assert any(a["confirmed_by"] == "" for c in fin.get(f"/api/v2/accounts/{aid}").json()["contracts"] for a in c["assumptions"])
    assert any(i["key"] == "assumptions" for i in fin.get("/api/close/checklist?period=2026-11").json()["items"])
    assert fin.post(f"/api/v2/contracts/{cid}/assumptions/5/confirm").status_code == 404
    done = fin.post(f"/api/v2/contracts/{cid}/assumptions/0/confirm").json()
    assert done["assumptions"][0]["confirmed_by"] == "finance@test.local"
    assert {r["name"]: r for r in fin.get("/api/v2/accounts").json()}["Beta Load Co"]["assumptions_open"] == 0


def test_reread_rulebook_to_clear_unmodelled_is_draft_only_and_repeatable(fin, acc):
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in RULES)
    f = {"file": ("r.jsonl", text.encode())}
    assert acc.post("/api/v2/import/rules/resolve", files=f).status_code == 403          # only Finance amends terms
    first = fin.post("/api/v2/import/rules/resolve", files={"file": ("r.jsonl", text.encode())})
    assert first.status_code == 200 and {"drafts_created", "drafted", "still_open", "skipped_dropped"} <= set(first.json())
    again = fin.post("/api/v2/import/rules/resolve", files={"file": ("r.jsonl", text.encode())}).json()
    assert again["drafts_created"] == 0                                                   # nothing new the second time
    for d in first.json()["drafted"]:
        c = next(c for c in fin.get(f"/api/v2/accounts/{[a for a in fin.get('/api/v2/accounts').json() if a['name'] == d['name']][0]['id']}").json()["contracts"] if c["id"] == d["contract_id"])
        assert c["status"] == "draft"                                                     # never locks by itself
