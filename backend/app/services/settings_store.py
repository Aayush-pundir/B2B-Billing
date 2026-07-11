"""DB-backed org settings with .env fallback. Editable at runtime — no
backend restart needed for org settings changes."""
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting

EDITABLE_KEYS = [
    "ORG_NAME", "ORG_GST", "ORG_STATE_CODE", "INVOICE_PREFIX", "DEFAULT_GST_RATE",
    "PAYMENT_TERMS_DAYS",
]


def _env_defaults() -> dict[str, str]:
    s = get_settings()
    return {
        "ORG_NAME": s.ORG_NAME,
        "ORG_GST": s.ORG_GST,
        "ORG_STATE_CODE": s.ORG_STATE_CODE,
        "INVOICE_PREFIX": s.INVOICE_PREFIX,
        "DEFAULT_GST_RATE": str(s.DEFAULT_GST_RATE),
        "PAYMENT_TERMS_DAYS": str(s.PAYMENT_TERMS_DAYS),
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
