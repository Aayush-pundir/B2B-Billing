import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import Base, engine, SessionLocal
from app.models import Metric, Addon
from app.routers import auth_routes, core_routes, agreement_routes, billing_routes, reconciliation_routes, crm_routes

settings = get_settings()

os.makedirs(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")) or ".", exist_ok=True) \
    if settings.DATABASE_URL.startswith("sqlite") else None
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="B2B Billing Automation Platform", version="1.0")

app.add_middleware(
    CORSMiddleware,
    # allow_origins=["*"] is rejected by browsers once allow_credentials=True
    # (the cb_token auth cookie requires it) — a regex reflects the calling
    # origin instead, keeping any localhost/127.0.0.1 port working in dev.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(core_routes.router)
app.include_router(agreement_routes.router)
app.include_router(billing_routes.router)
app.include_router(reconciliation_routes.router)
app.include_router(crm_routes.router)

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
        db.commit()
    finally:
        db.close()
