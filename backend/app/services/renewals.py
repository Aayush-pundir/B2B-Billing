"""Subscription renewals: forecast what is due, and raise the renewal as a subscription record + a real invoice."""
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from app.models import BillingAccount, EscalationRule, SubscriptionInvoice
from app.services import billing_v2, sales


def _d(v):
    return datetime.date.fromisoformat(v) if v else None


def _next_period(last_start: str, last_end: str) -> tuple[str, str]:
    s, e = _d(last_start), _d(last_end)
    months = (e.year - s.year) * 12 + e.month - s.month + 1
    ns = e + datetime.timedelta(days=1)
    ne = sales.add_months(ns, months) - datetime.timedelta(days=1)
    return ns.isoformat(), ne.isoformat()


def upcoming(db: Session, today: datetime.date | None = None, horizon_days: int = 75) -> list[dict]:
    today = today or datetime.date.today()
    out = []
    for a in db.query(BillingAccount).all():
        subs = (db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == a.id, SubscriptionInvoice.status == "raised")
                .order_by(SubscriptionInvoice.period_end).all())
        periodic = any(e.event_type in ("annual_billing", "quarterly_billing") for e in a.events)
        if not subs or not (_d(subs[-1].period_end)):
            continue
        last = subs[-1]
        life = billing_v2.lifecycle(a, today.strftime("%Y-%m"))
        if not life["billable"] and not periodic:
            continue
        end = _d(last.period_end)
        days_left = (end - today).days
        if days_left > horizon_days:
            continue
        rule = db.query(EscalationRule).filter(EscalationRule.account_id == a.id, EscalationRule.active.is_(True), EscalationRule.kind == "percent").first()
        expected = round(last.amount * (1 + rule.value), 2) if rule else last.amount
        ns, ne = _next_period(last.period_start, last.period_end)
        out.append({"account_id": a.id, "account": a.display_name, "frequency": last.frequency, "last_period_end": last.period_end,
                    "last_amount": last.amount, "expected_amount": expected, "next_start": ns, "next_end": ne, "days_left": days_left,
                    "status": "overdue" if days_left < 0 else "due", "billable": life["billable"]})
    return sorted(out, key=lambda r: r["days_left"])


def raise_renewal(db: Session, account: BillingAccount, amount: float | None, invoice: bool, user_email: str) -> dict:
    subs = (db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == account.id, SubscriptionInvoice.status == "raised")
            .order_by(SubscriptionInvoice.period_end).all())
    if not subs:
        raise ValueError("This account has no subscription history to renew")
    last = subs[-1]
    ns, ne = _next_period(last.period_start, last.period_end)
    if any(s.period_start == ns for s in subs):
        raise ValueError(f"A subscription starting {ns} already exists")
    amt = float(amount if amount is not None else last.amount)
    inv_no = ""
    if invoice:
        inv = sales.create_invoice(db, account.client, [{"description": f"{last.description} ({ns} to {ne})", "qty": 1, "rate": amt, "gst_rate": 18}],
                                   email=user_email, source="renewal", issue=True)
        inv_no = inv.invoice_no
    db.add(SubscriptionInvoice(account_id=account.id, particulars=account.display_name, frequency=last.frequency, period_start=ns, period_end=ne,
                               amount=amt, description=last.description, bill_month=ns[:7], zoho_invoice_id=None))
    db.commit()
    return {"period_start": ns, "period_end": ne, "amount": amt, "invoice_no": inv_no}
