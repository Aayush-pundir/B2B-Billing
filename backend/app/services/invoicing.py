"""Invoice lifecycle helpers shared by the manual endpoints, the approval
workflow and the scheduler, so every path issues and sends invoices the same way."""
from __future__ import annotations

import datetime
import secrets

from sqlalchemy.orm import Session

from app.models import BillingRun, Client, Invoice
from app.services import mailer, settings_store


def ensure_token(inv: Invoice) -> str:
    if not inv.public_token:
        inv.public_token = secrets.token_urlsafe(18)
    return inv.public_token


def portal_url(org: dict, inv: Invoice) -> str:
    base = (org.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/portal.html?t={inv.public_token}" if base and inv.public_token else ""


def issue(db: Session, inv: Invoice) -> None:
    inv.status = "issued"
    inv.issued_at = datetime.datetime.utcnow()
    if not inv.due_date:
        inv.due_date = (datetime.date.today() + datetime.timedelta(days=inv.payment_terms_days or 15)).isoformat()
    inv.balance_due = inv.total - inv.paid_amount
    ensure_token(inv)


def send(db: Session, inv: Invoice, client: Client | None = None) -> bool:
    """Email the invoice (PDF + portal link) and mark it sent. Returns whether an email actually went out."""
    from app.services.invoice_pdf import build_invoice_pdf

    client = client or db.query(Client).get(inv.client_id)
    org = settings_store.get_all_settings(db)
    emailed = False
    if mailer.is_configured() and client.billing_contact_email:
        recipients = ",".join(x for x in [client.billing_contact_email, *(client.cc_emails or "").split(",")] if x.strip())
        link = portal_url(org, inv)
        body = (f"Dear {client.name},\n\nPlease find attached invoice {inv.invoice_no} for {inv.period}, "
                f"amount due Rs. {inv.total:,.2f}, due on {inv.due_date}.\n"
                + (f"\nView, pay or raise a query online: {link}\n" if link else "")
                + f"\n{org.get('ORG_NAME')}")
        emailed = mailer.send_email(recipients, f"Invoice {inv.invoice_no} — {org.get('ORG_NAME')}", body,
                                    build_invoice_pdf(org, client, inv), f"{inv.invoice_no}.pdf")
    inv.status = "sent"
    inv.sent_at = datetime.datetime.utcnow()
    return emailed


def finalize_run(db: Session, run: BillingRun) -> dict:
    """Issue — and email — every draft invoice in an approved run."""
    issued = emailed = 0
    for inv in run.invoices:
        if inv.status != "draft":
            continue
        issue(db, inv)
        issued += 1
        if send(db, inv):
            emailed += 1
    db.commit()
    return {"issued": issued, "emailed": emailed}
