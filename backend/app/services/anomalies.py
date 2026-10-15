"""Pre-approval checks on a freshly generated billing run. Findings are stored
on the run and shown to the approver; 'high' findings must be acknowledged."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Client, Invoice


def previous_subtotal(db: Session, client_id: int, period: str) -> float | None:
    prev = (db.query(Invoice)
            .filter(Invoice.client_id == client_id, Invoice.period < period, Invoice.status != "cancelled")
            .order_by(Invoice.period.desc(), Invoice.id.desc()).first())
    return prev.subtotal if prev else None


def detect(db: Session, period: str, clients: list[Client], usage_by_client: dict, unknown_ids: list[str],
           billed: dict[str, float], skipped: list[dict], swing_pct: float) -> list[dict]:
    """billed: enterprise_id -> subtotal of invoices generated in this run."""
    out: list[dict] = []
    by_eid = {c.enterprise_id: c for c in clients}
    skip_reason = {s["enterprise_id"]: s["reason"] for s in skipped}

    for eid in unknown_ids:
        out.append({"severity": "high", "code": "unknown_enterprise", "enterprise_id": eid,
                    "message": f"Usage found for {eid}, which is not a registered customer — it will not be billed."})

    for eid, usage in usage_by_client.items():
        c = by_eid.get(eid)
        if c is None or eid in billed:
            continue
        used = (usage.get("loading") or 0) > 0 or (usage.get("upi_transactions") or 0) > 0
        if used and skip_reason.get(eid) == "no locked commercial":
            out.append({"severity": "high", "code": "usage_no_terms", "enterprise_id": eid,
                        "message": f"{c.name} has usage this month but no locked billing terms — revenue is not being billed."})

    for c in clients:
        if (c.status or "active") != "active":
            continue
        if c.enterprise_id not in billed and skip_reason.get(c.enterprise_id) == "zero usage / no billable lines":
            prev = previous_subtotal(db, c.id, period)
            if prev:
                out.append({"severity": "medium", "code": "zero_usage", "enterprise_id": c.enterprise_id,
                            "message": f"{c.name} had no billable usage this month (last invoice ₹{prev:,.0f}). Possible churn or a data gap."})

    for eid, subtotal in billed.items():
        c = by_eid[eid]
        prev = previous_subtotal(db, c.id, period)
        if prev and prev > 0:
            change = (subtotal - prev) / prev * 100
            if abs(change) >= swing_pct:
                out.append({"severity": "medium", "code": "swing", "enterprise_id": eid,
                            "message": f"{c.name}: invoice moved {change:+.0f}% vs previous (₹{prev:,.0f} → ₹{subtotal:,.0f})."})
        if c.credit_limit and c.credit_limit > 0:
            exposure = sum(i.balance_due or 0 for i in db.query(Invoice).filter(
                Invoice.client_id == c.id, Invoice.status.in_(["issued", "sent", "partially_paid", "overdue", "disputed"])))
            if exposure + subtotal > c.credit_limit:
                out.append({"severity": "low", "code": "credit_limit", "enterprise_id": eid,
                            "message": f"{c.name}: outstanding ₹{exposure:,.0f} plus this invoice exceeds the credit limit of ₹{c.credit_limit:,.0f}."})

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda a: (order[a["severity"]], a["enterprise_id"]))
    return out
