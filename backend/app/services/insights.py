"""Revenue and operations insights, computed in a single pass over issued invoices plus usage snapshots.

Everything is derived from the same documents the ledger uses, so figures always reconcile with the books.
Revenue = taxable value (ex-GST) of issued invoices and debit notes, net of cancelled documents."""
from __future__ import annotations

import datetime
import statistics
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import BillingAccount, BillingException, BillingRun, Client, Invoice, Payment, UsageSnapshot
from app.services import cache, engine2, renewals

GROUP_LABEL = {"A": "Platform / load fee", "B": "UPI fees", "C": "Users & admins", "D": "Modules", "MG": "Minimum top-ups", "CAP": "Cap adjustments"}
OPEN = ("issued", "sent", "partially_paid", "overdue", "disputed", "pushed")


def _prev_period(period: str, n: int = 1) -> str:
    y, m = int(period[:4]), int(period[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _periods(end: str, n: int) -> list[str]:
    return [_prev_period(end, i) for i in range(n - 1, -1, -1)]


def _group_of(line: dict) -> str:
    ct = line.get("component_type") or line.get("metric") or ""
    if ct == "mg_topup":
        return "MG"
    if ct == "cap_adjustment":
        return "CAP"
    if ct == "group_fee":
        return "A"
    if ct in engine2.COMPONENTS:
        return engine2.COMPONENTS[ct][0]
    return {"loading": "A", "upi_transactions": "B", "admin_users": "C", "spender_users": "C", "total_users": "C"}.get(ct, "A")


def _pct(a: float, b: float) -> float | None:
    return round((a - b) / b * 100, 1) if b else None


def overview(db: Session, period: str | None = None, months: int = 12) -> dict:
    return cache.cached(db, "insights", _overview, period, months)


def _overview(db: Session, period: str | None = None, months: int = 12) -> dict:
    today = datetime.date.today()
    invoices = db.query(Invoice).filter(Invoice.status.notin_(("draft", "cancelled"))).all()
    clients = {c.id: c for c in db.query(Client).all()}
    by_period: dict[str, list[Invoice]] = defaultdict(list)
    for i in invoices:
        by_period[i.period].append(i)
    period = period or (max(by_period) if by_period else today.strftime("%Y-%m"))
    window = _periods(period, months)

    # ---------- revenue trend ----------
    trend = []
    for p in window:
        rows = by_period.get(p, [])
        trend.append({"period": p, "taxable": round(sum(i.subtotal for i in rows), 2), "gst": round(sum(i.gst for i in rows), 2),
                      "invoices": len(rows), "customers": len({i.client_id for i in rows})})
    cur, prev = by_period.get(period, []), by_period.get(_prev_period(period), [])
    cur_t, prev_t = sum(i.subtotal for i in cur), sum(i.subtotal for i in prev)
    ttm = sum(t["taxable"] for t in trend)
    kpis = {"period": period, "revenue": round(cur_t, 2), "prev_revenue": round(prev_t, 2), "mom_pct": _pct(cur_t, prev_t),
            "arr_run_rate": round(cur_t * 12, 2), "customers": len({i.client_id for i in cur}), "invoices": len(cur),
            "avg_per_customer": round(cur_t / len({i.client_id for i in cur}), 2) if cur else 0, "ttm_revenue": round(ttm, 2)}

    # ---------- revenue mix by charge group ----------
    mix: dict[str, float] = defaultdict(float)
    for i in cur:
        for l in i.lines or []:
            mix[GROUP_LABEL[_group_of(l)]] += l.get("amount") or 0
    mix_rows = sorted(({"label": k, "amount": round(v, 2), "share": round(v / cur_t * 100, 1) if cur_t else 0} for k, v in mix.items() if abs(v) > 0.005), key=lambda r: -r["amount"])

    # ---------- concentration ----------
    per_client = defaultdict(float)
    for i in cur:
        per_client[i.client_id] += i.subtotal
    ranked = sorted(per_client.items(), key=lambda kv: -kv[1])
    top = [{"customer": clients[c].name if c in clients else "?", "amount": round(v, 2), "share": round(v / cur_t * 100, 2) if cur_t else 0} for c, v in ranked[:10]]
    shares = [v / cur_t for _, v in ranked] if cur_t else []
    conc = {"top": top, "top10_share": round(sum(v for _, v in ranked[:10]) / cur_t * 100, 1) if cur_t else 0,
            "top1_share": round(ranked[0][1] / cur_t * 100, 1) if ranked and cur_t else 0, "hhi": round(sum(s * s for s in shares) * 10000),
            "customers": len(ranked), "median_bill": round(statistics.median([v for _, v in ranked]), 2) if ranked else 0}

    # ---------- movers ----------
    pc = defaultdict(float)
    for i in prev:
        pc[i.client_id] += i.subtotal
    new = [{"customer": clients[c].name, "amount": round(v, 2)} for c, v in per_client.items() if c not in pc and c in clients]
    lost = [{"customer": clients[c].name, "amount": round(v, 2)} for c, v in pc.items() if c not in per_client and c in clients]
    deltas = [(c, per_client[c] - pc[c], per_client[c], pc[c]) for c in per_client if c in pc]
    up = sorted([d for d in deltas if d[1] > 0], key=lambda d: -d[1])[:8]
    down = sorted([d for d in deltas if d[1] < 0], key=lambda d: d[1])[:8]
    fmt = lambda d: {"customer": clients[d[0]].name if d[0] in clients else "?", "delta": round(d[1], 2), "now": round(d[2], 2), "before": round(d[3], 2), "pct": _pct(d[2], d[3])}
    movers = {"new": sorted(new, key=lambda r: -r["amount"])[:8], "new_total": round(sum(r["amount"] for r in new), 2), "lost": sorted(lost, key=lambda r: -r["amount"])[:8],
              "lost_total": round(sum(r["amount"] for r in lost), 2), "up": [fmt(d) for d in up], "down": [fmt(d) for d in down],
              "expansion": round(sum(d[1] for d in deltas if d[1] > 0), 2), "contraction": round(sum(d[1] for d in deltas if d[1] < 0), 2)}
    # net revenue retention of last month's customers
    movers["nrr_pct"] = round(sum(per_client.get(c, 0) for c in pc) / sum(pc.values()) * 100, 1) if pc else None
    movers["logo_churn_pct"] = round(len(lost) / len(pc) * 100, 1) if pc else None

    # ---------- usage, take rate ----------
    snaps = defaultdict(lambda: {"add_money": 0.0, "upi": 0.0, "makers": 0.0, "admins": 0.0})
    for s in db.query(UsageSnapshot).filter(UsageSnapshot.period.in_(window)).all():
        a = snaps[s.period]
        a["add_money"] += s.add_money or 0; a["upi"] += s.upi_txn_count or 0; a["makers"] += s.maker_count or 0; a["admins"] += s.admin_count or 0
    usage = []
    for t in trend:
        s = snaps.get(t["period"])
        usage.append({"period": t["period"], "add_money": round(s["add_money"], 2) if s else None, "upi_txns": round(s["upi"]) if s else None,
                      "makers": round(s["makers"]) if s else None, "take_rate_pct": round(t["taxable"] / s["add_money"] * 100, 3) if s and s["add_money"] else None,
                      "revenue_per_upi_txn": round(t["taxable"] / s["upi"], 2) if s and s["upi"] else None})

    # ---------- floors (minimum top-ups) ----------
    topups, at_floor = [], defaultdict(int)
    for p in _periods(period, 3):
        for i in by_period.get(p, []):
            if any((l.get("component_type") == "mg_topup") for l in i.lines or []):
                at_floor[i.client_id] += 1
    for i in cur:
        t = sum(l["amount"] for l in i.lines or [] if l.get("component_type") == "mg_topup")
        if t:
            topups.append({"customer": clients[i.client_id].name if i.client_id in clients else "?", "topup": round(t, 2), "bill": round(i.subtotal, 2)})
    floors = {"topup_total": round(sum(t["topup"] for t in topups), 2), "accounts": len(topups), "share_of_revenue": round(sum(t["topup"] for t in topups) / cur_t * 100, 1) if cur_t else 0,
              "top": sorted(topups, key=lambda t: -t["topup"])[:8],
              "stuck_at_minimum": [{"customer": clients[c].name, "months": n} for c, n in at_floor.items() if n >= 3 and c in clients][:12]}

    # ---------- risk: declining or fixed-only customers ----------
    hist = defaultdict(dict)
    for p in _periods(period, 4):
        for i in by_period.get(p, []):
            hist[i.client_id][p] = hist[i.client_id].get(p, 0) + i.subtotal
    at_risk = []
    for c, h in hist.items():
        prior = [h.get(p, 0) for p in _periods(_prev_period(period), 3)]
        base = sum(prior) / 3 if any(prior) else 0
        now = h.get(period, 0)
        if base > 0 and now < base * 0.7 and c in clients:
            at_risk.append({"customer": clients[c].name, "now": round(now, 2), "avg_prior_3m": round(base, 2), "drop_pct": round((now - base) / base * 100, 1)})
    risk = {"declining": sorted(at_risk, key=lambda r: r["drop_pct"])[:10], "declining_revenue_at_stake": round(sum(r["avg_prior_3m"] - r["now"] for r in at_risk), 2)}

    # ---------- collections ----------
    open_inv = [i for i in invoices if i.status in OPEN and (i.balance_due or 0) > 0.005]
    outstanding = sum(i.balance_due for i in open_inv)
    overdue = sum(i.balance_due for i in open_inv if i.due_date and i.due_date < today.isoformat() and not i.disputed)
    last90 = sum(i.total for i in invoices if (i.issued_at or i.created_at).date() >= today - datetime.timedelta(days=90))
    delays = []
    inv_by_id = {i.id: i for i in invoices}
    for p in db.query(Payment).filter(Payment.invoice_id.isnot(None)).all():
        i = inv_by_id.get(p.invoice_id)
        try:
            if i and i.due_date:
                delays.append((datetime.date.fromisoformat(p.date[:10]) - datetime.date.fromisoformat(i.due_date)).days)
        except ValueError:
            pass
    collections = {"outstanding": round(outstanding, 2), "overdue": round(overdue, 2), "overdue_pct": round(overdue / outstanding * 100, 1) if outstanding else 0,
                   "dso_days": round(outstanding / last90 * 90, 1) if last90 else None, "avg_days_to_pay_vs_due": round(sum(delays) / len(delays), 1) if delays else None,
                   "on_time_pct": round(sum(1 for d in delays if d <= 0) / len(delays) * 100, 1) if delays else None, "payments_sampled": len(delays)}

    # ---------- cohorts by go-live year ----------
    coh: dict[str, dict] = defaultdict(lambda: {"accounts": 0, "revenue": 0.0})
    for a in db.query(BillingAccount).all():
        y = (a.go_live_date or "unknown")[:4]
        coh[y]["accounts"] += 1
        coh[y]["revenue"] += per_client.get(a.client_id, 0)
    cohorts = [{"cohort": k, "accounts": v["accounts"], "revenue": round(v["revenue"], 2), "share": round(v["revenue"] / cur_t * 100, 1) if cur_t else 0} for k, v in sorted(coh.items())]

    # ---------- data-quality / leakage ----------
    ex = defaultdict(int)
    for e in db.query(BillingException).filter(BillingException.status == "open").all():
        ex[e.code] += 1
    accounts_n = db.query(BillingAccount).count()
    unmapped = sum(1 for a in db.query(BillingAccount).all() if any(e.enterprise_id.startswith(("ROW-", "UNMAPPED")) for e in a.enterprises))
    leakage = {"open_exceptions": sum(ex.values()), "by_code": sorted(({"code": k, "count": v} for k, v in ex.items()), key=lambda r: -r["count"])[:10],
               "unmapped_accounts": unmapped, "accounts": accounts_n,
               "withheld_last_run": len(((db.query(BillingRun).order_by(BillingRun.id.desc()).first() or BillingRun()).summary or {}).get("skipped", []))}

    # ---------- forecast ----------
    last3 = [t["taxable"] for t in trend[-3:] if t["taxable"]]
    slope = (last3[-1] - last3[0]) / (len(last3) - 1) if len(last3) > 1 else 0
    base = sum(last3) / len(last3) if last3 else 0
    ren = renewals.upcoming(db, today, 90)
    forecast = {"next_month_estimate": round(max(base + slope / 2, 0), 2), "method": "trailing 3-month average with trend", "renewals_due_90d": round(sum(r["expected_amount"] for r in ren), 2),
                "renewals_count": len(ren)}
    return {"kpis": kpis, "trend": trend, "mix": mix_rows, "concentration": conc, "movers": movers, "usage": usage, "floors": floors, "risk": risk,
            "collections": collections, "cohorts": cohorts, "leakage": leakage, "forecast": forecast, "periods": sorted(by_period)}
