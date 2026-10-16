"""Month-end review queue. After a run is calculated, every invoice that is unusual is queued for a person to check
(first invoice, part-month, minimum or cap applied, big swing, manual inputs, open exceptions, group fees, new contract),
plus a small random sample of the clean ones. The run cannot be locked until the queue is cleared."""
from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models import BaselineAmount, Invoice, ReviewItem

SPOT_PCT = 5  # share of otherwise-clean invoices pulled for a spot-check
LABELS = {
    "first_invoice": ("First invoice for this customer", "medium"),
    "partial_month": ("Part-month billing", "medium"),
    "minimum_applied": ("Minimum commitment applied", "medium"),
    "cap_applied": ("Monthly cap applied", "medium"),
    "swing_up": ("Up sharply on last month", "high"),
    "swing_down": ("Down sharply on last month", "high"),
    "manual_input": ("Usage entered by hand", "medium"),
    "group_applied": ("Group fee / group minimum", "medium"),
    "new_contract": ("New contract version this month", "medium"),
    "subscription_covered": ("Platform fee covered by a subscription", "low"),
    "not_billed": ("Nothing billed although a commitment exists", "high"),
}


def _reason(code: str, label: str | None = None, severity: str | None = None) -> dict:
    default_label, default_sev = LABELS.get(code, (code, "medium"))
    return {"code": code, "label": label or default_label, "severity": severity or default_sev}


def _spot(run_id: int, client_id: int) -> bool:
    """Deterministic, so reopening the page never reshuffles which invoices are sampled."""
    h = int(hashlib.sha1(f"{run_id}:{client_id}".encode()).hexdigest()[:8], 16)
    return h % 100 < SPOT_PCT


def previous_subtotals(db: Session, period: str) -> dict[int, float]:
    """client_id -> taxable value of that customer's most recent earlier invoice."""
    out: dict[int, float] = {}
    for inv in (db.query(Invoice).filter(Invoice.period < period, Invoice.status != "cancelled", Invoice.doc_type == "invoice")
                .order_by(Invoice.period, Invoice.id)):
        out[inv.client_id] = inv.subtotal
    # before this system had issued anything, the previous system's last figure is the comparison
    for b in db.query(BaselineAmount).filter(BaselineAmount.period < period).order_by(BaselineAmount.period):
        out.setdefault(b.client_id, b.taxable)
    return out


def build(db: Session, run, v2_results: dict[int, dict], swing_pct: float) -> dict:
    """Create the queue for a freshly calculated run. v2_results is keyed by client_id (engine v2 output)."""
    db.query(ReviewItem).filter(ReviewItem.run_id == run.id).delete()
    prev = previous_subtotals(db, run.period)
    flagged = spot = 0
    for inv in run.invoices:
        if inv.status == "cancelled":
            continue
        reasons: list[dict] = []
        before = prev.get(inv.client_id)
        if before is None:
            reasons.append(_reason("first_invoice"))
        elif before > 0:
            change = (inv.subtotal - before) / before * 100
            if abs(change) >= swing_pct:
                reasons.append(_reason("swing_up" if change > 0 else "swing_down", f"{'Up' if change > 0 else 'Down'} {abs(change):.0f}% on last month"))
        elif inv.subtotal > 0:
            reasons.append(_reason("swing_up", "Billed this month after ₹0 last month"))
        r = v2_results.get(inv.client_id)
        if r is not None:
            life = r["life"]
            if life["fraction"] < 1:
                reasons.append(_reason("partial_month", f"Billed {life['active_days']} of {life['days_in_month']} days" + (f" ({', '.join(life['partial_reasons'])})" if life["partial_reasons"] else "")))
            kinds = {l["component_type"] for l in r["lines"]}
            if "mg_topup" in kinds:
                reasons.append(_reason("minimum_applied"))
            if "cap_adjustment" in kinds or "cap_applied" in r["flags"]:
                reasons.append(_reason("cap_applied"))
            if r["manual"]:
                reasons.append(_reason("manual_input", f"Usage entered by hand: {', '.join(r['manual'])}"))
            if "group_applied" in r["flags"] or kinds & {"group_fee"}:
                reasons.append(_reason("group_applied"))
            if "covered_by_subscription" in {f for l in r["lines"] for f in l.get("flags", [])}:
                reasons.append(_reason("subscription_covered"))
            if r["contract"].valid_from and r["contract"].valid_from[:7] == run.period and (r["contract"].version or 1) > 1:
                reasons.append(_reason("new_contract", f"Contract v{r['contract'].version} starts this month"))
            for e in r["exceptions"]:
                if e["severity"] in ("high", "medium") and not e["blocks"]:
                    reasons.append(_reason(f"exception:{e['code']}", e["message"], e["severity"]))
        if reasons:
            flagged += 1
            kind = "flag"
        elif _spot(run.id, inv.client_id):
            spot += 1
            kind, reasons = "spot", [{"code": "spot_check", "label": "Random spot-check of a clean invoice", "severity": "low"}]
        else:
            continue
        db.add(ReviewItem(run_id=run.id, invoice_id=inv.id, client_id=inv.client_id, kind=kind, reasons=reasons, amount=inv.subtotal, prev_amount=before))
    return {"flagged": flagged, "spot_checks": spot}


def counts(db: Session, run_id: int) -> dict:
    items = db.query(ReviewItem).filter(ReviewItem.run_id == run_id).all()
    out = {"total": len(items), "open": 0, "reviewed": 0, "excluded": 0, "auto_cleared": 0, "open_flags": 0, "open_spot": 0}
    for i in items:
        if i.status == "needs_review":
            out["open"] += 1
            out["open_flags" if i.kind == "flag" else "open_spot"] += 1
        else:
            out[i.status] = out.get(i.status, 0) + 1
    return out


def auto_clear(db: Session, run_id: int) -> None:
    db.query(ReviewItem).filter(ReviewItem.run_id == run_id, ReviewItem.status == "needs_review").update(
        {"status": "auto_cleared", "reviewed_by": "policy"})
