# B2B Billing Automation Platform

Offline-first tool to automate monthly usage-based billing for B2B corporate
clients: auto-extracts commercial terms from agreement documents (DOCX,
digital/scanned PDF, PNG/JPG — including phone-camera photos), runs a
flexible slab billing engine over monthly CSV usage exports, applies GST/TDS,
and pushes draft invoices to Zoho Books for review. Nothing is ever
auto-approved — every extraction lands as a DRAFT commercial and every
invoice as a DRAFT in Zoho, always human-reviewed before it goes out.

Built entirely offline — no external AI API cost. All extraction, billing
calculation, and reporting runs locally.

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
