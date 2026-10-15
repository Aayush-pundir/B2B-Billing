"""DB-backed org settings with .env fallback. Editable at runtime — no
backend restart needed for org settings changes."""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting

EDITABLE_KEYS = [
    "ORG_NAME", "ORG_GST", "ORG_STATE_CODE", "INVOICE_PREFIX", "DEFAULT_GST_RATE",
    "PAYMENT_TERMS_DAYS",
    # customer portal / pay details
    "PUBLIC_BASE_URL", "BANK_NAME", "BANK_ACCOUNT_NO", "BANK_IFSC", "UPI_ID",
    "DEFAULT_SAC",
]

# Policy knobs for approvals, scheduling, dunning and credit control. Managed on
# the Automation page (finance / admin), stored in the same settings table.
AUTOMATION_DEFAULTS = {
    "APPROVAL_REQUIRED": "yes",          # billing runs need finance sign-off before invoices can be issued
    "APPROVAL_ALLOW_SELF": "no",         # may the person who generated a run also approve it?
    "AUTO_APPROVE_MAX_TOTAL": "0",       # auto-approve clean runs up to this total (0 = never)
    "ANOMALY_SWING_PCT": "50",           # flag invoices that moved more than this % vs the previous one
    "AUTO_RUN_ENABLED": "no",            # create the previous month's run automatically
    "AUTO_RUN_DAY": "1",                 # day of month to start the automatic run
    "AUTO_ISSUE_ON_APPROVAL": "yes",     # issue (and email) every invoice once a run is approved
    "DUNNING_ENABLED": "yes",
    "DUNNING_LADDER": "-3,1,7,15",       # days relative to due date (negative = before due)
    "BLOCK_AFTER_OVERDUE_DAYS": "0",     # auto-block customers overdue beyond N days (0 = off)
}
AUTOMATION_KEYS = list(AUTOMATION_DEFAULTS.keys())


def get_automation(db: Session) -> dict[str, str]:
    rows = {row.key: row.value for row in db.query(Setting).filter(Setting.key.in_(AUTOMATION_KEYS)).all()}
    return {k: rows.get(k, v) for k, v in AUTOMATION_DEFAULTS.items()}


def update_automation(db: Session, values: dict[str, str]) -> dict[str, str]:
    for key, value in values.items():
        if key not in AUTOMATION_KEYS:
            continue
        row = db.query(Setting).filter(Setting.key == key).first()
        if row is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    db.commit()
    return get_automation(db)


def is_yes(value: str) -> bool:
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def _env_defaults() -> dict[str, str]:
    s = get_settings()
    return {
        "ORG_NAME": s.ORG_NAME,
        "ORG_GST": s.ORG_GST,
        "ORG_STATE_CODE": s.ORG_STATE_CODE,
        "INVOICE_PREFIX": s.INVOICE_PREFIX,
        "DEFAULT_GST_RATE": str(s.DEFAULT_GST_RATE),
        "PAYMENT_TERMS_DAYS": str(s.PAYMENT_TERMS_DAYS),
        "PUBLIC_BASE_URL": "", "BANK_NAME": "", "BANK_ACCOUNT_NO": "", "BANK_IFSC": "", "UPI_ID": "", "DEFAULT_SAC": "998314",
    }


def get_all_settings(db: Session) -> dict[str, str]:
    defaults = _env_defaults()
    rows = {row.key: row.value for row in db.query(Setting).all()}
    return {k: rows.get(k, defaults.get(k, "")) for k in EDITABLE_KEYS}


def get_setting(db: Session, key: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is not None:
        return row.value
    return _env_defaults().get(key, "")


def update_settings(db: Session, values: dict[str, str]) -> dict[str, str]:
    for key, value in values.items():
        if key not in EDITABLE_KEYS:
            continue
        row = db.query(Setting).filter(Setting.key == key).first()
        if row is None:
            row = Setting(key=key, value=str(value))
            db.add(row)
        else:
            row.value = str(value)
    db.commit()
    return get_all_settings(db)


LOCK_KEY = "BOOKS_LOCK_DATE"


def get_lock_date(db: Session) -> str:
    row = db.query(Setting).filter(Setting.key == LOCK_KEY).first()
    return row.value if row and row.value else ""


def set_lock_date(db: Session, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == LOCK_KEY).first()
    if row is None:
        db.add(Setting(key=LOCK_KEY, value=value))
    else:
        row.value = value
    db.commit()
