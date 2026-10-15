"""Usage snapshots: frozen, per-enterprise monthly metrics. Billing runs read snapshots,
never live tables, so a re-run of an approved month reproduces the same numbers."""
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.models import UsageSnapshot

FIELDS = ["add_money", "upi_txn_count", "upi_txn_amount", "upi_cnt_le_2500", "upi_amt_gt_2500", "upi_spend_after_free",
          "card_share_pct", "maker_count", "admin_count", "checker_count", "approver_count", "truck_count", "driver_count"]


def save(db: Session, period: str, rows: dict[str, dict], source: str) -> dict:
    """Upsert snapshot rows. Frozen rows are never overwritten; manual fields survive a refresh."""
    created = updated = skipped_frozen = 0
    for eid, vals in rows.items():
        snap = db.query(UsageSnapshot).filter(UsageSnapshot.period == period, UsageSnapshot.enterprise_id == eid).first()
        if snap is None:
            snap = UsageSnapshot(period=period, enterprise_id=eid, source=source, manual_fields=[])
            db.add(snap)
            created += 1
        elif snap.frozen:
            skipped_frozen += 1
            continue
        else:
            updated += 1
        manual = set(snap.manual_fields or [])
        for f in FIELDS:
            if f in vals and f not in manual:
                setattr(snap, f, vals[f])
        snap.source = source if not manual else "mixed"
        snap.extracted_at = datetime.datetime.utcnow()
    db.commit()
    return {"created": created, "updated": updated, "skipped_frozen": skipped_frozen}


def load(db: Session, period: str) -> dict[str, dict]:
    out = {}
    for s in db.query(UsageSnapshot).filter(UsageSnapshot.period == period).all():
        out[s.enterprise_id] = {**{f: getattr(s, f) for f in FIELDS}, "_manual": list(s.manual_fields or []), "_source": s.source}
    return out


def set_manual(db: Session, period: str, enterprise_id: str, values: dict, user_email: str) -> UsageSnapshot:
    snap = db.query(UsageSnapshot).filter(UsageSnapshot.period == period, UsageSnapshot.enterprise_id == enterprise_id).first()
    if snap is None:
        snap = UsageSnapshot(period=period, enterprise_id=enterprise_id, source="manual", manual_fields=[])
        db.add(snap)
    if snap.frozen:
        raise ValueError("This period's usage is frozen (the run was approved). Create a correcting run instead.")
    manual = set(snap.manual_fields or [])
    for k, v in values.items():
        if k in FIELDS:
            setattr(snap, k, None if v in (None, "") else float(v))
            manual.add(k)
    snap.manual_fields = sorted(manual)
    db.commit()
    return snap


def freeze(db: Session, period: str) -> int:
    n = 0
    for s in db.query(UsageSnapshot).filter(UsageSnapshot.period == period, UsageSnapshot.frozen.is_(False)).all():
        s.frozen = True
        n += 1
    db.commit()
    return n


def from_csv(loading: dict, upi: dict, users: dict) -> dict[str, dict]:
    """Build snapshot rows from the CSV fallback parsers."""
    rows: dict[str, dict] = {}
    for eid in set(loading) | set(upi) | set(users):
        r: dict = {"add_money": loading.get(eid, 0.0)}
        u = upi.get(eid, {})
        r["upi_txn_count"], r["upi_txn_amount"] = u.get("upi_transactions", 0), u.get("upi_amount", 0.0)
        if eid in users or users:
            x = users.get(eid, {})
            r["admin_count"], r["maker_count"] = x.get("admin_users", 0), x.get("maker_users", 0)
            r["checker_count"], r["approver_count"] = x.get("checker_users", 0), x.get("approver_users", 0)
        rows[eid] = r
    return rows
