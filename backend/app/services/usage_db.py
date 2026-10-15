"""Pull monthly usage straight from the platform database instead of CSV exports.

The connection string comes from the USAGE_DATABASE_URL environment variable.
What to read is defined by three SQL queries (editable by an admin on the
Settings page) with the same business rules the CSV parsers used:

  loading -> enterprise_id, amount                          (Add Money, COMPLETED, summed)
  upi     -> enterprise_id, txn_count, txn_amount           (DEBIT, COMPLETED, billable categories)
  users   -> enterprise_id, admin_users, spender_users, total_users   (point-in-time snapshot)

Queries may use :period_start and :period_end (inclusive start, exclusive end,
ISO dates). Only a single SELECT/WITH statement is ever executed, inside a
transaction that is always rolled back.
"""
from __future__ import annotations

import calendar
import datetime
import re
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting

QUERY_KEYS = {"loading": "USAGE_QUERY_LOADING", "upi": "USAGE_QUERY_UPI", "users": "USAGE_QUERY_USERS", "extras": "USAGE_QUERY_EXTRAS", "spend": "USAGE_QUERY_SPEND"}

# Defaults mirror the former CSV rules. Table and column names are a starting
# point — an admin adapts them to the real schema on the Settings page.
DEFAULT_QUERIES = {
    "loading": """SELECT enterprise_id, SUM(total_transaction_amount) AS amount
FROM wallet_transactions
WHERE transaction_category = 'Add Money'
  AND status = 'COMPLETED'
  AND transaction_date >= :period_start AND transaction_date < :period_end
GROUP BY enterprise_id""",
    "upi": """SELECT enterprise_id, COUNT(*) AS txn_count, SUM(total_trxn_amount) AS txn_amount,
       SUM(CASE WHEN total_trxn_amount <= 2500 THEN 1 ELSE 0 END) AS cnt_le_2500,
       SUM(CASE WHEN total_trxn_amount > 2500 THEN total_trxn_amount ELSE 0 END) AS amt_gt_2500
FROM upi_transactions
WHERE transaction_type = 'DEBIT'
  AND status = 'COMPLETED'
  AND transaction_category IN ('QR Payment', 'Upi Pay', 'Authorize', 'Collect')
  AND transaction_date >= :period_start AND transaction_date < :period_end
GROUP BY enterprise_id""",
    "users": """SELECT enterprise_id,
       SUM(CASE WHEN role IN ('ADMIN', 'CO_ADMIN') THEN 1 ELSE 0 END) AS admin_users,
       SUM(CASE WHEN role IN ('CHECKER', 'APPROVER', 'MAKER') THEN 1 ELSE 0 END) AS spender_users,
       SUM(CASE WHEN role = 'MAKER' THEN 1 ELSE 0 END) AS maker_users,
       SUM(CASE WHEN role = 'CHECKER' THEN 1 ELSE 0 END) AS checker_users,
       SUM(CASE WHEN role = 'APPROVER' THEN 1 ELSE 0 END) AS approver_users,
       COUNT(*) AS total_users
FROM enterprise_users
WHERE status = 'ACTIVE'
GROUP BY enterprise_id""",
    # Optional. Leave empty if not needed. May return: card_share_pct, upi_spend_after_free,
    # truck_count, driver_count (fleet / spend-based contracts).
    "extras": "",
    # Optional, for spend-based contracts ("first N transactions free, then 1% of spend"). Return one row per
    # transaction: enterprise_id, amount, and (recommended) txn_time. The engine sorts by time, skips each
    # enterprise's own N free transactions (from its contract) and sums the rest.
    "spend": "",
}

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|replace|call|exec|execute|copy|attach|pragma|into)\b", re.I)

_engine = None
_engine_url = None


class UsageDbError(Exception):
    pass


def is_configured() -> bool:
    return bool(get_settings().USAGE_DATABASE_URL)


def describe() -> dict:
    url = get_settings().USAGE_DATABASE_URL
    if not url:
        return {"configured": False, "dialect": "", "host": "", "database": ""}
    from sqlalchemy.engine import make_url
    u = make_url(url)
    return {"configured": True, "dialect": u.get_backend_name(), "host": u.host or "(local file)", "database": u.database or ""}


def _get_engine():
    global _engine, _engine_url
    url = get_settings().USAGE_DATABASE_URL
    if not url:
        raise UsageDbError("USAGE_DATABASE_URL is not set. Add it to the server environment (read-only DB user recommended).")
    if _engine is None or _engine_url != url:
        _engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=0) if not url.startswith("sqlite") else create_engine(url)
        _engine_url = url
    return _engine


def validate_query(sql: str) -> str:
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        raise UsageDbError("Query is empty")
    if ";" in cleaned:
        raise UsageDbError("Only a single statement is allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise UsageDbError("Only SELECT queries are allowed")
    if _FORBIDDEN.search(re.sub(r"'[^']*'", "''", cleaned)):  # ignore words inside string literals
        raise UsageDbError("Query contains a keyword that is not allowed in a read-only query")
    return cleaned


def optional(name: str) -> bool:
    return name in ("extras", "spend")


def get_queries(db: Session) -> dict[str, str]:
    rows = {r.key: r.value for r in db.query(Setting).filter(Setting.key.in_(QUERY_KEYS.values())).all()}
    return {name: (rows[key] if key in rows else DEFAULT_QUERIES[name]) if optional(name) else (rows.get(key) or DEFAULT_QUERIES[name])
            for name, key in QUERY_KEYS.items()}


def save_queries(db: Session, queries: dict[str, str]) -> dict[str, str]:
    for name, sql in queries.items():
        if name not in QUERY_KEYS:
            continue
        sql = "" if (optional(name) and not (sql or "").strip()) else validate_query(sql)
        key = QUERY_KEYS[name]
        row = db.query(Setting).filter(Setting.key == key).first()
        if row is None:
            db.add(Setting(key=key, value=sql))
        else:
            row.value = sql
    db.commit()
    return get_queries(db)


def period_bounds(period: str) -> tuple[str, str]:
    """'2026-05' -> ('2026-05-01', '2026-06-01')."""
    try:
        y, m = int(period[:4]), int(period[5:7])
        start = datetime.date(y, m, 1)
    except (ValueError, IndexError):
        raise UsageDbError(f"Invalid period '{period}', expected YYYY-MM")
    end = start + datetime.timedelta(days=calendar.monthrange(y, m)[1])
    return start.isoformat(), end.isoformat()


def _run(sql: str, period: str, limit: int | None = None) -> tuple[list[dict], float]:
    sql = validate_query(sql)
    start, end = period_bounds(period)
    params = {"period_start": start, "period_end": end}
    engine = _get_engine()
    t0 = time.time()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
            result = conn.execute(text(sql), params)
            rows = [dict(r._mapping) for r in (result.fetchmany(limit) if limit else result.fetchall())]
        except Exception as exc:  # surface DB errors as readable messages
            raise UsageDbError(f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
        finally:
            trans.rollback()
    return rows, round(time.time() - t0, 2)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _eid(row: dict) -> str:
    for k in ("enterprise_id", "enterpriseid", "EnterpriseId"):
        if k in row and row[k] is not None:
            return str(row[k]).strip()
    raise UsageDbError("Query result must include an 'enterprise_id' column")


def fetch_usage(db: Session, period: str) -> tuple[dict, dict, dict]:
    """Returns (loading, upi, users) shaped exactly like the CSV parsers' output,
    so the same aggregation and billing engine run unchanged."""
    q = get_queries(db)
    loading, upi, users = {}, {}, {}
    rows, _ = _run(q["loading"], period)
    for r in rows:
        loading[_eid(r)] = loading.get(_eid(r), 0.0) + _num(r.get("amount"))
    rows, _ = _run(q["upi"], period)
    for r in rows:
        upi[_eid(r)] = {"upi_transactions": _num(r.get("txn_count")), "upi_amount": _num(r.get("txn_amount"))}
    rows, _ = _run(q["users"], period)
    for r in rows:
        users[_eid(r)] = {"admin_users": _num(r.get("admin_users")), "spender_users": _num(r.get("spender_users")),
                          "total_users": _num(r.get("total_users"))}
    return loading, upi, users


def test_queries(db: Session, period: str) -> dict:
    out = {}
    for name, sql in get_queries(db).items():
        if optional(name) and not sql.strip():
            continue
        try:
            rows, secs = _run(sql, period, limit=200)
            out[name] = {"ok": True, "rows": len(rows), "seconds": secs, "columns": list(rows[0].keys()) if rows else [],
                         "sample": [{k: (float(v) if isinstance(v, (int, float)) else str(v)) for k, v in r.items()} for r in rows[:5]]}
        except UsageDbError as exc:
            out[name] = {"ok": False, "error": str(exc)}
    return out


# snapshot column -> (query, result column)
_SNAPSHOT_COLUMNS = {
    "add_money": ("loading", "amount"),
    "upi_txn_count": ("upi", "txn_count"), "upi_txn_amount": ("upi", "txn_amount"),
    "upi_cnt_le_2500": ("upi", "cnt_le_2500"), "upi_amt_gt_2500": ("upi", "amt_gt_2500"),
    "admin_count": ("users", "admin_users"), "maker_count": ("users", "maker_users"),
    "checker_count": ("users", "checker_users"), "approver_count": ("users", "approver_users"),
    "card_share_pct": ("extras", "card_share_pct"), "upi_spend_after_free": ("extras", "upi_spend_after_free"),
    "truck_count": ("extras", "truck_count"), "driver_count": ("extras", "driver_count"),
}


def fetch_snapshot_rows(db: Session, period: str) -> dict[str, dict]:
    """Per-enterprise metrics for the period, keyed by enterprise_id. A column the query returns
    is 0 for enterprises absent from its result (no activity); a column the query does NOT return
    stays None ("not supplied") so the engine can flag it instead of silently billing zero."""
    q = get_queries(db)
    out: dict[str, dict] = {}
    for name in ("loading", "upi", "users", "extras"):
        if not q[name].strip():
            continue
        rows, _ = _run(q[name], period)
        cols = {k.lower() for r in rows[:1] for k in r} if rows else set()
        if not rows:  # an empty result still "supplies" its columns as zero for everyone seen elsewhere
            cols = {c for (qn, c) in _SNAPSHOT_COLUMNS.values() if qn == name}
        for r in rows:
            eid = _eid(r)
            lower = {k.lower(): v for k, v in r.items()}
            row = out.setdefault(eid, {})
            for field, (qn, col) in _SNAPSHOT_COLUMNS.items():
                if qn == name and col in lower:
                    row[field] = row.get(field, 0.0) + _num(lower[col]) if field not in ("card_share_pct",) else _num(lower[col])
        out.setdefault("__supplied__", {}).setdefault(name, sorted(cols))
    if q.get("spend", "").strip():
        _apply_spend(db, period, q["spend"], out)
    supplied = out.pop("__supplied__", {})
    # enterprises seen anywhere get 0 for every column of every query that ran
    for eid, row in out.items():
        for field, (qn, col) in _SNAPSHOT_COLUMNS.items():
            if qn in supplied and col in supplied[qn] and field not in row:
                row[field] = 0.0
    return out


def _free_txns_by_enterprise(db: Session) -> dict[str, int]:
    """free_txns of each enterprise's spend-based contract component (newest version wins)."""
    from app.models import BillingAccount
    out: dict[str, int] = {}
    for a in db.query(BillingAccount).all():
        for c in sorted(a.contracts, key=lambda c: c.version):
            if c.status not in ("locked", "superseded"):
                continue
            for k in c.components:
                if k.component_type == "spend_percent_after_free_txns":
                    for e in a.enterprises:
                        out[e.enterprise_id] = int((k.params or {}).get("free_txns") or 0)
    return out


def _apply_spend(db: Session, period: str, sql: str, out: dict[str, dict]) -> None:
    free = _free_txns_by_enterprise(db)
    if not free:
        return
    rows, _ = _run(sql, period)
    by_ent: dict[str, list[tuple]] = {}
    for i, r in enumerate(rows):
        low = {k.lower(): v for k, v in r.items()}
        eid = _eid(r)
        if eid in free:
            by_ent.setdefault(eid, []).append((str(low.get("txn_time") or low.get("ts") or low.get("transaction_date") or ""), i, _num(low.get("amount"))))
    for eid, n in free.items():
        txns = sorted(by_ent.get(eid, []))
        out.setdefault(eid, {})["upi_spend_after_free"] = round(sum(a for _, _, a in txns[n:]), 2)
