"""Default Indian chart of accounts. System accounts are referenced by the
auto-posting ledger and cannot be deleted; users may add their own."""
from sqlalchemy.orm import Session

from app.models import Account

# code, name, type, system
DEFAULT_ACCOUNTS = [
    ("1100", "Bank & Cash", "asset", True),
    ("1200", "Accounts Receivable", "asset", True),
    ("1250", "TDS Receivable", "asset", True),
    ("1310", "Input CGST", "asset", True),
    ("1320", "Input SGST", "asset", True),
    ("1330", "Input IGST", "asset", True),
    ("1400", "Prepaid & Other Current Assets", "asset", False),
    ("1500", "Fixed Assets", "asset", False),
    ("2100", "Accounts Payable", "liability", True),
    ("2250", "Unallocated Customer Receipts", "liability", True),
    ("2260", "Other Receipts (Suspense)", "liability", True),
    ("2310", "Output CGST", "liability", True),
    ("2320", "Output SGST", "liability", True),
    ("2330", "Output IGST", "liability", True),
    ("2400", "TDS Payable", "liability", True),
    ("2500", "Other Current Liabilities", "liability", False),
    ("3000", "Owner's Equity / Share Capital", "equity", True),
    ("4000", "Sales — Services", "income", True),
    ("4100", "Sales — Other", "income", False),
    ("4800", "Other Income", "income", False),
    ("4900", "Sales Returns & Credit Notes", "income", True),
    ("5000", "Cost of Services", "expense", False),
    ("5100", "Salaries & Wages", "expense", False),
    ("5200", "Rent", "expense", False),
    ("5300", "Software & Hosting", "expense", False),
    ("5400", "Marketing & Advertising", "expense", False),
    ("5500", "Professional & Legal Fees", "expense", False),
    ("5600", "Travel & Conveyance", "expense", False),
    ("5700", "Office & Administrative", "expense", False),
    ("5800", "Bad Debts Written Off", "expense", True),
    ("5900", "Bank Charges", "expense", False),
]

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")


def seed_accounts(db: Session) -> int:
    have = {a.code for a in db.query(Account).all()}
    added = 0
    for code, name, typ, system in DEFAULT_ACCOUNTS:
        if code not in have:
            db.add(Account(code=code, name=name, type=typ, system=system))
            added += 1
    if added:
        db.commit()
    return added
