from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.routers.core_routes import log_action
from app.services import usage_db
from app.services.auth import require_any_write, require_user, require_write

router = APIRouter(prefix="/api/datasource")


@router.get("")
def get_datasource(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return {**usage_db.describe(), "queries": usage_db.get_queries(db), "defaults": usage_db.DEFAULT_QUERIES}


class QueriesIn(BaseModel):
    queries: dict[str, str]


@router.put("")
def put_queries(payload: QueriesIn, db: Session = Depends(get_db), user: User = Depends(require_write("datasource"))):
    try:
        saved = usage_db.save_queries(db, payload.queries)
    except usage_db.UsageDbError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, user, "datasource.update_queries", "datasource", ",".join(payload.queries.keys()))
    return {"queries": saved}


class TestIn(BaseModel):
    period: str


@router.post("/test")
def test_datasource(payload: TestIn, db: Session = Depends(get_db), user: User = Depends(require_any_write("datasource", "billing"))):
    if not usage_db.is_configured():
        raise HTTPException(400, "USAGE_DATABASE_URL is not set on the server")
    try:
        usage_db.period_bounds(payload.period)
        return {"results": usage_db.test_queries(db, payload.period)}
    except usage_db.UsageDbError as exc:
        raise HTTPException(400, str(exc))
