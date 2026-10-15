import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship

from app.database import Base


def now():
    return datetime.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    role = Column(String, nullable=False, default="viewer")  # admin, accounts, finance, sales, viewer
    provider = Column(String, default="dev")  # dev, microsoft, google
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    enterprise_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    gst = Column(String, default="")
    pan = Column(String, default="")
    state = Column(String, default="")
    state_code = Column(String, default="")
    billing_contact_email = Column(String, default="")
    billing_frequency = Column(String, default="monthly")  # monthly, annual
    zoho_contact_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    # --- customer master data ---
    status = Column(String, default="active")  # active, inactive, blocked
    customer_type = Column(String, default="business")  # business, individual
    legal_name = Column(String, default="")
    trade_name = Column(String, default="")
    gst_treatment = Column(String, default="registered")  # registered, unregistered, composition, sez, overseas
    tan = Column(String, default="")
    cin = Column(String, default="")
    msme_no = Column(String, default="")
    address_line1 = Column(String, default="")
    address_line2 = Column(String, default="")
    city = Column(String, default="")
    pincode = Column(String, default="")
    country = Column(String, default="India")
    contact_name = Column(String, default="")
    contact_phone = Column(String, default="")
    cc_emails = Column(String, default="")  # comma-separated extra recipients for invoices/reminders
    currency = Column(String, default="INR")
    payment_terms_days = Column(Integer, nullable=True)  # overrides org default when set
    credit_limit = Column(Float, default=0.0)  # 0 = no limit
    account_manager = Column(String, default="")
    segment = Column(String, default="")
    notes = Column(Text, default="")

    agreements = relationship("Agreement", back_populates="client", cascade="all, delete-orphan")
    commercials = relationship("Commercial", back_populates="client", cascade="all, delete-orphan")


class Agreement(Base):
    __tablename__ = "agreements"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    file_name = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    file_type = Column(String, default="")  # docx, pdf, png, jpg
    agreement_date = Column(String, nullable=True)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    version = Column(Integer, default=1)
    extracted_json = Column(JSON, nullable=True)
    uploaded_at = Column(DateTime, default=now)

    client = relationship("Client", back_populates="agreements")


class Commercial(Base):
    __tablename__ = "commercials"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    billing_frequency = Column(String, default="monthly")  # monthly, annual
    first_invoice_date = Column(String, nullable=True)
    rules = Column(JSON, default=list)  # list of rule dicts
    status = Column(String, default="draft")  # draft, locked
    version_no = Column(Integer, default=0)
    # How the current terms got here: manual, agreement (imported from a reviewed
    # agreement), excel (bulk upload). Amended only by the finance role.
    source = Column(String, default="manual")
    source_ref = Column(String, default="")
    amended_by = Column(String, default="")
    amended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    client = relationship("Client", back_populates="commercials")
    versions = relationship("CommercialVersion", back_populates="commercial", cascade="all, delete-orphan")


class CommercialVersion(Base):
    __tablename__ = "commercial_versions"

    id = Column(Integer, primary_key=True)
    commercial_id = Column(Integer, ForeignKey("commercials.id"), nullable=False)
    version_no = Column(Integer, nullable=False)
    rules = Column(JSON, default=list)
    billing_frequency = Column(String, default="monthly")
    snapshot_reason = Column(String, default="locked")  # locked, escalation +10%
    source = Column(String, default="manual")
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=now)

    commercial = relationship("Commercial", back_populates="versions")


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # loading, upi_transactions, admin_users, spender_users, total_users
    label = Column(String, nullable=False)
    basis = Column(String, default="amount")  # amount, count
    active = Column(Boolean, default=True)


class Addon(Base):
    __tablename__ = "addons"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    default_rate_type = Column(String, default="flat")  # flat, per_unit, annual
    active = Column(Boolean, default=True)


class BillingRun(Base):
    __tablename__ = "billing_runs"

    id = Column(Integer, primary_key=True)
    period = Column(String, nullable=False)  # e.g. 2026-05
    tds_rate = Column(Float, default=0.0)
    status = Column(String, default="draft")  # draft, pushed
    summary = Column(JSON, default=dict)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=now)

    invoices = relationship("Invoice", back_populates="run", cascade="all, delete-orphan")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("billing_runs.id"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    invoice_no = Column(String, nullable=False)
    period = Column(String, nullable=False)
    lines = Column(JSON, default=list)
    subtotal = Column(Float, default=0.0)
    gst_type = Column(String, default="IGST")  # IGST or CGST_SGST
    gst = Column(Float, default=0.0)
    tds = Column(Float, default=0.0)
    total = Column(Float, default=0.0)
    # draft -> issued -> sent -> partially_paid -> paid ; or -> overdue / disputed / written_off
    status = Column(String, default="draft")
    zoho_invoice_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=now)

    payment_terms_days = Column(Integer, default=15)
    due_date = Column(String, nullable=True)
    issued_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    paid_amount = Column(Float, default=0.0)
    balance_due = Column(Float, default=0.0)
    disputed = Column(Boolean, default=False)
    dispute_reason = Column(String, default="")

    run = relationship("BillingRun", back_populates="invoices")
    client = relationship("Client")
    payments = relationship("Payment", back_populates="invoice")
    credit_notes = relationship("CreditNote", back_populates="invoice", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    mode = Column(String, default="")  # NEFT, RTGS, UPI, cheque, other
    reference = Column(String, default="")  # UTR / narration excerpt
    narration = Column(String, default="")
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    match_confidence = Column(String, default="")  # exact_reference, amount_client, amount_only, manual, none
    matched_by = Column(String, default="")  # auto, manual
    source = Column(String, default="bank_statement")  # bank_statement, manual_upload, zoho
    import_id = Column(Integer, ForeignKey("bank_statement_imports.id"), nullable=True)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=now)

    invoice = relationship("Invoice", back_populates="payments")
    client = relationship("Client")


class BankStatementImport(Base):
    __tablename__ = "bank_statement_imports"

    id = Column(Integer, primary_key=True)
    file_name = Column(String, default="")
    uploaded_by = Column(String, default="")
    rows_parsed = Column(Integer, default=0)
    rows_matched = Column(Integer, default=0)
    date_from = Column(String, nullable=True)
    date_to = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=now)


class CreditNote(Base):
    __tablename__ = "credit_notes"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    amount = Column(Float, nullable=False)
    reason = Column(String, default="")
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=now)

    invoice = relationship("Invoice", back_populates="credit_notes")


class ReminderLog(Base):
    __tablename__ = "reminders_log"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    days_overdue = Column(Integer, default=0)
    template = Column(String, default="")
    channel = Column(String, default="email")
    sent_at = Column(DateTime, default=now)


class ClientCrmLink(Base):
    __tablename__ = "client_crm_links"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, unique=True)
    crm_source = Column(String, default="generic_csv")  # generic_csv, zoho_crm, hubspot, salesforce
    crm_id = Column(String, default="")
    last_synced_at = Column(DateTime, default=now)


class CrmSyncRun(Base):
    __tablename__ = "crm_sync_runs"

    id = Column(Integer, primary_key=True)
    crm_source = Column(String, default="generic_csv")
    created_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    detail = Column(JSON, default=list)
    run_by = Column(String, default="")
    run_at = Column(DateTime, default=now)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user = Column(String, default="")
    action = Column(String, nullable=False)
    entity = Column(String, default="")
    detail = Column(Text, default="")
    ts = Column(DateTime, default=now)
