import os
import secrets
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/billing.db")
        self.JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)
        self.BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.local")

        self.ORG_NAME = os.getenv("ORG_NAME", "Your Company Pvt Ltd")
        self.ORG_GST = os.getenv("ORG_GST", "")
        self.ORG_STATE_CODE = os.getenv("ORG_STATE_CODE", "09")
        self.INVOICE_PREFIX = os.getenv("INVOICE_PREFIX", "INV-")
        self.DEFAULT_GST_RATE = float(os.getenv("DEFAULT_GST_RATE", "18.0"))
        self.PAYMENT_TERMS_DAYS = int(os.getenv("PAYMENT_TERMS_DAYS", "15"))

        self.SMTP_HOST = os.getenv("SMTP_HOST", "")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USER = os.getenv("SMTP_USER", "")
        self.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
        self.SMTP_FROM = os.getenv("SMTP_FROM", "")

        # Read-only connection to the platform database holding raw usage (loading, UPI, users).
        # Kept in the environment — never stored in the app database or shown in the UI.
        self.USAGE_DATABASE_URL = os.getenv("USAGE_DATABASE_URL", "")

        self.UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")

        self.AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
        self.AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
        self.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

        self.ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "")
        self.ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "")
        self.ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "")
        self.ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "")
        self.ZOHO_API_BASE = os.getenv("ZOHO_API_BASE", "https://www.zohoapis.in/books/v3")


@lru_cache
def get_settings() -> Settings:
    return Settings()
