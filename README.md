# B2B Billing Automation Platform

Offline-first, end-to-end order-to-cash (O2C) platform for B2B corporate
billing: auto-extracts commercial terms from agreement documents (DOCX,
digital/scanned PDF, PNG/JPG — including phone-camera photos), runs a
flexible slab billing engine over monthly CSV usage exports, applies GST/TDS,
issues native invoices (PDF + email), and closes the loop with bank-statement
reconciliation against real receivables — not just a Zoho status flag, since
most B2B clients pay by direct NEFT/RTGS that never touches Zoho at all.
Zoho Books push remains available as an optional delivery channel. Nothing is
ever auto-approved — every extraction lands as a DRAFT commercial, and every
payment match below full confidence lands in a human review queue.

Built entirely offline — no external AI API cost. All extraction, billing
calculation, reconciliation, and reporting runs locally.

## Order-to-cash cycle

1. **Client sync** — bulk CSV import, manual entry, or a one-way pull from
   your CRM's account export (CRM owns identity fields; this platform owns
   billing data — see CRM Sync page).
2. **Commercial capture** — agreement upload → offline OCR extraction →
   review → lock (immutable version snapshot).
3. **Monthly billing** — upload the three usage CSVs, the slab engine
   computes GST/TDS and produces draft invoices.
4. **Issue & deliver** — approve the draft, get a native PDF invoice with its
   own numbering, email it (or push to Zoho as an optional channel).
5. **Reconcile** — upload a bank statement (any bank's CSV export) or a
   manual collections sheet; a five-tier matching engine auto-closes
   high-confidence matches and queues the rest — including partial and
   split payments — for one-click human confirmation.
6. **Aging & follow-up** — receivables aging report, automatic overdue
   reminders, dispute flag (pauses the aging clock), and credit notes.
7. **Statement of account** — a running per-client ledger of every invoice
   issued and payment received, available from the Clients page.

## Quick start

```
./START.sh          # macOS/Linux
START.bat           # Windows
```

This installs backend dependencies, starts the FastAPI backend on
`http://localhost:8502`, and serves the frontend on `http://localhost:8501`.

First run copies `backend/.env.example` to `backend/.env` — edit it to add
real org GST details, OAuth credentials, or Zoho Books credentials. Everything
works out of the box in "dev login + Zoho simulation" mode without any of
that.

Sign in with the dev-login screen — pick a role (admin/accounts/finance/
sales/viewer) and any email. The first login using `BOOTSTRAP_ADMIN_EMAIL`
(default `admin@example.local`) becomes admin automatically.

## Architecture

- **Backend** — Python FastAPI (`backend/app`), SQLAlchemy models, business
  logic in `services/`.
- **Frontend** — Vanilla JS + CSS single-page app (`frontend/`), no build step.
- **Database** — SQLite by default (`backend/data/billing.db`). Set
  `DATABASE_URL=postgresql://...` in `.env` to upgrade to Postgres — no code
  change needed.
- **Storage** — Local `backend/data/uploads/` for agreement files.

### Monthly billing cycle

1. Add clients (EnterpriseId, name, GST, state code) — manually or bulk CSV import.
2. Upload agreement (PDF/DOCX/PNG/JPG, including scans and phone photos).
3. Extract commercial terms offline — python-docx / pdfplumber / PyMuPDF+easyocr.
4. Review extracted slabs, edit as needed, lock the commercial (creates an
   immutable version snapshot).
5. Every month: upload the three usage CSVs (Loading, UPI, Users).
6. The engine aggregates usage per client, applies the locked slab rules,
   computes GST (CGST+SGST intra-state vs IGST inter-state) and optional TDS.
7. Review generated draft invoices on-screen, then push to Zoho Books as
   DRAFT (single line item, full breakdown in notes) — or run in offline
   simulation mode with no Zoho credentials configured.

### Billing engine

Supports percent-of-usage with floor (cliff), multi-tier slabs with
per-tier floors, flat monthly fees, per-unit pricing with minimum quantity,
hybrid fixed+per-unit, annual per-user lump-sum billing, multi-metric
commercials (several rules per client), add-on line items, and annual rate
escalation. See `backend/app/services/engine.py`.

### Offline extraction

| File type          | Tool                    |
|---------------------|-------------------------|
| `.docx`             | python-docx (last table) |
| `.pdf` (digital)     | pdfplumber              |
| `.pdf` (scanned)     | PyMuPDF + easyocr        |
| `.png` / `.jpg`      | easyocr (covers phone photos) |

Every extracted rule carries a confidence score; low-confidence fields are
flagged in the UI. Extraction always creates a DRAFT commercial — a human
validates before anything is billed.

## Development

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8502
```

```
cd frontend
python3 -m http.server 8501
```

API docs: `http://localhost:8502/docs`
