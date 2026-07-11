import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, ClientCrmLink, CrmSyncRun, User
from app.services.auth import require_user, require_write
from app.services.crm_sync import ADAPTERS
from app.routers.core_routes import log_action

router = APIRouter(prefix="/api/crm")

# Identity fields the CRM owns — overwritten on every sync. Billing-specific
# fields (billing_frequency, zoho_contact_id, commercials) are never touched here.
CRM_OWNED_FIELDS = ["name", "gst", "pan", "state", "state_code", "billing_contact_email"]


@router.post("/sync")
async def sync_from_crm(source: str = "generic_csv", file: UploadFile = File(...),
                         db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    adapter = ADAPTERS.get(source)
    if adapter is None:
        raise HTTPException(400, f"Unknown CRM source '{source}'. Available: {list(ADAPTERS)}")

    raw = await file.read()
    records = adapter.fetch_clients(raw)
    if not records:
        raise HTTPException(400, "No usable rows found — need at least enterprise_id and name columns")

    created, updated, skipped = 0, 0, []
    for rec in records:
        client = db.query(Client).filter(Client.enterprise_id == rec.enterprise_id).first()
        if client is None:
            client = Client(
                enterprise_id=rec.enterprise_id, name=rec.name, gst=rec.gst, pan=rec.pan,
                state=rec.state, state_code=rec.state_code, billing_contact_email=rec.billing_contact_email,
            )
            db.add(client)
            db.flush()
            created += 1
        else:
            client.name = rec.name or client.name
            for field, value in [("gst", rec.gst), ("pan", rec.pan), ("state", rec.state),
                                  ("state_code", rec.state_code), ("billing_contact_email", rec.billing_contact_email)]:
                if value:
                    setattr(client, field, value)
            updated += 1

        link = db.query(ClientCrmLink).filter(ClientCrmLink.client_id == client.id).first()
        if link is None:
            link = ClientCrmLink(client_id=client.id, crm_source=source, crm_id=rec.crm_id)
            db.add(link)
        else:
            link.crm_id = rec.crm_id
            link.crm_source = source

    run = CrmSyncRun(
        crm_source=source, created_count=created, updated_count=updated, skipped_count=len(skipped),
        detail=skipped, run_by=user.email,
    )
    db.add(run)
    db.commit()
    log_action(db, user, "crm.sync", "client", f"source={source} created={created} updated={updated}")
    return {"created": created, "updated": updated, "skipped": skipped, "total_rows": len(records)}


@router.get("/sync-runs")
def list_sync_runs(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(CrmSyncRun).order_by(CrmSyncRun.run_at.desc()).limit(50).all()
    return [{"id": r.id, "crm_source": r.crm_source, "created": r.created_count, "updated": r.updated_count,
             "skipped": r.skipped_count, "run_by": r.run_by, "run_at": r.run_at.isoformat()} for r in rows]


@router.get("/template")
def crm_template():
    header = "crm_id,enterprise_id,name,gst,pan,state,state_code,billing_contact_email\n"
    return StreamingResponse(io.StringIO(header), media_type="text/csv",
                              headers={"Content-Disposition": "attachment; filename=crm_export_template.csv"})
