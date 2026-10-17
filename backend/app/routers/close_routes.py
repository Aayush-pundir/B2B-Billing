"""Month-end close: pre-flight checklist, the review queue, locking and the Zoho push, in one guided flow."""
import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import BillingAccount, BillingRun, Client, Contract, Invoice, ReviewItem, UsageSnapshot, User
from app.routers.core_routes import log_action
from app.services import billing_v2, review, settings_store, usage_db, zoho, zoho_push
from app.services.auth import require_user, require_write

router = APIRouter(prefix="/api/close")


def _prev(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _run_for(db: Session, period: str) -> BillingRun | None:
    return db.query(BillingRun).filter(BillingRun.period == period, BillingRun.status != "rejected").order_by(BillingRun.id.desc()).first()


@router.get("/checklist")
def checklist(period: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """What must be true before pressing Calculate. status: ok | warn | block (block = those accounts will be withheld)."""
    billable = billing_v2.billable_accounts(db, period)
    unmapped = [a for a in billable if any(e.enterprise_id.startswith(("ROW-", "UNMAPPED")) for e in a.enterprises)]
    mapped_ids = {e.enterprise_id for a in billable for e in a.enterprises if not e.enterprise_id.startswith(("ROW-", "UNMAPPED"))}
    seen = {s.enterprise_id for s in db.query(UsageSnapshot.enterprise_id).filter(UsageSnapshot.period == period)}
    unmodelled = sum(1 for a in billable for c in (billing_v2.active_contract(a, period).components) if c.component_type == "manual_formula")
    drafts = db.query(Contract).filter(Contract.status == "draft").count()
    prev_run = _run_for(db, _prev(period))
    run = _run_for(db, period)
    items = []

    def add(key, label, status, detail, page=None):
        items.append({"key": key, "label": label, "status": status, "detail": detail, "page": page})

    add("feed", "Usage database connected", "ok" if usage_db.is_configured() else "warn",
        "Usage is read straight from the platform database." if usage_db.is_configured() else "Not connected — set USAGE_DATABASE_URL, or load usage on the Usage tab.", "accounts")
    covered = len(mapped_ids & seen)
    add("usage", f"Usage loaded for {period}", "ok" if mapped_ids and covered == len(mapped_ids) else "warn",
        f"{covered} of {len(mapped_ids)} mapped enterprises have usage rows." + (" Calculate fetches the rest from the database." if usage_db.is_configured() and covered < len(mapped_ids) else ""), "accounts")
    add("mapping", "Every billable account is mapped to a platform enterprise", "ok" if not unmapped else "block",
        "All mapped." if not unmapped else f"{len(unmapped)} billable account(s) have no enterprise ID yet and will be withheld: " + ", ".join(a.display_name for a in unmapped[:4]) + ("…" if len(unmapped) > 4 else ""), "accounts")
    no_state = [a for a in billable if not (a.client.state_code or "").strip() and (a.client.gst_treatment or "") != "overseas"]
    add("master", "Every customer has its GST state on file", "ok" if not no_state else ("block" if get_settings().REQUIRE_CUSTOMER_STATE else "warn"),
        "All billable customers have a state." if not no_state else
        f"{len(no_state)} billable customer(s) have no state, so their GST type cannot be decided and they are withheld: " + ", ".join(a.display_name for a in no_state[:4]) + ("…" if len(no_state) > 4 else "") + ". Use Customers → Pull from Zoho, or import the customer master.", "clients")
    add("modelled", "Every charge is modelled", "ok" if not unmodelled else "warn",
        "All commercial terms are modelled." if not unmodelled else f"{unmodelled} charge(s) are still free-text and would bill ₹0 until someone models them.", "accounts")
    need: dict[str, int] = {}
    for a in billable:
        for f in billing_v2.needed_usage_fields(billing_v2.active_contract(a, period).components):
            need[f] = need.get(f, 0) + 1
    extra = {f: n for f, n in need.items() if f not in billing_v2.STANDARD_FIELDS}
    queries = usage_db.get_queries(db)
    gaps = {f: n for f, n in extra.items() if not queries["spend" if f == "upi_spend_after_free" else "extras"].strip()}
    add("fields", "Usage query supplies every field the contracts read", "ok" if not gaps else "warn",
        "Every field is covered by the configured queries." if not gaps else
        "Charges that read a field no query supplies bill ₹0 and are flagged: " + ", ".join(f"{f} ({n} accounts)" for f, n in sorted(gaps.items()))
        + ". Add an 'extras' query (truck_count, driver_count, card_share_pct) and, for spend-based contracts, a 'spend' query on the Settings page.", "settings")
    pending_assumptions = sum(len(billing_v2.open_assumptions(billing_v2.active_contract(a, period))) for a in billable)
    add("assumptions", "Interpreted contract terms are confirmed", "ok" if not pending_assumptions else "warn",
        "Nothing is waiting for confirmation." if not pending_assumptions else f"{pending_assumptions} interpreted term(s) are unconfirmed. They are billed as read and flagged on each invoice until Finance confirms them.", "accounts")
    add("drafts", "No contract changes waiting for Finance to lock", "ok" if not drafts else "warn",
        "Nothing pending." if not drafts else f"{drafts} draft contract version(s) are not locked — billing uses each account's last locked version.", "accounts")
    add("previous", f"{_prev(period)} is locked", "ok" if prev_run and prev_run.status in ("approved", "pushed") else "warn",
        "Locked." if prev_run and prev_run.status in ("approved", "pushed") else f"Last month's run is {prev_run.status if prev_run else 'missing'}.", "billing")
    z = zoho.configured()
    add("zoho", "Zoho Books connected", "ok" if z else "warn",
        "Credentials are set." if z else "Not connected — the push below runs in simulation mode (nothing reaches Zoho).", "settings")
    return {"period": period, "items": items, "billable_accounts": len(billable),
            "run": {"id": run.id, "status": run.status} if run else None}


def _serialize(i: ReviewItem, inv: Invoice | None, client: Client | None) -> dict:
    change = None
    if i.prev_amount:
        change = round((i.amount - i.prev_amount) / i.prev_amount * 100, 1)
    return {
        "id": i.id, "kind": i.kind, "status": i.status, "reasons": i.reasons or [], "amount": i.amount, "prev_amount": i.prev_amount, "change_pct": change,
        "reviewed_by": i.reviewed_by, "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None, "comment": i.comment,
        "invoice_id": i.invoice_id, "invoice_no": inv.invoice_no if inv else "", "total": inv.total if inv else 0,
        "client_name": client.name if client else "?", "enterprise_id": client.enterprise_id if client else "",
        "lines": [{"label": l.get("label"), "amount": l.get("amount"), "detail": l.get("calc_detail") or l.get("detail", "")} for l in (inv.lines if inv else []) or []],
    }


@router.get("/runs/{run_id}/review")
def review_queue(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    run = db.query(BillingRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    rows = db.query(ReviewItem).filter(ReviewItem.run_id == run_id).order_by(ReviewItem.id).all()
    invs = {i.id: i for i in db.query(Invoice).filter(Invoice.run_id == run_id)}
    clients = {c.id: c for c in db.query(Client).filter(Client.id.in_({r.client_id for r in rows}))} if rows else {}
    order = {"high": 0, "medium": 1, "low": 2}
    items = [_serialize(r, invs.get(r.invoice_id), clients.get(r.client_id)) for r in rows]
    def impact(x):
        return abs((x["amount"] or 0) - (x["prev_amount"] or 0))

    items.sort(key=lambda x: (x["kind"] == "spot", x["status"] != "needs_review", min((order.get(r["severity"], 1) for r in x["reasons"]), default=1), -impact(x), x["client_name"]))
    summary = run.summary or {}
    return {"run": {"id": run.id, "period": run.period, "status": run.status, "created_by": run.created_by, "approved_by": run.approved_by, "total": summary.get("total", 0),
                    "invoices": len(invs), "withheld": summary.get("skipped", []), "anomalies": summary.get("anomalies", [])},
            "counts": review.counts(db, run_id), "items": items}


class ReviewIn(BaseModel):
    action: str  # ok | exclude | reopen
    comment: str = ""


def _guard(db: Session, run_id: int, user: User) -> BillingRun:
    run = db.query(BillingRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status != "pending_approval":
        raise HTTPException(400, f"The run is {run.status}; the review queue can only be changed while it awaits approval")
    if run.created_by == user.email and not settings_store.is_yes(settings_store.get_automation(db)["APPROVAL_ALLOW_SELF"]):
        raise HTTPException(403, "Second check: you generated this run, so someone else in Finance has to review it")
    return run


def _apply(db: Session, item: ReviewItem, payload: ReviewIn, user: User) -> None:
    inv = db.query(Invoice).get(item.invoice_id) if item.invoice_id else None
    if payload.action == "ok":
        item.status = "reviewed"
    elif payload.action == "exclude":
        if not payload.comment.strip():
            raise HTTPException(400, "Say why this invoice is being left out of the run")
        item.status = "excluded"
        if inv:
            inv.status, inv.balance_due = "cancelled", 0.0
    elif payload.action == "reopen":
        item.status = "needs_review"
        if inv and inv.status == "cancelled":
            inv.status, inv.balance_due = "draft", inv.total
    else:
        raise HTTPException(400, "Unknown action")
    item.reviewed_by = "" if payload.action == "reopen" else user.email
    item.reviewed_at = None if payload.action == "reopen" else datetime.datetime.utcnow()
    item.comment = payload.comment.strip() if payload.action != "reopen" else item.comment


@router.post("/runs/{run_id}/review/{item_id}")
def review_item(run_id: int, item_id: int, payload: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require_write("approvals"))):
    _guard(db, run_id, user)
    item = db.query(ReviewItem).filter(ReviewItem.id == item_id, ReviewItem.run_id == run_id).first()
    if not item:
        raise HTTPException(404, "Review item not found")
    _apply(db, item, payload, user)
    db.commit()
    log_action(db, user, f"review.{payload.action}", "billing_run", f"run={run_id} item={item_id} {payload.comment[:80]}")
    return {"ok": True, "counts": review.counts(db, run_id)}


class BulkIn(BaseModel):
    item_ids: list[int]
    comment: str = ""


@router.post("/runs/{run_id}/review-bulk")
def review_bulk(run_id: int, payload: BulkIn, db: Session = Depends(get_db), user: User = Depends(require_write("approvals"))):
    """Mark many items checked at once (never excludes: leaving an invoice out needs its own reason)."""
    _guard(db, run_id, user)
    n = 0
    for item in db.query(ReviewItem).filter(ReviewItem.run_id == run_id, ReviewItem.id.in_(payload.item_ids), ReviewItem.status == "needs_review"):
        _apply(db, item, ReviewIn(action="ok", comment=payload.comment), user)
        n += 1
    db.commit()
    log_action(db, user, "review.bulk_ok", "billing_run", f"run={run_id} items={n}")
    return {"ok": True, "updated": n, "counts": review.counts(db, run_id)}


# ----------------------------------------------------------------- Zoho -----------------------------------------------------------------

@router.get("/zoho")
def zoho_connection(user: User = Depends(require_user)):
    return {"configured": zoho.configured()}


@router.post("/zoho/test")
def zoho_test(user: User = Depends(require_write("billing"))):
    return zoho.test_connection()


@router.post("/runs/{run_id}/zoho/start")
def zoho_start(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    run = db.query(BillingRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in ("approved", "pushed"):
        raise HTTPException(400, "Lock the run first — only a locked run is pushed to Zoho")
    started = zoho_push.start(run_id)
    log_action(db, user, "billing.push_zoho_start", "billing_run", f"run={run_id} started={started}")
    return {"started": started, **zoho_push.status(db, run_id)}


@router.get("/runs/{run_id}/zoho/status")
def zoho_status(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not db.query(BillingRun).get(run_id):
        raise HTTPException(404, "Run not found")
    return zoho_push.status(db, run_id)
