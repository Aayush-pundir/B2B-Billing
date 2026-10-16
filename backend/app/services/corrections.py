"""Finance corrections layered over the imported rulebook: where the contract and the legacy sheet disagree, a short file says
what the contract says, row by row, in the app's own vocabulary. Each correction becomes a DRAFT contract version (or a group
edit) that Finance reviews and locks; nothing is changed silently and locked versions are never edited.

File format (JSON):
{
  "corrections": [
    {"row": 788, "customer": "(for people)", "note": "why", "overall_min_amount": 15000,
     "components": [{"component_type": "per_unit_fleet", "label": "...", "params": {...}}, ...],     # replaces all charges, OR
     "modify": [{"component_type": "load_percent_tiered", "set_params": {"mode": "marginal"}}],     # change parameters of one charge, and/or
     "remove": ["per_user"], "add": [{...}],
     "assumptions": ["what was interpreted"]}
  ],
  "groups": [
    {"name": "Manash group", "kind": "pooled", "group_min_amount": 20000, "parent_row": 809, "member_rows": [810], "notes": "..."},
    {"name": "Shree group", "pool_load": true}                                                        # update an existing group
  ]
}
"""
from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from app.models import BillingAccount, BillingGroup, BillingGroupMember, Contract, ContractComponent
from app.services import billing_v2, engine2

GROUP_KINDS = ("net_parent", "equal_split", "pooled", "per_entity_min")
GROUP_FIELDS = ("kind", "group_min_amount", "rate", "pool_load", "units", "subtract", "notes")
CONTRACT_FIELDS = ("overall_min_amount", "overall_cap_amount", "mg_effective_from", "prorate_mg")


def _signature(components: list[dict], contract_values: dict) -> tuple:
    return (tuple(sorted((c["component_type"], repr(sorted((c.get("params") or {}).items()))) for c in components)), tuple(sorted((k, repr(v)) for k, v in contract_values.items())))


def _desired(latest: Contract, c: dict) -> tuple[list[dict], dict]:
    comps = [{"component_type": k.component_type, "label": k.label, "params": copy.deepcopy(k.params or {})} for k in latest.components]
    if "components" in c:
        comps = [{"component_type": x["component_type"], "label": x.get("label", ""), "params": x.get("params", {})} for x in c["components"]]
    for m in c.get("modify", []):
        target = next((x for x in comps if x["component_type"] == m["component_type"]), None)
        if target is None:
            raise ValueError(f"no '{m['component_type']}' charge to modify")
        target["params"] = {**target["params"], **m.get("set_params", {})}
    comps = [x for x in comps if x["component_type"] not in set(c.get("remove", []))]
    for x in c.get("add", []):  # safe to apply twice: a charge that is already there is not added again
        new = {"component_type": x["component_type"], "label": x.get("label", ""), "params": x.get("params", {})}
        if not any(k["component_type"] == new["component_type"] and k["params"] == new["params"] for k in comps):
            comps.append(new)
    unknown = [x["component_type"] for x in comps if x["component_type"] not in engine2.COMPONENTS]
    if unknown:
        raise ValueError(f"unknown charge type(s): {', '.join(unknown)}")
    values = {f: getattr(latest, f) for f in CONTRACT_FIELDS}
    values.update({f: c[f] for f in CONTRACT_FIELDS if f in c})
    return comps, values


def apply(db: Session, payload: dict, user_email: str, lock: bool = False) -> dict:
    out = {"drafted": [], "locked": [], "unchanged": [], "errors": [], "groups": []}
    by_row = {a.source_row: a for a in db.query(BillingAccount).filter(BillingAccount.source_row.isnot(None))}
    for c in payload.get("corrections", []):
        label = f"row {c.get('row')} {c.get('customer', '')}".strip()
        acct = by_row.get(c.get("row"))
        if acct is None or not acct.contracts:
            out["errors"].append(f"{label}: no such account with a contract")
            continue
        latest = max(acct.contracts, key=lambda x: x.version)
        try:
            comps, values = _desired(latest, c)
        except ValueError as exc:
            out["errors"].append(f"{label}: {exc}")
            continue
        before = _signature([{"component_type": k.component_type, "params": k.params} for k in latest.components], {f: getattr(latest, f) for f in CONTRACT_FIELDS})
        if before == _signature(comps, values):
            out["unchanged"].append(label)
            continue
        new = Contract(account_id=acct.id, version=latest.version + 1, valid_from=latest.valid_from, valid_to=None, billing_frequency=latest.billing_frequency,
                       gst_mode=latest.gst_mode, gst_rate=latest.gst_rate, subscription_covers=latest.subscription_covers, status="draft", source="correction",
                       created_by=user_email, assumptions=[{"text": t, "confirmed_by": "", "confirmed_at": None} for t in c.get("assumptions", [])],
                       notes=f"Correction: {c.get('note', '')}".strip(), **values)
        db.add(new)
        db.flush()
        for i, k in enumerate(comps):
            db.add(ContractComponent(contract_id=new.id, component_type=k["component_type"], label=k["label"], params=k["params"], sort_order=i))
        db.flush()
        db.refresh(new)
        item = {"row": c["row"], "name": acct.display_name, "version": new.version, "contract_id": new.id}
        out["drafted"].append(item)
        if lock:
            try:
                billing_v2.lock_contract(new, user_email)
                out["locked"].append(item)
            except ValueError as exc:
                out["errors"].append(f"{label}: drafted but not locked — {exc}")
    for g in payload.get("groups", []):
        group = db.query(BillingGroup).filter(BillingGroup.name == g["name"]).first()
        bad = [k for k in ("kind",) if k in g and g[k] not in GROUP_KINDS]
        if bad:
            out["errors"].append(f"group {g['name']}: unknown kind '{g['kind']}'")
            continue
        created = group is None
        if created:
            group = BillingGroup(name=g["name"], kind=g.get("kind", "net_parent"))
            db.add(group)
            db.flush()
        for f in GROUP_FIELDS:
            if f in g:
                setattr(group, f, g[f])
        if "parent_row" in g or "member_rows" in g:
            rows = [(g.get("parent_row"), True)] + [(r, False) for r in g.get("member_rows", [])]
            missing = [r for r, _ in rows if r is not None and r not in by_row]
            if missing:
                out["errors"].append(f"group {g['name']}: no account for row(s) {missing}")
                continue
            for m in db.query(BillingGroupMember).filter(BillingGroupMember.group_id == group.id).all():
                db.delete(m)
            db.flush()
            for r, is_parent in rows:
                if r is not None:
                    db.add(BillingGroupMember(group_id=group.id, account_id=by_row[r].id, is_parent=is_parent))
        out["groups"].append({"name": group.name, "created": created})
    db.commit()
    return out
