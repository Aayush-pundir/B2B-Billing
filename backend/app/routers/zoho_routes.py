from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.core_routes import log_action
from app.services import zoho, zoho_sync
from app.services.auth import require_write

router = APIRouter(prefix="/api/zoho")


@router.post("/sync-customers")
def sync_customers(dry_run: bool = False, db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    """Link billing accounts to their existing Zoho customers and fill GSTIN, state and e-mail from Zoho. dry_run only reports."""
    if not zoho.configured():
        raise HTTPException(400, "Zoho is not connected: set ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN and ZOHO_ORG_ID first")
    try:
        report = zoho_sync.pull_customers(db, apply=not dry_run)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    log_action(db, user, "zoho.sync_customers", "client", f"dry_run={dry_run} linked={report['linked_by_gstin'] + report['linked_by_name']}")
    return report
