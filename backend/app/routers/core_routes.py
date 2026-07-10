import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client, Commercial, CommercialVersion, Metric, Addon, User, AuditLog
from app.services.auth import require_user, require_write
from app.services.engine import escalate_rules
from app.services import settings_store

router = APIRouter(prefix="/api")


def log_action(db: Session, user: User, action: str, entity: str, detail: str = ""):
    db.add(AuditLog(user=user.email, action=action, entity=entity, detail=detail))
    db.commit()


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------

class ClientIn(BaseModel):
    enterprise_id: str
    name: str
    gst: str = ""
    pan: str = ""
    state: str = ""
    state_code: str = ""
    billing_contact_email: str = ""
    billing_frequency: str = "monthly"


@router.get("/clients")
def list_clients(db: Session = Depends(get_db), user: User = Depends(require_user)):
    clients = db.query(Client).order_by(Client.name).all()
    out = []
    for c in clients:
        commercial = db.query(Commercial).filter(Commercial.client_id == c.id).order_by(Commercial.id.desc()).first()
        out.append({
            "id": c.id, "enterprise_id": c.enterprise_id, "name": c.name, "gst": c.gst, "pan": c.pan,
            "state": c.state, "state_code": c.state_code, "billing_contact_email": c.billing_contact_email,
            "billing_frequency": c.billing_frequency, "zoho_contact_id": c.zoho_contact_id,
            "commercial_status": commercial.status if commercial else "none",
        })
    return out


@router.post("/clients")
def create_client(payload: ClientIn, db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    if db.query(Client).filter(Client.enterprise_id == payload.enterprise_id).first():
        raise HTTPException(400, "EnterpriseId already exists")
    client = Client(**payload.model_dump())
    db.add(client)
    db.commit()
    db.refresh(client)
    log_action(db, user, "client.create", "client", client.enterprise_id)
    return {"id": client.id}


@router.put("/clients/{client_id}")
def update_client(client_id: int, payload: ClientIn, db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    client = db.query(Client).get(client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    for k, v in payload.model_dump().items():
        setattr(client, k, v)
    db.commit()
    log_action(db, user, "client.update", "client", client.enterprise_id)
    return {"ok": True}


@router.delete("/clients/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    client = db.query(Client).get(client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    db.delete(client)
    db.commit()
    log_action(db, user, "client.delete", "client", client.enterprise_id)
    return {"ok": True}


@router.post("/clients/bulk")
async def bulk_import_clients(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("clients"))):
    raw = await file.read()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    created, skipped = 0, []
    for row in reader:
        eid = (row.get("enterprise_id") or row.get("EnterpriseId") or "").strip()
        if not eid or db.query(Client).filter(Client.enterprise_id == eid).first():
            skipped.append(eid or "(blank)")
            continue
        db.add(Client(
            enterprise_id=eid,
            name=(row.get("name") or row.get("Name") or "").strip(),
            gst=(row.get("gst") or row.get("GSTIN") or "").strip(),
            pan=(row.get("pan") or row.get("PAN") or "").strip(),
            state=(row.get("state") or row.get("State") or "").strip(),
            state_code=(row.get("state_code") or row.get("StateCode") or "").strip(),
            billing_contact_email=(row.get("billing_contact_email") or "").strip(),
            billing_frequency=(row.get("billing_frequency") or "monthly").strip() or "monthly",
        ))
        created += 1
    db.commit()
    log_action(db, user, "client.bulk_import", "client", f"created={created} skipped={len(skipped)}")
    return {"created": created, "skipped": skipped}


@router.get("/clients/template")
def client_template():
    header = "enterprise_id,name,gst,pan,state,state_code,billing_contact_email,billing_frequency\n"
    return StreamingResponse(io.StringIO(header), media_type="text/csv",
                              headers={"Content-Disposition": "attachment; filename=clients_template.csv"})


# --------------------------------------------------------------------------
# Commercials
# --------------------------------------------------------------------------

class CommercialIn(BaseModel):
    client_id: int
    billing_frequency: str = "monthly"
    first_invoice_date: str | None = None
    rules: list[dict] = []


@router.get("/commercials")
def list_commercials(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(Commercial).all()
    return [{
        "id": c.id, "client_id": c.client_id, "billing_frequency": c.billing_frequency,
        "first_invoice_date": c.first_invoice_date, "rules": c.rules, "status": c.status,
        "version_no": c.version_no,
    } for c in rows]


@router.post("/commercials")
def save_commercial(payload: CommercialIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    commercial = db.query(Commercial).filter(Commercial.client_id == payload.client_id).first()
    if commercial is None:
        commercial = Commercial(client_id=payload.client_id)
        db.add(commercial)
    if commercial.status == "locked":
        raise HTTPException(400, "Commercial is locked. Unlock before editing.")
    commercial.billing_frequency = payload.billing_frequency
    commercial.first_invoice_date = payload.first_invoice_date
    commercial.rules = payload.rules
    commercial.status = "draft"
    db.commit()
    db.refresh(commercial)
    log_action(db, user, "commercial.save", "commercial", str(commercial.id))
    return {"id": commercial.id}


@router.post("/commercials/{commercial_id}/lock")
def lock_commercial(commercial_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    commercial = db.query(Commercial).get(commercial_id)
    if not commercial:
        raise HTTPException(404, "Commercial not found")
    commercial.status = "locked"
    commercial.version_no += 1
    db.add(CommercialVersion(
        commercial_id=commercial.id, version_no=commercial.version_no, rules=commercial.rules,
        billing_frequency=commercial.billing_frequency, snapshot_reason="locked", created_by=user.email,
    ))
    db.commit()
    log_action(db, user, "commercial.lock", "commercial", str(commercial.id))
    return {"ok": True, "version_no": commercial.version_no}


@router.post("/commercials/{commercial_id}/unlock")
def unlock_commercial(commercial_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    commercial = db.query(Commercial).get(commercial_id)
    if not commercial:
        raise HTTPException(404, "Commercial not found")
    commercial.status = "draft"
    db.commit()
    log_action(db, user, "commercial.unlock", "commercial", str(commercial.id))
    return {"ok": True}


@router.get("/commercials/{commercial_id}/versions")
def commercial_versions(commercial_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(CommercialVersion).filter(CommercialVersion.commercial_id == commercial_id).order_by(CommercialVersion.version_no.desc()).all()
    return [{
        "version_no": v.version_no, "rules": v.rules, "billing_frequency": v.billing_frequency,
        "snapshot_reason": v.snapshot_reason, "created_by": v.created_by, "created_at": v.created_at.isoformat(),
    } for v in rows]


class EscalateIn(BaseModel):
    pct: float


@router.post("/commercials/{commercial_id}/escalate")
def escalate_commercial(commercial_id: int, payload: EscalateIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    commercial = db.query(Commercial).get(commercial_id)
    if not commercial:
        raise HTTPException(404, "Commercial not found")
    commercial.rules = escalate_rules(commercial.rules or [], payload.pct)
    commercial.status = "draft"
    commercial.version_no += 1
    db.add(CommercialVersion(
        commercial_id=commercial.id, version_no=commercial.version_no, rules=commercial.rules,
        billing_frequency=commercial.billing_frequency, snapshot_reason=f"escalation +{payload.pct}%", created_by=user.email,
    ))
    db.commit()
    log_action(db, user, "commercial.escalate", "commercial", f"{commercial.id} +{payload.pct}%")
    return {"ok": True, "rules": commercial.rules}


# --------------------------------------------------------------------------
# Metrics & Add-ons
# --------------------------------------------------------------------------

class MetricIn(BaseModel):
    code: str
    label: str
    basis: str = "amount"
    active: bool = True


@router.get("/metrics")
def list_metrics(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return [{"id": m.id, "code": m.code, "label": m.label, "basis": m.basis, "active": m.active} for m in db.query(Metric).all()]


@router.post("/metrics")
def create_metric(payload: MetricIn, db: Session = Depends(get_db), user: User = Depends(require_write("metrics"))):
    m = Metric(**payload.model_dump())
    db.add(m)
    db.commit()
    return {"id": m.id}


@router.put("/metrics/{metric_id}")
def update_metric(metric_id: int, payload: MetricIn, db: Session = Depends(get_db), user: User = Depends(require_write("metrics"))):
    m = db.query(Metric).get(metric_id)
    if not m:
        raise HTTPException(404, "Metric not found")
    for k, v in payload.model_dump().items():
        setattr(m, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/metrics/{metric_id}")
def delete_metric(metric_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("metrics"))):
    m = db.query(Metric).get(metric_id)
    if m:
        db.delete(m)
        db.commit()
    return {"ok": True}


class AddonIn(BaseModel):
    code: str
    name: str
    default_rate_type: str = "flat"
    active: bool = True


@router.get("/addons")
def list_addons(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return [{"id": a.id, "code": a.code, "name": a.name, "default_rate_type": a.default_rate_type, "active": a.active} for a in db.query(Addon).all()]


@router.post("/addons")
def create_addon(payload: AddonIn, db: Session = Depends(get_db), user: User = Depends(require_write("addons"))):
    a = Addon(**payload.model_dump())
    db.add(a)
    db.commit()
    return {"id": a.id}


# --------------------------------------------------------------------------
# Users (platform users, not clients)
# --------------------------------------------------------------------------

class UserIn(BaseModel):
    email: str
    role: str = "viewer"


@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_write("users"))):
    return [{"id": u.id, "email": u.email, "role": u.role, "provider": u.provider,
             "last_login": u.last_login.isoformat() if u.last_login else None} for u in db.query(User).all()]


@router.post("/users")
def create_user(payload: UserIn, db: Session = Depends(get_db), user: User = Depends(require_write("users"))):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "User already exists")
    u = User(email=payload.email, role=payload.role, provider="dev")
    db.add(u)
    db.commit()
    return {"id": u.id}


@router.put("/users/{user_id}")
def update_user(user_id: int, payload: UserIn, db: Session = Depends(get_db), user: User = Depends(require_write("users"))):
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(404, "User not found")
    u.email = payload.email
    u.role = payload.role
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("users"))):
    u = db.query(User).get(user_id)
    if u:
        db.delete(u)
        db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

@router.get("/audit")
def audit_log(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(AuditLog).order_by(AuditLog.ts.desc()).limit(200).all()
    return [{"user": r.user, "action": r.action, "entity": r.entity, "detail": r.detail, "ts": r.ts.isoformat()} for r in rows]


# --------------------------------------------------------------------------
# Templates (billing CSV templates pre-filled with active EnterpriseIds)
# --------------------------------------------------------------------------

TEMPLATE_HEADERS = {
    "loading": "TransactionId,EnterpriseId,VirtualAccountNumber,Amount,Fee,TotalTransactionAmount,TransactionType,TransactionDate,Status,Vpan,TransactionCategory,ReferenceTransactionId,OmniTransactionId,PreviousStatus,OriginalTransactionDate,UpdatedOn,RunningBalance,PrevBalance,UtrNo,AddMoneyRequestId,EnterpriseName",
    "upi": "TransactionId,TransactionDate,CustomerId,Amount,TotalTrxnAmount,TransactionCategory,TransactionType,Status,EnterpriseName,EnterpriseId",
    "users": "S.No,EnterpriseId,EnterpriseName,Admin,CO_ADMIN,CHECKER,APPROVER,MAKER,TotalUsers",
}


@router.get("/templates/{kind}")
def download_template(kind: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if kind not in TEMPLATE_HEADERS:
        raise HTTPException(404, "Unknown template kind")
    clients = db.query(Client).all()
    lines = [TEMPLATE_HEADERS[kind]]
    if kind == "users":
        for i, c in enumerate(clients, start=1):
            lines.append(f"{i},{c.enterprise_id},{c.name},0,0,0,0,0,0")
    else:
        for c in clients:
            lines.append(c.enterprise_id)
    body = "\n".join(lines) + "\n"
    return StreamingResponse(io.StringIO(body), media_type="text/csv",
                              headers={"Content-Disposition": f"attachment; filename={kind}_template.csv"})


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@router.get("/settings")
def get_settings_route(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return settings_store.get_all_settings(db)


class SettingsIn(BaseModel):
    values: dict[str, str]


@router.put("/settings")
def put_settings(payload: SettingsIn, db: Session = Depends(get_db), user: User = Depends(require_write("settings"))):
    result = settings_store.update_settings(db, payload.values)
    log_action(db, user, "settings.update", "settings", str(list(payload.values.keys())))
    return result
