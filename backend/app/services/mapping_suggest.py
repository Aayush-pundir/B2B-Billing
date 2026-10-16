"""Suggest which platform enterprise belongs to which billing account, from an export of the platform's customer list.
Only high-confidence matches (GSTIN, or the same name once legal suffixes are ignored) are pre-filled in the mapping CSV;
close matches are shown beside it for a person to accept. Nothing is applied until Finance imports the reviewed file."""
from __future__ import annotations

import csv
import io
import re

from sqlalchemy.orm import Session

from app.models import BillingAccount
from app.services.zoho_sync import normalise, similarity

ID_COLS = ("enterpriseid", "enterprise_id", "enterprise id", "id", "entid")
NAME_COLS = ("name", "enterprisename", "enterprise_name", "enterprise name", "companyname", "company_name", "businessname", "business_name", "legalname", "customername", "customer")
GST_COLS = ("gstin", "gst", "gstno", "gst_no", "gst number")
CLOSE = 0.88


def _pick(headers: list[str], options: tuple[str, ...]) -> str | None:
    lowered = {h.strip().lower(): h for h in headers if h}
    return next((lowered[o] for o in options if o in lowered), None)


def read_platform_list(text: str) -> list[dict]:
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    id_col, name_col, gst_col = _pick(rdr.fieldnames or [], ID_COLS), _pick(rdr.fieldnames or [], NAME_COLS), _pick(rdr.fieldnames or [], GST_COLS)
    if not id_col or not name_col:
        raise ValueError(f"The file needs an enterprise-ID column ({', '.join(ID_COLS[:2])}) and a name column ({', '.join(NAME_COLS[:3])}…). Found: {', '.join(rdr.fieldnames or [])}")
    return [{"id": (r[id_col] or "").strip(), "name": (r[name_col] or "").strip(), "gstin": (r.get(gst_col) or "").strip().upper() if gst_col else ""}
            for r in rdr if (r[id_col] or "").strip()]


def suggest(db: Session, platform: list[dict]) -> dict:
    by_gst: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for p in platform:
        if p["gstin"]:
            by_gst.setdefault(p["gstin"], []).append(p)
        by_name.setdefault(normalise(p["name"]), []).append(p)
    names = list(by_name)

    out, claimed = [], {}
    for acct in db.query(BillingAccount).order_by(BillingAccount.source_row).all():
        current = [e.enterprise_id for e in acct.enterprises]
        already = bool(current) and not any(i.startswith(("ROW-", "UNMAPPED")) for i in current)
        gst = (acct.client.gst or "").strip().upper()
        match, kind, score = [], "none", 0.0
        if gst and gst in by_gst:
            match, kind, score = by_gst[gst], "gstin", 1.0
        else:
            for cand in (acct.zoho_billing_name, acct.display_name, acct.client.name):
                if cand and normalise(cand) in by_name:
                    match, kind, score = by_name[normalise(cand)], "name", 0.98
                    break
        if not match:
            best = max(((similarity(normalise(acct.zoho_billing_name or acct.display_name), n), n) for n in names), default=(0.0, ""))
            if best[0] >= CLOSE:
                match, kind, score = by_name[best[1]], "close", round(best[0], 2)
        ids = [m["id"] for m in match]
        out.append({"acct": acct, "ids": ids, "kind": kind, "score": score, "platform_names": sorted({m["name"] for m in match}), "already": already})
        for i in ids:
            claimed.setdefault(i, []).append(acct.display_name)
    for o in out:
        o["conflict"] = any(len(claimed[i]) > 1 for i in o["ids"])
    return {"rows": out, "platform_total": len(platform)}


def to_csv(result: dict) -> tuple[str, dict]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_row", "customer", "current_enterprise_id", "enterprise_id (use ; to add more)", "match", "confidence", "platform_name", "note"])
    counts = {"auto": 0, "review": 0, "none": 0, "already_mapped": 0, "conflicts": 0}
    for o in result["rows"]:
        a = o["acct"]
        note, fill = "", ""
        if o["already"]:
            counts["already_mapped"] += 1
            note = "already mapped; left alone"
        elif o["kind"] in ("gstin", "name") and not o["conflict"]:
            fill = ";".join(o["ids"])
            counts["auto"] += 1
            note = "several platform enterprises share this name: all are linked; check they belong together" if len(o["ids"]) > 1 else ""
        elif o["kind"] == "none":
            counts["none"] += 1
            note = "no match in the platform list"
        else:
            counts["review"] += 1
            counts["conflicts"] += int(o["conflict"])
            note = ("the same platform enterprise matched more than one account: decide which one owns it. " if o["conflict"] else "") + \
                   (f"close match: suggested {';'.join(o['ids'])}, not filled in until you check it" if o["kind"] == "close" else "")
            if o["kind"] == "close" and not o["conflict"]:
                note = f"close match ({o['score']}): suggested {';'.join(o['ids'])}. Copy it into the enterprise_id column if it is right"
        w.writerow([a.source_row or "", a.display_name, a.client.enterprise_id, fill, o["kind"] if o["kind"] != "none" else "", o["score"] or "", " | ".join(o["platform_names"]), note])
    return buf.getvalue(), counts
