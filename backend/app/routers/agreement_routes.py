import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Agreement, Client, Commercial, User
from app.services.auth import require_user, require_write
from app.services.extraction import extract_commercial_terms
from app.routers.core_routes import log_action

router = APIRouter(prefix="/api/agreements")

ALLOWED_TYPES = {"docx", "pdf", "png", "jpg", "jpeg"}


def _save_upload(client_id: int, upload: UploadFile) -> tuple[str, str, str]:
    settings = get_settings()
    ext = (upload.filename.rsplit(".", 1)[-1] if "." in upload.filename else "").lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: .{ext}. Allowed: docx, pdf, png, jpg, jpeg")
    client_dir = os.path.join(settings.UPLOAD_DIR, str(client_id))
    os.makedirs(client_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    stored_path = os.path.join(client_dir, stored_name)
    return stored_path, ext, stored_name


@router.post("/upload")
async def upload_agreement(
    client_id: int = Form(...),
    agreement_date: str | None = Form(None),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_write("agreements")),
):
    client = db.query(Client).get(client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    stored_path, ext, _ = _save_upload(client_id, file)
    content = await file.read()
    with open(stored_path, "wb") as f:
        f.write(content)
    agreement = Agreement(
        client_id=client_id, file_name=file.filename, stored_path=stored_path, file_type=ext,
        agreement_date=agreement_date, start_date=start_date, end_date=end_date,
    )
    db.add(agreement)
    db.commit()
    db.refresh(agreement)
    log_action(db, user, "agreement.upload", "agreement", f"{agreement.id}:{file.filename}")
    return {"id": agreement.id, "file_type": ext}


@router.post("/bulk")
async def bulk_upload_agreements(
    client_id: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_write("agreements")),
):
    client = db.query(Client).get(client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    created = []
    for file in files:
        stored_path, ext, _ = _save_upload(client_id, file)
        content = await file.read()
        with open(stored_path, "wb") as f:
            f.write(content)
        agreement = Agreement(client_id=client_id, file_name=file.filename, stored_path=stored_path, file_type=ext)
        db.add(agreement)
        db.commit()
        db.refresh(agreement)
        created.append(agreement.id)
    log_action(db, user, "agreement.bulk_upload", "agreement", f"count={len(created)}")
    return {"created": created}


@router.get("")
def list_agreements(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(Agreement).all()
    return [{
        "id": a.id, "client_id": a.client_id, "file_name": a.file_name, "file_type": a.file_type,
        "agreement_date": a.agreement_date, "start_date": a.start_date, "end_date": a.end_date,
        "version": a.version, "has_extraction": a.extracted_json is not None,
        "uploaded_at": a.uploaded_at.isoformat(),
    } for a in rows]


@router.post("/{agreement_id}/extract")
def extract_agreement(agreement_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("agreements"))):
    agreement = db.query(Agreement).get(agreement_id)
    if not agreement:
        raise HTTPException(404, "Agreement not found")
    try:
        result = extract_commercial_terms(agreement.stored_path, agreement.file_type)
    except Exception as exc:  # extraction failures shouldn't crash the request
        raise HTTPException(422, f"Extraction failed: {exc}")

    agreement.extracted_json = result
    db.commit()

    log_action(db, user, "agreement.extract", "agreement", f"{agreement.id} confidence={result['overall_confidence']}")
    return {
        "rules": result["rules"],
        "field_confidence": result["field_confidence"],
        "overall_confidence": result["overall_confidence"],
        "escalation_pct_annual": result["escalation_pct_annual"],
    }
