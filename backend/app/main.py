import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import Base, engine, SessionLocal
from app.migrate import ensure_schema
from app.models import Metric, Addon
from app.routers import auth_routes, core_routes, agreement_routes, billing_routes, reconciliation_routes, crm_routes, datasource_routes, automation_routes, portal_routes, items_routes, sales_routes, purchases_routes, accounting_routes, accounts_v2_routes

settings = get_settings()

os.makedirs(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")) or ".", exist_ok=True) \
    if settings.DATABASE_URL.startswith("sqlite") else None
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

ensure_schema(engine, Base)

app = FastAPI(title="B2B Billing Automation Platform", version="1.0")

# Frontend and backend are one deployable unit: the SPA is served straight
# off disk by this same process, on this same port. No CORS layer needed —
# there is no second origin anymore.
app.include_router(auth_routes.router)
app.include_router(core_routes.router)
app.include_router(agreement_routes.router)
app.include_router(billing_routes.router)
app.include_router(reconciliation_routes.router)
app.include_router(crm_routes.router)
app.include_router(datasource_routes.router)
app.include_router(automation_routes.router)
app.include_router(portal_routes.router)
app.include_router(items_routes.router)
app.include_router(sales_routes.router)
app.include_router(purchases_routes.router)
app.include_router(accounting_routes.router)
app.include_router(accounts_v2_routes.router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

DEFAULT_METRICS = [
    ("loading", "Loading (Add Money)", "amount"),
    ("upi_transactions", "UPI Transactions", "count"),
    ("admin_users", "Admin Users", "count"),
    ("spender_users", "Spender Users", "count"),
    ("total_users", "Total Users", "count"),
]
DEFAULT_ADDONS = [
    ("bulk_disbursal", "Bulk Disbursal", "annual"),
    ("edit_log_trails", "Edit Log Trails", "flat"),
]


@app.on_event("startup")
def seed_data():
    db = SessionLocal()
    try:
        if db.query(Metric).count() == 0:
            for code, label, basis in DEFAULT_METRICS:
                db.add(Metric(code=code, label=label, basis=basis, active=True))
        if db.query(Addon).count() == 0:
            for code, name, rate_type in DEFAULT_ADDONS:
                db.add(Addon(code=code, name=name, default_rate_type=rate_type, active=True))
        from app.services.chart import seed_accounts
        seed_accounts(db)
        # One-time upgrades for data created before Phase 2
        from app.models import BillingRun, Invoice
        from app.services import invoicing
        for run in db.query(BillingRun).filter(BillingRun.status == "draft").all():
            run.status, run.approved_by = "approved", "legacy (before approval workflow)"
        for inv in db.query(Invoice).filter(Invoice.public_token.is_(None), Invoice.status.notin_(["draft", "cancelled"])).all():
            invoicing.ensure_token(inv)
        db.commit()
    finally:
        db.close()
    if os.getenv("DISABLE_SCHEDULER") != "1":
        from app.services import scheduler
        scheduler.start_background()
