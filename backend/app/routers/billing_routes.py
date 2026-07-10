import datetime
import io
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agreement, BillingRun, Client, Commercial, Invoice, User
from app.services import csv_parser, settings_store, zoho
from app.services.auth import require_user, require_write
from app.services.engine import compute_invoice
from app.routers.core_routes import log_action

router = APIRouter(prefix="/api/billing")

HEADER_FILL = PatternFill(start_color="E8622A", end_color="E8622A", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _next_invoice_no(db: Session, prefix: str) -> str:
    count = db.query(Invoice).count()
    return f"{prefix}{2480 + count}"


def _is_annual_due(client: Client, commercial: Commercial, period: str, confirm_ids: set[str]) -> tuple[bool, str]:
    if client.billing_frequency != "annual":
        return True, ""
    if client.enterprise_id not in confirm_ids:
        return False, "annual billing not confirmed this period"
    if not commercial.first_invoice_date:
        return True, ""  # first invoice ever — always due once confirmed
    anniversary_month = commercial.first_invoice_date[5:7]
    period_month = period[5:7]
    if anniversary_month != period_month:
        return False, f"not due until anniversary month {anniversary_month}"
    return True, ""


@router.post("/calculate")
async def calculate_billing(
    period: str = Form(...),
    tds_rate: float = Form(0.0),
    confirm_annual: str = Form(""),  # comma-separated EnterpriseIds
    loading_file: UploadFile = File(...),
    upi_file: UploadFile = File(...),
    users_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_write("billing")),
):
    loading = csv_parser.parse_loading_csv(await loading_file.read())
    upi = csv_parser.parse_upi_csv(await upi_file.read())
    users = csv_parser.parse_users_csv(await users_file.read())

    clients = db.query(Client).all()
    known_ids = {c.enterprise_id for c in clients}
    usage_by_client, unknown_ids = csv_parser.aggregate_usage(loading, upi, users, known_ids)

    confirm_ids = {x.strip() for x in confirm_annual.split(",") if x.strip()}

    settings = settings_store.get_all_settings(db)
    org_state_code = settings["ORG_STATE_CODE"]
    gst_rate = float(settings["DEFAULT_GST_RATE"])
    prefix = settings["INVOICE_PREFIX"]

    run = BillingRun(period=period, tds_rate=tds_rate, status="draft", created_by=user.email)
    db.add(run)
    db.commit()
    db.refresh(run)

    invoices_out = []
    skipped = []
    for client in clients:
        commercial = db.query(Commercial).filter(Commercial.client_id == client.id, Commercial.status == "locked").first()
        if commercial is None:
            skipped.append({"enterprise_id": client.enterprise_id, "reason": "no locked commercial"})
            continue
        due, reason = _is_annual_due(client, commercial, period, confirm_ids)
        if not due:
            skipped.append({"enterprise_id": client.enterprise_id, "reason": reason})
            continue
        usage = usage_by_client.get(client.enterprise_id, {
            "loading": 0, "upi_transactions": 0, "upi_amount": 0, "admin_users": 0, "spender_users": 0, "total_users": 0,
        })
        result = compute_invoice(commercial.rules or [], usage, client.state_code, org_state_code, gst_rate, tds_rate, is_annual_due=True)
        if not result["lines"] or result["subtotal"] <= 0:
            skipped.append({"enterprise_id": client.enterprise_id, "reason": "zero usage / no billable lines"})
            continue

        invoice_no = _next_invoice_no(db, prefix)
        invoice = Invoice(
            run_id=run.id, client_id=client.id, invoice_no=invoice_no, period=period,
            lines=result["lines"], subtotal=result["subtotal"], gst_type=result["gst_type"],
            gst=result["gst"], tds=result["tds"], total=result["total"],
        )
        db.add(invoice)

        if client.billing_frequency == "annual" and not commercial.first_invoice_date:
            commercial.first_invoice_date = f"{period}-06"

        invoices_out.append(invoice_no)

    run.summary = {
        "invoices_generated": len(invoices_out), "skipped": skipped, "unknown_enterprise_ids": unknown_ids,
    }
    db.commit()
    log_action(db, user, "billing.calculate", "billing_run", f"run={run.id} invoices={len(invoices_out)}")
    return {"run_id": run.id, "invoices": invoices_out, "skipped": skipped, "unknown_enterprise_ids": unknown_ids}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(BillingRun).order_by(BillingRun.id.desc()).all()
    return [{
        "id": r.id, "period": r.period, "tds_rate": r.tds_rate, "status": r.status,
        "summary": r.summary, "created_by": r.created_by, "created_at": r.created_at.isoformat(),
        "invoice_count": len(r.invoices),
    } for r in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    run = db.query(BillingRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    invoices = []
    for inv in run.invoices:
        client = db.query(Client).get(inv.client_id)
        invoices.append({
            "id": inv.id, "invoice_no": inv.invoice_no, "client_name": client.name if client else "?",
            "enterprise_id": client.enterprise_id if client else "?", "lines": inv.lines,
            "subtotal": inv.subtotal, "gst_type": inv.gst_type, "gst": inv.gst, "tds": inv.tds,
            "total": inv.total, "status": inv.status, "zoho_invoice_id": inv.zoho_invoice_id,
        })
    return {"id": run.id, "period": run.period, "tds_rate": run.tds_rate, "status": run.status,
            "summary": run.summary, "invoices": invoices}


@router.post("/runs/{run_id}/push-zoho")
def push_run_to_zoho(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    run = db.query(BillingRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    pushed, failed = [], []
    for inv in run.invoices:
        if inv.status == "pushed":
            continue
        client = db.query(Client).get(inv.client_id)
        if not client.zoho_contact_id:
            failed.append({"invoice_no": inv.invoice_no, "reason": "client has no Zoho contact"})
            continue
        period_label = f"Invoice for the month of {run.period}"
        breakdown_lines = [f"{l['label']}: ₹{l['amount']:,.2f} ({l['calc_detail']})" for l in inv.lines]
        notes = "\n".join(breakdown_lines) + f"\n{inv.gst_type}: ₹{inv.gst:,.2f} · TDS: ₹{inv.tds:,.2f} · Total: ₹{inv.total:,.2f}"
        try:
            result = zoho.push_draft_invoice(client.zoho_contact_id, period_label, inv.total, notes)
        except Exception as exc:
            failed.append({"invoice_no": inv.invoice_no, "reason": str(exc)})
            continue
        inv.zoho_invoice_id = result["invoice_id"]
        inv.status = "pushed"
        pushed.append(inv.invoice_no)
    if pushed and not failed:
        run.status = "pushed"
    db.commit()
    log_action(db, user, "billing.push_zoho", "billing_run", f"run={run_id} pushed={len(pushed)} failed={len(failed)}")
    return {"pushed": pushed, "failed": failed}


@router.get("/invoices")
def list_invoices(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.query(Invoice).order_by(Invoice.id.desc()).all()
    out = []
    for inv in rows:
        client = db.query(Client).get(inv.client_id)
        out.append({
            "id": inv.id, "invoice_no": inv.invoice_no, "period": inv.period,
            "client_name": client.name if client else "?", "enterprise_id": client.enterprise_id if client else "?",
            "subtotal": inv.subtotal, "gst_type": inv.gst_type, "gst": inv.gst, "tds": inv.tds,
            "total": inv.total, "status": inv.status, "zoho_invoice_id": inv.zoho_invoice_id,
        })
    return out


@router.get("/alerts/expiry")
def expiry_alerts(db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = datetime.date.today()
    agreements = db.query(Agreement).filter(Agreement.end_date.isnot(None)).all()
    danger, warn = [], []
    for a in agreements:
        try:
            end = datetime.date.fromisoformat(a.end_date)
        except (ValueError, TypeError):
            continue
        days_left = (end - today).days
        client = db.query(Client).get(a.client_id)
        item = {"client_name": client.name if client else "?", "file_name": a.file_name,
                "end_date": a.end_date, "days_left": days_left}
        if 0 <= days_left <= 30:
            danger.append(item)
        elif 30 < days_left <= 60:
            warn.append(item)
    return {"danger": danger, "warn": warn}


@router.get("/alerts/annual-due")
def annual_due_alerts(period: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    clients = db.query(Client).filter(Client.billing_frequency == "annual").all()
    due = []
    for client in clients:
        commercial = db.query(Commercial).filter(Commercial.client_id == client.id, Commercial.status == "locked").first()
        if not commercial:
            continue
        if not commercial.first_invoice_date:
            due.append({"enterprise_id": client.enterprise_id, "name": client.name, "reason": "first invoice"})
        elif commercial.first_invoice_date[5:7] == period[5:7]:
            due.append({"enterprise_id": client.enterprise_id, "name": client.name, "reason": "anniversary month"})
    return due


@router.post("/zoho/create-contact/{client_id}")
def create_zoho_contact(client_id: int, db: Session = Depends(get_db), user: User = Depends(require_write("billing"))):
    client = db.query(Client).get(client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    result = zoho.create_contact(client.name, client.gst)
    client.zoho_contact_id = result["contact_id"]
    db.commit()
    log_action(db, user, "zoho.create_contact", "client", f"{client.enterprise_id} -> {result['contact_id']}")
    return result


def _autosize_and_style(ws, headers: list[str]):
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for col_idx, header in enumerate(headers, start=1):
        ws.column_dimensions[chr(64 + col_idx) if col_idx <= 26 else "A"].width = max(14, len(header) + 4)


@router.get("/reports/export")
def export_report(kind: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    wb = Workbook()
    ws = wb.active

    if kind == "invoice_register":
        ws.title = "Invoice Register"
        _autosize_and_style(ws, ["Invoice No", "Client", "Period", "Subtotal", "GST", "TDS", "Total", "GST Type", "Status", "Zoho ID"])
        for inv in db.query(Invoice).order_by(Invoice.id).all():
            client = db.query(Client).get(inv.client_id)
            ws.append([inv.invoice_no, client.name if client else "?", inv.period, inv.subtotal, inv.gst,
                       inv.tds, inv.total, inv.gst_type, inv.status, inv.zoho_invoice_id or ""])
    elif kind == "revenue_summary":
        ws.title = "Revenue Summary"
        _autosize_and_style(ws, ["Period", "Invoice Count", "Subtotal", "GST", "Total", "Status"])
        for run in db.query(BillingRun).order_by(BillingRun.id).all():
            subtotal = sum(i.subtotal for i in run.invoices)
            gst = sum(i.gst for i in run.invoices)
            total = sum(i.total for i in run.invoices)
            ws.append([run.period, len(run.invoices), subtotal, gst, total, run.status])
    elif kind == "client_master":
        ws.title = "Client Master"
        _autosize_and_style(ws, ["EnterpriseId", "Name", "GST", "State", "Billing Frequency", "Commercial Status", "Zoho Contact"])
        for client in db.query(Client).all():
            commercial = db.query(Commercial).filter(Commercial.client_id == client.id).order_by(Commercial.id.desc()).first()
            ws.append([client.enterprise_id, client.name, client.gst, client.state, client.billing_frequency,
                       commercial.status if commercial else "none", client.zoho_contact_id or ""])
    else:
        raise HTTPException(400, "kind must be invoice_register|revenue_summary|client_master")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={kind}.xlsx"},
    )
