"""Minimal SMTP sender. No-ops silently when SMTP_HOST isn't configured so
the platform works fully without an email server — invoices can still be
downloaded and sent manually."""
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings


def is_configured() -> bool:
    s = get_settings()
    return bool(s.SMTP_HOST and s.SMTP_FROM)


def send_email(to: str, subject: str, body: str, attachment: bytes | None = None, attachment_name: str = "invoice.pdf") -> bool:
    s = get_settings()
    if not is_configured() or not to:
        return False
    msg = MIMEMultipart()
    msg["From"] = s.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    if attachment:
        part = MIMEApplication(attachment, Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)
    with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT) as server:
        server.starttls()
        if s.SMTP_USER:
            server.login(s.SMTP_USER, s.SMTP_PASSWORD)
        server.sendmail(s.SMTP_FROM, [to], msg.as_string())
    return True
