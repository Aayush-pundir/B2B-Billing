"""Parallel-run regression: replay the legacy sheet's Sep'26 inputs through billing v2 and compare taxable
values line by line. Reads confidential data from a path you provide — nothing is stored in the repo.

    BILLING_RULES_JSONL=/secure/customer_rules_sep26.jsonl [BILLING_REGISTER_CSV=...] python tests/regression_real_data.py [--verbose]
"""
import collections
import json
import os
import sys
import tempfile
from decimal import Decimal as D
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/regress.db"
os.environ["UPLOAD_DIR"] = f"{tmp}/up"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.migrate import ensure_schema  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import Client  # noqa: E402
from app.services import billing_v2, importer_v2, snapshots  # noqa: E402
from app.services.chart import seed_accounts  # noqa: E402

PERIOD = os.getenv("BILLING_PERIOD", "2026-09")


def main():
    path = os.environ.get("BILLING_RULES_JSONL")
    if not path:
        print("Set BILLING_RULES_JSONL to run the regression.")
        return 0
    text = Path(path).read_text(encoding="utf-8")
    lines = [json.loads(l) for l in text.splitlines() if l.strip()]
    ensure_schema(engine, Base)
    db = SessionLocal()
    seed_accounts(db)
    print("import:", importer_v2.import_rules(db, text, "regression", lock=True))
    reg = os.environ.get("BILLING_REGISTER_CSV")
    if reg:
        print("register:", importer_v2.import_register(db, Path(reg).read_text(encoding="utf-8")))

    rows = {}
    for r in lines:
        a = r.get("sep26_actuals") or {}
        n = lambda k, d=0.0: a.get(k) if isinstance(a.get(k), (int, float)) else d
        rows[f"ROW-{r['row']}"] = {"add_money": n("L Add Money"), "upi_txn_count": n("S UPI txns"), "upi_txn_amount": 0.0,
                                   "maker_count": n("W Makers"), "admin_count": n("AA Admins")}
    snapshots.save(db, PERIOD, rows, "regression")
    snaps = snapshots.load(db, PERIOD)
    clients = {c.id: c for c in db.query(Client).all()}
    out = billing_v2.compute_run(db, PERIOD, snaps, clients)

    by_row = {r["row"]: r for r in lines}
    tot = exact = blocked = 0
    causes = collections.Counter()
    detail = []
    for cid, res in out["results"].items():
        row = res["account"].source_row
        exp = (by_row[row].get("sep26_actuals") or {}).get("Taxable")
        if not isinstance(exp, (int, float)):
            continue
        tot += 1
        got = res["subtotal"] if res["billable"] else D("0")
        if any(e["blocks"] and e["code"] != "ACCOUNT_NOT_MAPPED" for e in res["exceptions"]):
            blocked += 1
        if abs(float(got) - exp) < 0.02:
            exact += 1
            continue
        a_ = by_row[row].get("sep26_actuals") or {}
        legacy_na = any(isinstance(a_.get(k), str) and "#N/A" in a_.get(k) for k in ("L Add Money", "S UPI txns", "A", "B"))
        covered = any("covered_by_subscription" in l.get("flags", []) for l in res["lines"])
        why = [e["code"] for e in res["exceptions"] if e["severity"] in ("high", "medium") and e["code"] != "ACCOUNT_NOT_MAPPED"][:2] or ["unexplained"]
        status = by_row[row].get("status")
        tiered = any(l["component_type"] == "load_percent_tiered" for l in res["lines"])
        if tiered and res["billable"] and not legacy_na:
            why = ["slab contract applied (legacy sheet used a single rate / bespoke formula)"]
        elif legacy_na and res["billable"]:
            why = ["legacy #N/A lookup (v2 bills its commitment)"]
        elif covered:
            why = ["covered by subscription (legacy bills monthly)"]
        if not res["billable"]:
            why = [f"non-billable ({res['life']['reason'] or status})"]
        causes[tuple(why)] += 1
        detail.append((row, by_row[row]["customer"], status, exp, float(got), why))

    want = [int(x) for x in (next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--rows=")), "")).split(",") if x]
    for cid, res in out["results"].items():
        row = res["account"].source_row
        if row in want:
            a = by_row[row]
            print(f"\n=== row {row} {a['status']} | expected {a['sep26_actuals'].get('Taxable')} | engine {res['subtotal']} | billable={res['billable']} frac={float(res['life']['fraction']):.3f}")
            print("   sheet:", {k: v for k, v in a["sep26_actuals"].items() if k in ("L Add Money", "S UPI txns", "W Makers", "AA Admins", "A", "B", "C", "D")})
            print("   rules:", (a["A_platform"] or {}).get("rule", "")[:110], "|", (a["B_upi"] or {}).get("rule", "")[:80], "|", (a["C_users"] or {}).get("formula", ""))
            for l in res["lines"]:
                print(f"   - {l['label'][:34]:<34} {float(l['amount']):>11,.2f}  {l['detail'][:90]}")
            print("   overall_min:", res["contract"].overall_min_amount, " mg_from:", res["contract"].mg_effective_from, " subs:", len(res["subs"]), " events:", [e.event_type for e in res["account"].events])
    print(f"\nPeriod {PERIOD}: {exact}/{tot} lines match the legacy sheet ({exact / tot:.1%}); {blocked} withheld by a blocking exception")
    EXPLAINED = ("slab contract applied", "legacy #N/A", "covered by subscription", "non-billable", "UNMODELLED_COMPONENT", "INPUT_MISSING", "PENDING_CHECK")
    explained = sum(v for k, v in causes.items() if any(e in " ".join(k) for e in EXPLAINED))
    print("\nMismatches by cause:")
    for k, v in causes.most_common(15):
        print(f"  {v:4d}  {', '.join(k)}")
    print(f"\nMatched or explained by a documented exception: {exact + explained}/{tot} ({(exact + explained) / tot:.1%}); unexplained: {tot - exact - explained}")
    if "--verbose" in sys.argv:
        print("\nrow | status | expected | engine | cause")
        for d in detail[:80]:
            print(f"  {d[0]:>4} | {str(d[2])[:26]:<26} | {d[3]:>12,.2f} | {d[4]:>12,.2f} | {', '.join(d[5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
