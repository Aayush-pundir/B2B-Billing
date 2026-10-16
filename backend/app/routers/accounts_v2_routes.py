"""Billing v2 API: accounts, versioned contracts, lifecycle events, groups, subscriptions,
usage snapshots, simulation, imports and exceptions. Terms are amended by Finance only."""
import datetime
from decimal import Decimal as D

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (Invoice, AccountEnterprise, AccountEvent, BillingAccount, BillingException, BillingGroup, BillingGroupMember, Client,
                        Contract, ContractComponent, EscalationRule, SubscriptionInvoice, UsageSnapshot, User)
from app.routers.core_routes import log_action
from app.services import billing_v2, engine2, importer_v2, renewals, snapshots, usage_db
from app.services.auth import require_user, require_write

router = APIRouter(prefix="/api/v2")
EVENT_TYPES = ["dropped", "demo", "on_hold", "frozen", "closure_requested", "fee_waived", "billed_elsewhere", "not_live", "not_using",
               "mg_deferred", "annual_billing", "quarterly_billing", "convenience_fee_model", "no_auto_debit", "pending_check"]


def _acct(db: Session, account_id: int) -> BillingAccount:
    a = db.query(BillingAccount).get(account_id)
    if not a:
        raise HTTPException(404, "Billing account not found")
    return a


def _date(v, name):
    if v in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(v).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be a date (YYYY-MM-DD)")


def _contract_out(c: Contract) -> dict:
    return {"id": c.id, "version": c.version, "valid_from": c.valid_from, "valid_to": c.valid_to, "status": c.status, "source": c.source,
            "billing_frequency": c.billing_frequency, "gst_mode": c.gst_mode, "gst_rate": c.gst_rate, "overall_min_amount": c.overall_min_amount,
            "overall_cap_amount": c.overall_cap_amount, "mg_effective_from": c.mg_effective_from, "prorate_mg": c.prorate_mg,
            "subscription_covers": c.subscription_covers or ["A"], "notes": c.notes, "assumptions": c.assumptions or [], "approved_by": c.approved_by,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
            "components": [{"id": k.id, "component_type": k.component_type, "label": k.label, "params": k.params, "sort_order": k.sort_order,
                            "valid_from": k.valid_from, "valid_to": k.valid_to} for k in c.components]}


@router.post("/contracts/{contract_id}/assumptions/{index}/confirm")
def confirm_assumption(contract_id: int, index: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    """Finance signs off something the importer had to interpret. The terms themselves stay as they are; if the
    interpretation is wrong, issue a corrected contract version instead."""
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    items = [dict(x) for x in (c.assumptions or [])]
    if not 0 <= index < len(items):
        raise HTTPException(404, "No such assumption")
    items[index].update(confirmed_by=user.email, confirmed_at=datetime.datetime.utcnow().isoformat())
    c.assumptions = items  # reassigned so the JSON column is saved
    db.commit()
    log_action(db, user, "contract.assumption_confirmed", "contract", f"contract={contract_id} #{index}")
    return {"ok": True, "assumptions": items}


@router.get("/catalogue")
def catalogue(user: User = Depends(require_user)):
    return {"components": {k: {"label": v[0], "group": engine2.COMPONENTS[k][0] if k in engine2.COMPONENTS else "A",
                               "params": [{"key": p[0], "label": p[1], "kind": p[2]} for p in v[1]]} for k, v in engine2.CATALOGUE.items()},
            "events": EVENT_TYPES, "group_kinds": ["net_parent", "equal_split", "pooled", "per_entity_min"]}


# ------------------------------- accounts -------------------------------

@router.get("/accounts")
def list_accounts(q: str = "", status: str = "", db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = datetime.date.today().strftime("%Y-%m")
    open_ex = {}
    for e in db.query(BillingException).filter(BillingException.status == "open").all():
        open_ex[e.account_id] = open_ex.get(e.account_id, 0) + 1
    last_billed: dict[int, dict] = {}
    for inv_client, inv_period, inv_amount in (db.query(Invoice.client_id, Invoice.period, Invoice.subtotal)
                                               .filter(Invoice.status.notin_(("draft", "cancelled")), Invoice.doc_type == "invoice").order_by(Invoice.period, Invoice.id)):
        last_billed[inv_client] = {"period": inv_period, "amount": inv_amount}
    out = []
    accounts = (db.query(BillingAccount).options(selectinload(BillingAccount.enterprises), selectinload(BillingAccount.events),
                                                 selectinload(BillingAccount.contracts).selectinload(Contract.components))
                .order_by(BillingAccount.display_name).all())
    for a in accounts:
        if q and q.lower() not in (a.display_name + " ".join(e.enterprise_id for e in a.enterprises)).lower():
            continue
        life = billing_v2.lifecycle(a, today)
        c = billing_v2.active_contract(a, today)
        latest = max(a.contracts, key=lambda x: x.version, default=None)
        state = "billable" if life["billable"] else life["reason"] or "not billable"
        if status and status != ("billable" if life["billable"] else "nonbillable"):
            continue
        out.append({"id": a.id, "client_id": a.client_id, "name": a.display_name, "enterprise_ids": [e.enterprise_id for e in a.enterprises],
                    "mapped": not any(e.enterprise_id.startswith(("ROW-", "UNMAPPED")) for e in a.enterprises), "kam": a.kam, "go_live": a.go_live_date,
                    "state": state, "billable": life["billable"], "contract_version": c.version if c else None,
                    "contract_status": (c or latest).status if (c or latest) else "none", "components": len((c or latest).components) if (c or latest) else 0,
                    "unmodelled": sum(k.component_type == "manual_formula" for k in (c or latest).components) if (c or latest) else 0,
                    "overall_min": c.overall_min_amount if c else None, "open_exceptions": open_ex.get(a.id, 0), "events": [e.event_type for e in a.events],
                    "kinds": [k.component_type for k in (c or latest).components] if (c or latest) else [], "last_billed": last_billed.get(a.client_id),
                    "draft_pending": any(x.status == "draft" for x in a.contracts), "assumptions_open": len(billing_v2.open_assumptions(c or latest))})
    return out


@router.get("/accounts/{account_id}")
def account_detail(account_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    a = _acct(db, account_id)
    groups = {g.id: g for g in db.query(BillingGroup).all()}
    gm = [{"group": groups[m.group_id].name, "group_id": m.group_id, "is_parent": m.is_parent} for m in db.query(BillingGroupMember).filter(BillingGroupMember.account_id == a.id) if m.group_id in groups]
    return {"id": a.id, "client_id": a.client_id, "name": a.display_name, "zoho_billing_name": a.zoho_billing_name, "kam": a.kam,
            "sales_manager": a.sales_manager, "go_live_date": a.go_live_date, "auto_debit": a.auto_debit, "notes": a.notes, "import_flags": a.import_flags or [],
            "enterprises": [{"id": e.id, "enterprise_id": e.enterprise_id, "role": e.role, "include_add_money": e.include_add_money, "include_upi": e.include_upi,
                             "include_users": e.include_users, "valid_from": e.valid_from, "valid_to": e.valid_to} for e in a.enterprises],
            "contracts": [_contract_out(c) for c in sorted(a.contracts, key=lambda c: -c.version)],
            "events": [{"id": e.id, "event_type": e.event_type, "effective_from": e.effective_from, "effective_to": e.effective_to, "payload": e.payload, "source_text": e.source_text} for e in a.events],
            "escalations": [{"id": r.id, "applies_to": r.applies_to, "kind": r.kind, "value": r.value, "next_date": r.next_date, "last_applied_date": r.last_applied_date, "active": r.active, "note": r.note}
                            for r in db.query(EscalationRule).filter(EscalationRule.account_id == a.id)],
            "subscriptions": [{"id": s.id, "frequency": s.frequency, "period_start": s.period_start, "period_end": s.period_end, "amount": s.amount, "description": s.description, "status": s.status}
                              for s in db.query(SubscriptionInvoice).filter(SubscriptionInvoice.account_id == a.id).order_by(SubscriptionInvoice.period_start)],
            "groups": gm}


class AccountIn(BaseModel):
    display_name: str | None = None
    zoho_billing_name: str | None = None
    kam: str | None = None
    sales_manager: str | None = None
    go_live_date: str | None = None
    auto_debit: bool | None = None
    notes: str | None = None


@router.put("/accounts/{account_id}")
def update_account(account_id: int, p: AccountIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    for k, v in p.model_dump(exclude_none=True).items():
        setattr(a, k, _date(v, "go_live_date") if k == "go_live_date" else v)
    db.commit()
    log_action(db, user, "v2.account.update", "billing_account", a.display_name)
    return {"ok": True}


class EnterpriseIn(BaseModel):
    enterprise_id: str
    role: str = "primary"
    include_add_money: bool = True
    include_upi: bool = True
    include_users: bool = True
    valid_from: str | None = None
    valid_to: str | None = None


@router.put("/accounts/{account_id}/enterprises")
def set_enterprises(account_id: int, items: list[EnterpriseIn], db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    if not items or not any(i.role == "primary" for i in items):
        raise HTTPException(400, "Provide at least one primary enterprise")
    primary = next(i for i in items if i.role == "primary").enterprise_id.strip()
    clash = db.query(Client).filter(Client.enterprise_id == primary, Client.id != a.client_id).first()
    if clash:
        raise HTTPException(400, f"{primary} is already used by {clash.name}")
    for e in list(a.enterprises):
        db.delete(e)
    db.flush()
    for i in items:
        db.add(AccountEnterprise(account_id=a.id, enterprise_id=i.enterprise_id.strip(), role=i.role, include_add_money=i.include_add_money,
                                 include_upi=i.include_upi, include_users=i.include_users, valid_from=_date(i.valid_from, "valid_from"), valid_to=_date(i.valid_to, "valid_to")))
    a.client.enterprise_id = primary
    db.commit()
    log_action(db, user, "v2.account.enterprises", "billing_account", f"{a.display_name}: {[i.enterprise_id for i in items]}")
    return {"ok": True}


# ------------------------------- contracts -------------------------------

class ComponentIn(BaseModel):
    component_type: str
    label: str = ""
    params: dict = {}
    valid_from: str | None = None
    valid_to: str | None = None


class ContractIn(BaseModel):
    valid_from: str
    valid_to: str | None = None
    billing_frequency: str = "monthly"
    gst_mode: str = "exclusive"
    gst_rate: float = 0.18
    overall_min_amount: float | None = None
    overall_cap_amount: float | None = None
    mg_effective_from: str | None = None
    prorate_mg: bool = False
    subscription_covers: list[str] = ["A"]
    notes: str = ""
    components: list[ComponentIn] = []


def _apply(db: Session, c: Contract, p: ContractIn):
    for comp in p.components:
        if comp.component_type not in engine2.COMPONENTS:
            raise HTTPException(400, f"Unknown component type '{comp.component_type}'")
    c.valid_from, c.valid_to = _date(p.valid_from, "valid_from"), _date(p.valid_to, "valid_to")
    if not c.valid_from:
        raise HTTPException(400, "valid_from is required")
    c.billing_frequency, c.gst_mode, c.gst_rate = p.billing_frequency, p.gst_mode, p.gst_rate
    c.overall_min_amount, c.overall_cap_amount = p.overall_min_amount, p.overall_cap_amount
    if p.overall_min_amount and p.overall_cap_amount and p.overall_cap_amount < p.overall_min_amount:
        raise HTTPException(400, "The cap cannot be below the minimum")
    c.mg_effective_from, c.prorate_mg, c.subscription_covers, c.notes = _date(p.mg_effective_from, "mg_effective_from"), p.prorate_mg, p.subscription_covers, p.notes
    for k in list(c.components):
        db.delete(k)
    db.flush()
    for i, comp in enumerate(p.components):
        db.add(ContractComponent(contract_id=c.id, component_type=comp.component_type, label=comp.label, params=comp.params, sort_order=i,
                                 valid_from=_date(comp.valid_from, "component valid_from"), valid_to=_date(comp.valid_to, "component valid_to")))


@router.post("/accounts/{account_id}/contracts")
def new_contract(account_id: int, copy_from_latest: bool = True, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    """Start a new draft version (a copy of the latest by default). Locked versions are never edited."""
    a = _acct(db, account_id)
    latest = max(a.contracts, key=lambda c: c.version, default=None)
    c = Contract(account_id=a.id, version=(latest.version + 1) if latest else 1, valid_from=datetime.date.today().isoformat(), status="draft",
                 source="manual", created_by=user.email)
    if latest and copy_from_latest:
        for f in ("billing_frequency", "gst_mode", "gst_rate", "overall_min_amount", "overall_cap_amount", "mg_effective_from", "prorate_mg", "subscription_covers", "notes"):
            setattr(c, f, getattr(latest, f))
    db.add(c)
    db.flush()
    if latest and copy_from_latest:
        for k in latest.components:
            db.add(ContractComponent(contract_id=c.id, component_type=k.component_type, label=k.label, params=dict(k.params or {}), sort_order=k.sort_order, valid_from=k.valid_from, valid_to=k.valid_to))
    db.commit()
    log_action(db, user, "v2.contract.new", "contract", f"{a.display_name} v{c.version}")
    return {"id": c.id, "version": c.version}


@router.put("/contracts/{contract_id}")
def update_contract(contract_id: int, p: ContractIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    if c.status != "draft":
        raise HTTPException(400, "Only draft versions can be edited — create a new version instead")
    _apply(db, c, p)
    c.source = "manual" if c.source != "import" else c.source
    db.commit()
    log_action(db, user, "v2.contract.save", "contract", f"{c.account.display_name} v{c.version}")
    return {"ok": True}


@router.post("/contracts/{contract_id}/lock")
def lock_contract(contract_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    if c.status != "draft":
        raise HTTPException(400, f"Contract is already {c.status}")
    if any(k.component_type == "manual_formula" for k in c.components):
        raise HTTPException(400, "This contract still has components marked 'not modelled' — model them before locking")
    fd = datetime.date.fromisoformat(c.valid_from)
    for other in c.account.contracts:
        if other.id != c.id and other.status == "locked" and (not other.valid_to or other.valid_to >= c.valid_from):
            other.valid_to = (fd - datetime.timedelta(days=1)).isoformat()
            if other.valid_to < other.valid_from:
                other.valid_to = other.valid_from
            other.status = "superseded"
    c.status, c.approved_by, c.approved_at = "locked", user.email, datetime.datetime.utcnow()
    db.commit()
    log_action(db, user, "v2.contract.lock", "contract", f"{c.account.display_name} v{c.version}")
    return {"ok": True}


# ------------------------------- events / escalation / groups / subscriptions -------------------------------

class EventIn(BaseModel):
    event_type: str
    effective_from: str | None = None
    effective_to: str | None = None
    payload: dict = {}
    source_text: str = ""


@router.post("/accounts/{account_id}/events")
def add_event(account_id: int, p: EventIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    if p.event_type not in EVENT_TYPES:
        raise HTTPException(400, f"Unknown event type; use one of {EVENT_TYPES}")
    db.add(AccountEvent(account_id=a.id, event_type=p.event_type, effective_from=_date(p.effective_from, "effective_from"), effective_to=_date(p.effective_to, "effective_to"),
                        payload=p.payload, source_text=p.source_text, created_by=user.email))
    db.commit()
    log_action(db, user, "v2.event.add", "billing_account", f"{a.display_name}: {p.event_type} from {p.effective_from}")
    return {"ok": True}


@router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    e = db.query(AccountEvent).get(event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    db.delete(e)
    db.commit()
    log_action(db, user, "v2.event.delete", "billing_account", f"{e.account_id}: {e.event_type}")
    return {"ok": True}


class EscalationIn(BaseModel):
    applies_to: str = "all"
    kind: str = "percent"
    value: float = 0.10
    next_date: str
    note: str = ""


@router.post("/accounts/{account_id}/escalations")
def add_escalation(account_id: int, p: EscalationIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    if p.kind not in ("percent", "fixed_increment"):
        raise HTTPException(400, "kind must be percent or fixed_increment")
    db.add(EscalationRule(account_id=a.id, applies_to=p.applies_to, kind=p.kind, value=p.value, next_date=_date(p.next_date, "next_date"), note=p.note))
    db.commit()
    return {"ok": True}


class ApplyEscIn(BaseModel):
    through: str  # apply every rule due on or before this date


@router.post("/escalations/apply")
def apply_escalations(p: ApplyEscIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    """Create DRAFT contract versions with escalated prices for every rule that is due (Finance reviews and locks each)."""
    created = billing_v2.apply_escalations(db, _date(p.through, "through"), user.email)
    log_action(db, user, "v2.escalations.apply", "contract", f"created={len(created)}")
    return {"drafts_created": created}


class GroupIn(BaseModel):
    name: str
    kind: str = "net_parent"
    group_min_amount: float | None = None
    rate: float | None = None
    pool_load: bool = False
    units: int | None = None
    subtract: str = "A"
    notes: str = ""
    members: list[dict] = []  # [{account_id, is_parent}]


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_user)):
    names = {a.id: a.display_name for a in db.query(BillingAccount).all()}
    return [{"id": g.id, "name": g.name, "kind": g.kind, "group_min_amount": g.group_min_amount, "rate": g.rate, "pool_load": g.pool_load, "units": g.units,
             "subtract": g.subtract, "notes": g.notes,
             "members": [{"account_id": m.account_id, "name": names.get(m.account_id, "?"), "is_parent": m.is_parent}
                         for m in db.query(BillingGroupMember).filter(BillingGroupMember.group_id == g.id)]} for g in db.query(BillingGroup).order_by(BillingGroup.name)]


@router.post("/groups")
def save_group(p: GroupIn, group_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    if p.kind not in ("net_parent", "equal_split", "pooled", "per_entity_min"):
        raise HTTPException(400, "Invalid group kind")
    if p.kind == "net_parent" and sum(1 for m in p.members if m.get("is_parent")) != 1:
        raise HTTPException(400, "A net-parent group needs exactly one parent")
    g = db.query(BillingGroup).get(group_id) if group_id else BillingGroup(name=p.name)
    if group_id and not g:
        raise HTTPException(404, "Group not found")
    for f in ("name", "kind", "group_min_amount", "rate", "pool_load", "units", "subtract", "notes"):
        setattr(g, f, getattr(p, f))
    db.add(g)
    db.flush()
    for m in db.query(BillingGroupMember).filter(BillingGroupMember.group_id == g.id).all():
        db.delete(m)
    for m in p.members:
        db.add(BillingGroupMember(group_id=g.id, account_id=int(m["account_id"]), is_parent=bool(m.get("is_parent"))))
    db.commit()
    log_action(db, user, "v2.group.save", "billing_group", p.name)
    return {"id": g.id}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    for m in db.query(BillingGroupMember).filter(BillingGroupMember.group_id == group_id).all():
        db.delete(m)
    g = db.query(BillingGroup).get(group_id)
    if g:
        db.delete(g)
    db.commit()
    return {"ok": True}


class SubIn(BaseModel):
    frequency: str = "annual"
    period_start: str
    period_end: str
    amount: float
    description: str = "Annual Subscription"
    zoho_invoice_id: str | None = None


@router.post("/accounts/{account_id}/subscriptions")
def add_subscription(account_id: int, p: SubIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    s, e = _date(p.period_start, "period_start"), _date(p.period_end, "period_end")
    if e < s:
        raise HTTPException(400, "period_end is before period_start")
    db.add(SubscriptionInvoice(account_id=a.id, particulars=a.display_name, frequency=p.frequency, period_start=s, period_end=e, amount=p.amount,
                               description=p.description, zoho_invoice_id=p.zoho_invoice_id, bill_month=s[:7]))
    db.commit()
    log_action(db, user, "v2.subscription.add", "billing_account", f"{a.display_name} {s}→{e} ₹{p.amount:,.2f}")
    return {"ok": True}


@router.put("/subscriptions/{sub_id}/status")
def set_sub_status(sub_id: int, status: str = Query(...), db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    s = db.query(SubscriptionInvoice).get(sub_id)
    if not s or status not in ("raised", "cancelled", "credit_noted"):
        raise HTTPException(400, "Invalid request")
    s.status = status
    db.commit()
    return {"ok": True}


# ------------------------------- usage snapshots -------------------------------

@router.get("/usage/{period}")
def get_usage(period: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(UsageSnapshot).filter(UsageSnapshot.period == period).order_by(UsageSnapshot.enterprise_id).all()
    return [{"enterprise_id": s.enterprise_id, **{f: getattr(s, f) for f in snapshots.FIELDS}, "source": s.source, "manual_fields": s.manual_fields or [], "frozen": s.frozen} for s in rows]


@router.post("/usage/{period}/refresh")
def refresh_usage(period: str, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    try:
        usage_db.period_bounds(period)
        rows = usage_db.fetch_snapshot_rows(db, period)
    except usage_db.UsageDbError as exc:
        raise HTTPException(400, f"Usage database: {exc}")
    res = snapshots.save(db, period, rows, "database")
    log_action(db, user, "v2.usage.refresh", "usage", f"{period} {res}")
    return {**res, "enterprises": len(rows)}


class ManualUsageIn(BaseModel):
    values: dict


@router.put("/usage/{period}/{enterprise_id}")
def manual_usage(period: str, enterprise_id: str, p: ManualUsageIn, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    try:
        snapshots.set_manual(db, period, enterprise_id, p.values, user.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, user, "v2.usage.manual", "usage", f"{period} {enterprise_id} {list(p.values)}")
    return {"ok": True}


# ------------------------------- simulate -------------------------------

class SimIn(BaseModel):
    period: str
    usage: dict = {}  # overrides, keyed by engine field names (add_money, upi_txn_count, maker_count, admin_count, …)
    contract_id: int | None = None


@router.post("/accounts/{account_id}/simulate")
def simulate(account_id: int, p: SimIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Compute one account for a period without saving anything — shows the full calculation trace."""
    a = _acct(db, account_id)
    snaps = snapshots.load(db, p.period)
    if p.usage:
        eid = a.enterprises[0].enterprise_id if a.enterprises else a.client.enterprise_id
        row = {**(snaps.get(eid) or {f: None for f in snapshots.FIELDS}), **{k: v for k, v in p.usage.items()}, "_manual": list(p.usage)}
        snaps = {**snaps, eid: row}
    if p.contract_id:  # preview a specific (e.g. draft) version
        c = db.query(Contract).get(p.contract_id)
        if not c or c.account_id != a.id:
            raise HTTPException(404, "Contract not found")
        original = billing_v2.active_contract
        billing_v2.active_contract = lambda acct, period: c
        try:
            r = billing_v2.compute_account(db, a, p.period, snaps)
        finally:
            billing_v2.active_contract = original
    else:
        r = billing_v2.compute_account(db, a, p.period, snaps)
    if r is None:
        raise HTTPException(400, "This account has no locked v2 contract for that period")
    r["exceptions"] = billing_v2.validate_account(db, r, p.period)
    life = r["life"]
    return {"billable": life["billable"], "reason": life["reason"], "active_days": life["active_days"], "days_in_month": life["days_in_month"],
            "subtotal": float(r["subtotal"]), "gst": float(r["subtotal"] * D("0.18")), "usage": {k: v for k, v in r["usage"].items()},
            "lines": [{"component_type": l["component_type"], "label": l["label"], "amount": float(l["amount"]), "detail": l.get("detail", ""), "flags": l.get("flags", [])} for l in r["lines"]],
            "exceptions": r["exceptions"]}


# ------------------------------- imports -------------------------------

@router.post("/import/rules")
async def import_rules(file: UploadFile = File(...), lock: bool = False, replace: bool = False, db: Session = Depends(get_db),
                       user: User = Depends(require_write("commercials"))):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        res = importer_v2.import_rules(db, text, user.email, lock=lock, replace=replace)
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, f"Could not read the rules file: {exc}")
    log_action(db, user, "v2.import.rules", "billing_account", str(res))
    return res


@router.post("/import/mapping/suggest")
async def suggest_mapping(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    """Upload an export of the platform's customer list (enterprise ID + name, optionally GSTIN) and get back a mapping CSV with
    the confident matches filled in and the close ones listed for review. Nothing is applied until that file is imported."""
    from app.services import mapping_suggest
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        platform = mapping_suggest.read_platform_list(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    csv_text, counts = mapping_suggest.to_csv(mapping_suggest.suggest(db, platform))
    log_action(db, user, "v2.mapping.suggest", "billing_account", str(counts))
    return {"summary": {**counts, "platform_enterprises": len(platform)}, "csv": csv_text}


@router.post("/import/rules/resolve")
async def resolve_unmodelled(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    """Upload the rulebook again to clear 'needs modelling' charges on accounts that are already imported. Creates draft versions only."""
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        res = importer_v2.resolve_unmodelled(db, text, user.email)
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, f"Could not read the rules file: {exc}")
    log_action(db, user, "v2.import.resolve_unmodelled", "billing_account", f"drafts={res['drafts_created']} still_open={len(res['still_open'])}")
    return res


@router.post("/import/register")
async def import_register(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    res = importer_v2.import_register(db, (await file.read()).decode("utf-8-sig", errors="replace"))
    log_action(db, user, "v2.import.register", "subscription", str(res))
    return res


@router.get("/import/mapping-template")
def mapping_template(db: Session = Depends(get_db), user: User = Depends(require_user)):
    import io
    return StreamingResponse(io.StringIO(importer_v2.mapping_template(db)), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=enterprise_mapping.csv"})


@router.post("/import/mapping")
async def import_mapping(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    res = importer_v2.import_mapping(db, (await file.read()).decode("utf-8-sig", errors="replace"))
    log_action(db, user, "v2.import.mapping", "billing_account", f"mapped={res['mapped']} errors={len(res['errors'])}")
    return res


# ------------------------------- exceptions -------------------------------

@router.get("/exceptions")
def list_exceptions(run_id: int | None = None, status: str = "open", db: Session = Depends(get_db), user: User = Depends(require_user)):
    names = {a.id: a.display_name for a in db.query(BillingAccount).all()}
    qy = db.query(BillingException)
    if run_id:
        qy = qy.filter(BillingException.run_id == run_id)
    if status != "all":
        qy = qy.filter(BillingException.status == status)
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rows = sorted(qy.order_by(BillingException.id.desc()).limit(1000).all(), key=lambda e: order.get(e.severity, 9))
    return [{"id": e.id, "run_id": e.run_id, "account_id": e.account_id, "account": names.get(e.account_id, "—"), "code": e.code, "severity": e.severity,
             "message": e.message, "blocks": e.blocks, "status": e.status, "resolved_by": e.resolved_by, "resolved_note": e.resolved_note} for e in rows]


class ResolveIn(BaseModel):
    status: str = "resolved"  # resolved | waived
    note: str = ""


@router.post("/exceptions/{exception_id}/resolve")
def resolve_exception(exception_id: int, p: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require_write("approvals"))):
    e = db.query(BillingException).get(exception_id)
    if not e:
        raise HTTPException(404, "Exception not found")
    if p.status not in ("resolved", "waived"):
        raise HTTPException(400, "status must be resolved or waived")
    if p.status == "waived" and not p.note.strip():
        raise HTTPException(400, "A note is required to waive an exception")
    e.status, e.resolved_by, e.resolved_note = p.status, user.email, p.note
    db.commit()
    log_action(db, user, "v2.exception.resolve", "billing_exception", f"{e.code} {p.status}: {p.note}")
    return {"ok": True}


# ------------------------------- renewals -------------------------------

@router.get("/renewals")
def list_renewals(horizon_days: int = 75, db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = renewals.upcoming(db, horizon_days=max(1, min(horizon_days, 400)))
    return {"rows": rows, "overdue_value": round(sum(r["expected_amount"] for r in rows if r["status"] == "overdue"), 2),
            "due_value": round(sum(r["expected_amount"] for r in rows if r["status"] == "due"), 2)}


class RenewIn(BaseModel):
    amount: float | None = None
    invoice: bool = True


@router.post("/renewals/{account_id}/raise")
def raise_renewal(account_id: int, p: RenewIn, db: Session = Depends(get_db), user: User = Depends(require_write("commercials"))):
    a = _acct(db, account_id)
    try:
        res = renewals.raise_renewal(db, a, p.amount, p.invoice, user.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    log_action(db, user, "v2.renewal.raise", "billing_account", f"{a.display_name} {res}")
    return res
