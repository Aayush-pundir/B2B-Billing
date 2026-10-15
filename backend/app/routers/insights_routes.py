from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services import insights
from app.services.auth import FINANCE_READERS, require_roles

router = APIRouter(prefix="/api/insights")


@router.get("/overview")
def overview(period: str | None = None, months: int = 12, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_READERS))):
    return insights.overview(db, period, max(3, min(months, 36)))
