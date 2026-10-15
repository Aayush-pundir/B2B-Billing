"""Dunning ladder. For every open invoice, find the latest ladder step whose
day-offset from the due date has been reached and which hasn't been sent yet
(negative offsets = before the due date). Respects: per-customer opt-out,
disputes, and a customer's promised payment date.

Driven by the scheduler, or on demand via the /reminders/run endpoint."""
import datetime

from sqlalchemy.orm import Session

from app.models import Client, Invoice, ReminderLog
from app.services import invoicing, mailer, settings_store

OPEN_STATUSES = ["issued", "sent", "partially_paid", "overdue"]
DEFAULT_LADDER = [-3, 1, 7, 15]


def parse_ladder(text: str) -> list[int]:
    steps = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            try:
                steps.append(int(part))
            except ValueError:
                raise ValueError(f"'{part}' is not a whole number of days")
    return sorted(set(steps)) or DEFAULT_LADDER


def _tone(step: int) -> tuple[str, str]:
    if step < 0:
        return "upcoming", "This is a courtesy reminder that the invoice below falls due soon."
    if step <= 1:
        return "due", "The invoice below is now past its due date. If you have already paid, please ignore this note."
    if step <= 7:
        return "reminder", "This is a reminder that the invoice below remains unpaid."
    return "final", "The invoice below is significantly overdue. Please arrange payment immediately or contact us if there is an issue."


def run_reminders(db: Session, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    automation = settings_store.get_automation(db)
    if not settings_store.is_yes(automation["DUNNING_ENABLED"]):
        return {"sent": [], "logged_only": [], "skipped_promise": [], "disabled": True}
    ladder = parse_ladder(automation["DUNNING_LADDER"])
    org = settings_store.get_all_settings(db)

    invoices = db.query(Invoice).filter(Invoice.status.in_(OPEN_STATUSES), Invoice.disputed.is_(False)).all()
    sent, logged, promised = [], [], []

    for inv in invoices:
        if not inv.due_date:
            continue
        try:
            due = datetime.date.fromisoformat(inv.due_date)
        except ValueError:
            continue
        client = db.query(Client).get(inv.client_id)
        if client is None or client.reminders_enabled is False:
            continue
        offset = (today - due).days
        step = max((k for k in ladder if k <= offset), default=None)
        if step is None:
            continue
        if db.query(ReminderLog).filter(ReminderLog.invoice_id == inv.id, ReminderLog.days_overdue == step).first():
            continue
        if inv.promise_date and inv.promise_date >= today.isoformat():
            promised.append({"invoice_no": inv.invoice_no, "promise_date": inv.promise_date})
            continue

        tone, intro = _tone(step)
        when = f"due in {-offset} day(s)" if offset < 0 else (f"due today" if offset == 0 else f"{offset} day(s) overdue")
        link = invoicing.portal_url(org, inv) if inv.public_token else ""
        subject = f"{'Payment reminder' if step >= 0 else 'Upcoming payment'} — invoice {inv.invoice_no} ({when})"
        body = (f"Dear {client.name},\n\n{intro}\n\nInvoice {inv.invoice_no} for {inv.period}\nAmount outstanding: Rs. {inv.balance_due:,.2f}\n"
                f"Due date: {inv.due_date}\n"
                + (f"\nView, pay or tell us when you will pay: {link}\n" if link else "")
                + f"\n{org.get('ORG_NAME')}")
        recipients = ",".join(x for x in [client.billing_contact_email, *(client.cc_emails or "").split(",")] if x and x.strip())
        emailed = mailer.send_email(recipients, subject, body) if recipients else False

        db.add(ReminderLog(invoice_id=inv.id, days_overdue=step, template=f"{tone}_{step}d",
                           channel="email" if emailed else "logged_only"))
        (sent if emailed else logged).append({"invoice_no": inv.invoice_no, "step": step, "when": when})

    db.commit()
    return {"sent": sent, "logged_only": logged, "skipped_promise": promised, "disabled": False}
