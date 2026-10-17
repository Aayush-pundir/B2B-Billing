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

One process, one port: `http://localhost:8000` serves both the API and the
frontend — there is no separate frontend server to run.

First run copies `backend/.env.example` to `backend/.env` — edit it to add
real org GST details, OAuth credentials, or Zoho Books credentials. Everything
works out of the box in "dev login + Zoho simulation" mode without any of
that.

Sign in with the dev-login screen — pick a role (admin/accounts/finance/
sales/viewer) and any email. The first login using `BOOTSTRAP_ADMIN_EMAIL`
(default `admin@example.local`) becomes admin automatically.

## Deploying (Render)

The repo includes a `Dockerfile` and `render.yaml` blueprint — it builds and
serves the whole app (frontend + API) as a single web service:

1. Push this repo to GitHub (already done if you're reading this from there).
2. On [render.com](https://render.com): **New** → **Blueprint** → connect
   this repo. Render reads `render.yaml` and creates the service automatically.
3. First deploy takes a few minutes; you'll get a public `onrender.com` URL
   serving the app.

**Data persistence note:** Render's free web-service plan has an ephemeral
filesystem — the SQLite database and uploaded agreements are wiped on every
deploy or restart. Fine for a first look. For anything real, either attach a
persistent Disk (needs a paid instance type) at `/app/data`, or point
`DATABASE_URL` at a managed Postgres instance instead (Render offers one;
no code change needed, see below).

**OCR extras are not installed by default on this deploy.** `backend/requirements.txt`
deliberately excludes PyMuPDF and easyocr — easyocr pulls in torch/torchvision,
multi-gigabyte dependencies that blow past the free-tier build's memory/disk/time
limit and were the actual cause of repeated Blueprint build failures. DOCX and
digital-PDF extraction work normally without them; uploading a scanned PDF or
an image returns a clear message instead of crashing the build. See
`backend/requirements-ocr.txt` to add OCR support on a host with real build
resources (a paid instance type, or anywhere outside the free tier).

`Procfile` is included as a fallback for buildpack-based hosts (Railway,
Heroku) that don't use the Dockerfile directly.

## Architecture

- **Backend** — Python FastAPI (`backend/app`), SQLAlchemy models, business
  logic in `services/`. Also serves the frontend as static files
  (`app.mount("/", StaticFiles(...))` in `main.py`) — one process, one port,
  no CORS to configure.
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
| `.pdf` (scanned)     | PyMuPDF + easyocr (optional — see `requirements-ocr.txt`) |
| `.png` / `.jpg`      | easyocr (optional — covers phone photos) |

Every extracted rule carries a confidence score; low-confidence fields are
flagged in the UI. Extraction always creates a DRAFT commercial — a human
validates before anything is billed.

## Development

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# optional, needs real build resources — adds scanned-PDF/image OCR:
# pip install -r requirements-ocr.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8502
```

```
cd frontend
python3 -m http.server 8501
```

API docs: `http://localhost:8502/docs`
