"""Overdue-invoice reminders. No scheduler is wired up here — call
run_reminders() from a cron/Task Scheduler hitting the /reminders/run
endpoint, same pattern as the monthly billing calculate call."""
import datetime

from sqlalchemy.orm import Session

from app.models import Client, Invoice, ReminderLog
from app.services import mailer, settings_store

THRESHOLDS = [7, 15, 30]


def run_reminders(db: Session) -> dict:
    today = datetime.date.today()
    org = settings_store.get_all_settings(db)
    invoices = db.query(Invoice).filter(Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]),
                                         Invoice.disputed.is_(False)).all()
    sent, skipped = [], []

    for inv in invoices:
        if not inv.due_date:
            continue
        try:
            due = datetime.date.fromisoformat(inv.due_date)
        except ValueError:
            continue
        days_overdue = (today - due).days
        threshold = max((t for t in THRESHOLDS if t <= days_overdue), default=None)
        if threshold is None:
            continue

        already_sent = db.query(ReminderLog).filter(
            ReminderLog.invoice_id == inv.id, ReminderLog.days_overdue == threshold
        ).first()
        if already_sent:
            continue

        client = db.query(Client).get(inv.client_id)
        subject = f"Payment reminder — invoice {inv.invoice_no} ({days_overdue} days overdue)"
        body = (f"Dear {client.name},\n\nInvoice {inv.invoice_no} for {inv.period}, amount Rs. "
                f"{inv.balance_due:,.2f}, was due on {inv.due_date} and remains unpaid.\n\n{org.get('ORG_NAME')}")
        emailed = mailer.send_email(client.billing_contact_email, subject, body) if client else False

        db.add(ReminderLog(invoice_id=inv.id, days_overdue=threshold, template=f"overdue_{threshold}d",
                            channel="email" if emailed else "logged_only"))
        (sent if emailed else skipped).append({"invoice_no": inv.invoice_no, "days_overdue": days_overdue})

    db.commit()
    return {"sent": sent, "logged_only": skipped}
