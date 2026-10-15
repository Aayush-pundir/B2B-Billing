from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Invoice, Item, User
from app.routers.core_routes import log_action
from app.services.auth import require_user, require_write

router = APIRouter(prefix="/api/items")


class ItemIn(BaseModel):
    code: str
    name: str
    item_type: str = "service"
    hsn_sac: str = ""
    unit: str = "nos"
    rate: float = 0.0
    gst_rate: float = 18.0
    revenue_account: str = "4000"
    description: str = ""
    active: bool = True


def _out(i: Item) -> dict:
    return {f: getattr(i, f) for f in ItemIn.model_fields} | {"id": i.id}


def _check(db: Session, p: ItemIn):
    if not p.code.strip() or not p.name.strip():
        raise HTTPException(400, "Code and name are required")
    if not 0 <= p.gst_rate <= 40 or p.rate < 0:
        raise HTTPException(400, "Rate must be ≥ 0 and GST % between 0 and 40")
    acct = db.query(Account).filter(Account.code == p.revenue_account).first()
    if not acct or acct.type != "income":
        raise HTTPException(400, f"Revenue account {p.revenue_account} must be an income account")


@router.get("")
def list_items(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return [_out(i) for i in db.query(Item).order_by(Item.name).all()]


@router.post("")
def create_item(p: ItemIn, db: Session = Depends(get_db), user: User = Depends(require_write("items"))):
    _check(db, p)
    if db.query(Item).filter(Item.code == p.code.strip()).first():
        raise HTTPException(400, "An item with this code already exists")
    it = Item(**p.model_dump() | {"code": p.code.strip(), "name": p.name.strip()})
    db.add(it)
    db.commit()
    log_action(db, user, "item.create", "item", it.code)
    return {"id": it.id}


@router.put("/{item_id}")
def update_item(item_id: int, p: ItemIn, db: Session = Depends(get_db), user: User = Depends(require_write("items"))):
    it = db.query(Item).get(item_id)
    if not it:
        raise HTTPException(404, "Item not found")
    _check(db, p)
    for k, v in p.model_dump().items():
        setattr(it, k, v)
    db.commit()
    log_action(db, user, "item.update", "item", it.code)
    return {"ok": True}


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("items"))):
    it = db.query(Item).get(item_id)
    if not it:
        raise HTTPException(404, "Item not found")
    used = any((ln.get("item_id") == item_id) for inv in db.query(Invoice).all() for ln in (inv.lines or []))
    if used:  # keep history intact — retire instead of deleting
        it.active = False
        db.commit()
        return {"ok": True, "deactivated": True}
    db.delete(it)
    db.commit()
    log_action(db, user, "item.delete", "item", it.code)
    return {"ok": True, "deleted": True}
