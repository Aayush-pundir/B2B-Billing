"""End-to-end rehearsal at real scale, through the HTTP API, with no outside services:

  your rulebook + register -> a stand-in platform database holding the legacy September usage -> mapping assistant ->
  calculate from that database -> review -> lock -> customers pulled from a stand-in Zoho -> background push -> checks.

    BILLING_RULES_JSONL=... BILLING_REGISTER_CSV=... python tests/e2e_real_scale.py

Reads confidential data from the paths you give it and writes nothing but a temp folder."""
import csv
import io
import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
platform_db = tmp / "platform.db"
os.environ.update({"DATABASE_URL": os.environ.get("E2E_DATABASE_URL") or f"sqlite:///{tmp}/app.db", "UPLOAD_DIR": str(tmp / "up"), "DISABLE_SCHEDULER": "1", "JWT_SECRET": "e2e",
                   "USAGE_DATABASE_URL": f"sqlite:///{platform_db}", "ZOHO_CLIENT_ID": "cid", "ZOHO_CLIENT_SECRET": "secret", "ZOHO_REFRESH_TOKEN": "refresh",
                   "ZOHO_ORG_ID": "ORG1", "ZOHO_API_BASE": "https://zoho.test/books/v3", "ZOHO_ACCOUNTS_BASE": "https://accounts.zoho.test", "ZOHO_PUSH_PAUSE_SECONDS": "0"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import zoho, zoho_push  # noqa: E402
from fake_zoho import FakeZoho  # noqa: E402

random.seed(42)
t0 = time.time()
lap = lambda label: print(f"  [{time.time() - t0:6.1f}s] {label}")


def login(email):
    c = TestClient(app)
    assert c.post("/auth/dev-login", json={"email": email, "role": "finance" if "fin" in email else "accounts"}).status_code == 200
    return c


def main():
    rules_path, reg_path = os.environ.get("BILLING_RULES_JSONL"), os.environ.get("BILLING_REGISTER_CSV")
    if not rules_path:
        print("Set BILLING_RULES_JSONL (and BILLING_REGISTER_CSV) to run the rehearsal.")
        return 0
    text = Path(rules_path).read_text(encoding="utf-8")
    rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    with TestClient(app):
        fin, fin2, acc = login("fin@e2e.local"), login("fin2@e2e.local"), login("acc@e2e.local")
        print("1. Import")
        r = fin.post("/api/v2/import/rules?lock=true", files={"file": ("r.jsonl", text.encode())}).json()
        print("  rulebook:", {k: r[k] for k in ("lines", "accounts_created", "unmodelled_components")})
        if reg_path:
            print("  register:", fin.post("/api/v2/import/register", files={"file": ("r.csv", Path(reg_path).read_bytes())}).json())
        lap("imported")

        print("2. Stand-in platform database with the legacy September usage")
        con = sqlite3.connect(platform_db)
        con.executescript("""CREATE TABLE wallet_transactions(enterprise_id TEXT, total_transaction_amount REAL, transaction_category TEXT, status TEXT, transaction_date TEXT);
            CREATE TABLE upi_transactions(enterprise_id TEXT, total_trxn_amount REAL, transaction_type TEXT, status TEXT, transaction_category TEXT, transaction_date TEXT);
            CREATE TABLE enterprise_users(enterprise_id TEXT, role TEXT, status TEXT);
            CREATE TABLE enterprises(enterprise_id TEXT, name TEXT, gstin TEXT);""")
        platform_list = []
        for r in rows:
            a = r.get("sep26_actuals") or {}
            num = lambda k: a.get(k) if isinstance(a.get(k), (int, float)) else 0
            eid = f"ENT{r['row']:04d}"
            for month_day in ("2026-09-15", "2026-10-15"):                 # the same usage in both months
                if num("L Add Money") > 0:
                    con.execute("INSERT INTO wallet_transactions VALUES (?,?,?,?,?)", (eid, num("L Add Money"), "Add Money", "COMPLETED", month_day))
                con.executemany("INSERT INTO upi_transactions VALUES (?,?,?,?,?,?)", [(eid, 100.0, "DEBIT", "COMPLETED", "Upi Pay", month_day)] * int(num("S UPI txns")))
            con.executemany("INSERT INTO enterprise_users VALUES (?,?,?)", [(eid, "MAKER", "ACTIVE")] * int(num("W Makers")) + [(eid, "ADMIN", "ACTIVE")] * int(num("AA Admins")))
            name = r["customer"]                                    # the platform spells names its own way
            name = re.sub(r"(?i)private limited", "PVT LTD", name).title() if random.random() < 0.6 else name.upper()
            if random.random() < 0.04:
                name = "Totally Different Trading Name " + str(r["row"])
            platform_list.append((eid, name))
        con.commit()
        con.close()
        lap(f"platform database written ({len(platform_list)} enterprises)")

        print("3. Mapping assistant")
        buf = io.StringIO()
        csv.writer(buf).writerows([["EnterpriseId", "Enterprise Name"], *platform_list])
        sug = fin.post("/api/v2/import/mapping/suggest", files={"file": ("p.csv", buf.getvalue().encode())}).json()
        print("  suggestions:", sug["summary"])
        imp = fin.post("/api/v2/import/mapping", files={"file": ("m.csv", sug["csv"].encode())}).json()
        print("  imported mapping:", {"mapped": imp["mapped"], "errors": len(imp["errors"])})
        # the rest were 'close' or unmatched: finance fills those in by hand (here: from the true IDs)
        truth = {r["row"]: f"ENT{r['row']:04d}" for r in rows}
        left = [r for r in csv.DictReader(io.StringIO(sug["csv"])) if not r["enterprise_id (use ; to add more)"] and r["source_row"]]
        fixed = io.StringIO()
        w = csv.writer(fixed)
        w.writerow(["source_row", "customer", "enterprise_id (use ; to add more)"])
        for r in left:
            w.writerow([r["source_row"], r["customer"], truth[int(r["source_row"])]])
        print("  filled by hand:", fin.post("/api/v2/import/mapping", files={"file": ("m2.csv", fixed.getvalue().encode())}).json()["mapped"])
        lap("mapped")

        print("4. Month-close checklist")
        chk = fin.get("/api/close/checklist?period=2026-09").json()
        for i in chk["items"]:
            print(f"  {i['status']:5s} {i['label']}: {i['detail'][:110]}")
        lap("checklist")

        print("4b. Zoho (stand-in): connection and customer master")
        states = {"09": "UP", "27": "MH", "29": "KA", "07": "DL", "33": "TN"}
        contacts = []
        for r in rows:
            if random.random() < 0.05:
                continue                                           # a few customers do not exist in Zoho yet: created on push
            code = random.choice(list(states))
            contacts.append({"contact_id": f"Z{r['row']}", "contact_name": (r.get("zoho_name") or r["customer"]).upper(), "contact_type": "customer",
                             "gst_no": f"{code}AAACE{r['row']:04d}A1Z5", "place_of_contact": states[code], "email": f"acct{r['row']}@example.test"})
        fake = FakeZoho(contacts=contacts)
        zoho.requests.post, zoho.requests.request, zoho.time.sleep = fake.post, fake.request, (lambda *_: None)
        zoho.reset_caches()
        print("  connection:", fin.post("/api/close/zoho/test").json()["detail"])
        dry = fin.post("/api/zoho/sync-customers?dry_run=true").json()
        sync = fin.post("/api/zoho/sync-customers").json()
        print(f"  customers: zoho has {sync['zoho_customers']} | linked {sync['linked_by_gstin'] + sync['linked_by_name']} (dry run said {dry['linked_by_gstin'] + dry['linked_by_name']}) | filled {sync['filled']} | not found in Zoho {sync['unmatched_account_count']}")

        m = [i for i in fin.get("/api/close/checklist?period=2026-09").json()["items"] if i["key"] == "master"][0]
        print(f"  after the pull, customer master check: {m['status']} - {m['detail'][:100]}")

        print("5. Calculate from the platform database (2026-09, the month the legacy sheet also billed)")
        res = acc.post("/api/billing/calculate-db", data={"period": "2026-09", "tds_rate": 0})
        assert res.status_code == 200, res.text
        run = res.json()
        rid = run["run_id"]
        detail = fin.get(f"/api/billing/runs/{rid}").json()
        legacy = {r["customer"]: (r.get("sep26_actuals") or {}).get("Taxable") for r in rows}
        got = {i["enterprise_id"]: i["subtotal"] for i in detail["invoices"]}
        comparable = [r for r in rows if isinstance(legacy[r["customer"]], (int, float))]
        exact = sum(1 for r in comparable if abs(got.get(truth[r["row"]], 0.0) - legacy[r["customer"]]) < 0.02)
        print(f"  invoices: {len(detail['invoices'])} | withheld: {len(run['skipped'])} | status: {run['status']} | same as the legacy sheet: {exact} of {len(comparable)}")
        reasons = {}
        for s in run["skipped"]:
            k = re.sub(r"[A-Za-z0-9 .&'()-]*: ", "", s["reason"])[:60]
            reasons[k] = reasons.get(k, 0) + 1
        print("  withheld because:", dict(sorted(reasons.items(), key=lambda x: -x[1])[:5]))
        lap("calculated")

        print("6. Review queue")
        q = fin2.get(f"/api/close/runs/{rid}/review").json()
        kinds = {}
        for i in q["items"]:
            for x in i["reasons"]:
                kinds[x["code"].split(":")[0]] = kinds.get(x["code"].split(":")[0], 0) + 1
        print(f"  {q['counts']['total']} queued of {len(detail['invoices'])} invoices; by reason:", dict(sorted(kinds.items(), key=lambda x: -x[1])[:8]))
        assert fin2.post(f"/api/billing/runs/{rid}/approve", json={"acknowledge_high": True}).status_code == 409, "must not lock with an open queue"
        t = time.time()
        fin2.post(f"/api/close/runs/{rid}/review-bulk", json={"item_ids": [i["id"] for i in q["items"]]})
        ap = fin2.post(f"/api/billing/runs/{rid}/approve", json={"acknowledge_high": True})
        print(f"  checked and locked in {time.time() - t:.1f}s: {ap.status_code} {ap.json().get('status')}")
        lap("locked")

        print("7. Push to Zoho")
        t = time.time()
        st = fin.post(f"/api/close/runs/{rid}/zoho/start").json()
        zoho_push.wait(rid, timeout=300)
        st = fin.get(f"/api/close/runs/{rid}/zoho/status").json()
        print(f"  push: {st['pushed']} of {st['total']} invoices in {time.time() - t:.1f}s | failed {st['failed']} | run status {st['run_status']} | simulated {st['simulated']}")
        for e in st["errors"][:5]:
            print("   ", e["invoice_no"], e["reason"][:140])
        taxes = {}
        for inv in fake.invoices.values():
            key = (inv["line_items"][0]["tax_id"], inv.get("place_of_supply", "") == "UP")
            taxes[key] = taxes.get(key, 0) + 1
        print("  taxes sent (tax id, supply in UP):", taxes, "| customers created on the fly:", len([c for c in fake.contacts if c.startswith("C")]))
        again = fin.post(f"/api/close/runs/{rid}/zoho/start").json()
        zoho_push.wait(rid)
        print("  second push created", len(fake.invoices) - st["pushed"], "extra invoices (must be 0)")
        lap("pushed")

        print("8. Next month (2026-10) with the same usage: compared with the legacy figures")
        r2 = acc.post("/api/billing/calculate-db", data={"period": "2026-10", "tds_rate": 0}).json()
        q2 = fin2.get(f"/api/close/runs/{r2['run_id']}/review").json()
        first = sum(1 for i in q2["items"] if any(x["code"] == "first_invoice" for x in i["reasons"]))
        print(f"  queued {q2['counts']['total']} of {len(r2['invoices'])}; 'first invoice' flags: {first}")
        why = {}
        for i in q2["items"]:
            for x in i["reasons"]:
                k = x["code"].split(":")[1] if x["code"].startswith("exception:") else x["code"]
                why[k] = why.get(k, 0) + 1
        print("  by reason:", dict(sorted(why.items(), key=lambda x: -x[1])))
        lap("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
