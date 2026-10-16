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

1. **Customer master data** — full identity/tax/contact/billing profile; bulk
   Excel/CSV import (create or update), manual entry, or a one-way pull from
   your CRM's account export (CRM owns identity fields; this platform owns
   billing data — see CRM Sync page).
2. **Billing terms (Finance only)** — three ways in: manual entry, import from
   a reviewed agreement (upload → offline OCR extraction → review → import),
   or bulk Excel. Always lands as a draft; lock it to create an immutable
   version snapshot. No other role can amend terms.
3. **Monthly billing** — usage is read straight from your platform database
   (`USAGE_DATABASE_URL`, read-only; queries editable in Settings). The slab
   engine computes GST/TDS and produces draft invoices. CSV upload remains as
   a fallback.
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

## Approvals, automation and the customer portal

- **Approval:** every billing run lands as *pending approval* with anomaly findings; Finance approves (or rejects with a reason). Nothing is issued before that. Policy is on the **Automation** page.
- **Automation:** an in-process scheduler (checks every 15 min) creates last month's run, sends dunning reminders and applies credit-block rules. Use **Run now** to trigger any step. If you run several server workers, run the scheduler in only one (`DISABLE_SCHEDULER=1` on the others).
- **Customer portal:** set `PUBLIC_BASE_URL` (and bank/UPI details) in **Settings**; each issued invoice gets a private link (`/portal.html?t=…`) included in the invoice email and reminders.

## Speed and navigation

- **Quick jump:** press `Ctrl/⌘ + K` (or `/`) anywhere to jump to a page, customer, billing account, invoice or vendor.
- **Invoices** filter by search / status / period and page 50 at a time on the server (`X-Total-Count` carries the total), so the list stays instant with tens of thousands of invoices.
- **Dashboard** is one small call (`/api/dashboard`) instead of downloading every client, run and invoice.
- **Reports** share one cached build of the derived ledger between writes (any commit invalidates it, a 5 s TTL bounds staleness across workers), and list endpoints no longer issue a query per row. A billing run over the ~800-account rulebook computes in about half a second. None of this changes a billing rule — the regression against the legacy sheet is identical.

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

`render.yaml` is a blueprint for one web service (API and UI together) plus a managed Postgres database.

1. On [render.com](https://render.com): **New** → **Blueprint** → pick this repo and the `claude/eager-fermat-4k13sp` branch (or `main` once merged).
2. Render asks for two values. They are never stored in the repo:
   - `ACCESS_PASSCODE`: the shared team passcode for sign-in. Choose something long.
   - `BOOTSTRAP_ADMIN_EMAIL`: your email. The first person to sign in with this address becomes admin.
3. Apply. The first build takes a few minutes. The public `onrender.com` address serves the app.
4. Sign in with your email and the passcode, then open **Users** and give each teammate their role (finance, accounts, sales, viewer). Anyone else who signs in starts as a read-only viewer; roles are never chosen on the login screen.

On a hosted server the built-in sign-in only works when `ACCESS_PASSCODE` is set. Without it, sign-in is switched off, so a public deployment can never let visitors pick their own role. Wrong passcodes are throttled (8 per 5 minutes per client).

**Before using real billing data:**
- Both plans in `render.yaml` are `free`. Render deletes a free Postgres database after about 30 days and the free web service sleeps when idle. Move both to a paid plan first.
- Uploaded agreements are stored on the container disk, which is wiped on every deploy. Attach a persistent disk at `/app/data` (paid plans) if you rely on the files.
- Optional settings go in the Render dashboard: `USAGE_DATABASE_URL` (read-only), `SMTP_*`, `ZOHO_*`.
- Single sign-on (Microsoft or Google) is not finished in this version, so the shared passcode is the only hosted login.
- Scanned-PDF and photo OCR is not included by default because it adds PyTorch (several GB). Digital PDFs and Word files work. Build with `--build-arg WITH_OCR=1` to include it.

The test suite runs against Postgres as well as SQLite: `TEST_DATABASE_URL=postgresql://user@host/db pytest`.

`Procfile` is included as a fallback for buildpack-based hosts (Railway, Heroku).

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
