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

## Before the first real run

Do these in order. **Month close → Check inputs** shows where you stand.

1. **Customer master.** *Customers → Pull from Zoho* links each account to its existing Zoho customer and fills GSTIN, PAN, state and e-mail. A customer with no state is withheld, because without it the GST type (IGST or CGST+SGST) would be a guess. Customers that do not exist in Zoho yet need their state filled in (import or edit) and are created in Zoho on the first push.
2. **Enterprise IDs.** Export the platform's customer list and use *Billing accounts → Import & mapping → Suggest the enterprise IDs*. Confident matches (GSTIN, or the same name ignoring Pvt/Ltd) are filled in; close matches are listed for you to check. Import the reviewed file in step 2 of that page.
3. **Usage database.** Set `USAGE_DATABASE_URL` (read-only) and adapt the SQL on the Settings page. Charges that read truck or driver counts, card share or spend need the optional *extras* / *spend* queries; the checklist tells you which fields your contracts read.
4. **Zoho.** Set the four `ZOHO_*` credentials (and `ZOHO_ACCOUNTS_BASE` if your Zoho is not on the `.in` data centre), press *Test connection*. Invoices go to Zoho as drafts with GST18/IGST18 (looked up by name; override with `ZOHO_TAX_INTRA` / `ZOHO_TAX_INTER`), place of supply, GSTIN and SAC. Zoho keeps its own invoice numbering and e-invoicing; our number goes in the reference field.
5. **First month.** The previous system's last figure per customer (from the rulebook's `sep26_actuals`) is the comparison, so only real swings are flagged for review.

### Rehearse it without any outside service

`tests/e2e_real_scale.py` runs the whole path through the API on your own files, using a stand-in platform database and a stand-in Zoho that enforces the same GST rules: import, mapping assistant, calculate, review, lock, customer pull, push, a second push (no duplicates) and the next month. It takes about 15 seconds for 800 accounts.

    BILLING_RULES_JSONL=... BILLING_REGISTER_CSV=... python tests/e2e_real_scale.py

## Corrections and missing customers

- **Corrections file** (*Import & mapping → Apply a corrections file*): where a contract and the legacy sheet disagree, a small JSON file says what the contract says, row by row (replace or modify a charge, change the minimum or cap, add or edit a group, attach an assumption to confirm). Each change becomes a draft contract version for Finance to check and lock; locked versions are never edited, and applying the same file twice changes nothing. The format is documented at the top of `backend/app/services/corrections.py`. Keep customer-specific files out of the repo.
- **Add the missing customers**: customers that appear on the periodic register but not in the rulebook get a customer and billing account (name variants such as `PRABHA ENTERPRISES` / `Prabha Enterprises` are merged), their subscription invoices are linked, and a draft contract waits for any usage charges the agreement has.

## Month-end close

**Month close** (under Billing) is the monthly job as one guided flow:

1. **Check inputs:** usage feed connected, usage loaded for the month, every billable account mapped to a platform enterprise, every charge modelled, no unlocked contract drafts, last month locked, Zoho connected.
2. **Calculate:** reads usage straight from the platform database. Accounts that cannot be billed correctly are withheld, never billed ₹0.
3. **Review:** every unusual invoice is queued with the reason it is there: first invoice, part-month (start, stop, demo), minimum or cap applied, a sharp swing on last month, hand-entered usage, group fee, new contract, open findings. A small random sample of clean invoices is queued too. A second Finance person marks each one checked, or leaves it out with a reason.
4. **Lock:** only possible when the queue is empty, and not by the person who calculated the run.
5. **Push to Zoho:** a background job creates the draft invoices (one Zoho line per charge) with progress, per-invoice errors, safe re-runs and no duplicates (matched on invoice reference). Customers are matched in Zoho by GSTIN or name before a new one is created. Set `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` and `ZOHO_ORG_ID`; until then it runs in simulation mode.

## Importing the rulebook and clearing 'needs modelling' charges

The rulebook importer reads each customer's wording into typed charges. Where the wording is unambiguous it is modelled outright (for example "2% or 7K whichever is higher", "₹3,500 up to 99 trucks, ₹100/truck from the 100th", slabs). Where it had to be *interpreted* (a blank platform fee, "per truck/driver", a minimum that starts "from the 5th month") the charge is billed as read and the interpretation is saved as an **assumption** on the contract. Every unconfirmed assumption flags that invoice in the month-end review until Finance presses **Confirm**. Genuinely ambiguous wording stays as "needs modelling" and withholds that invoice.

Already imported your rulebook? Upload it again under **Billing accounts → Import & mapping → Clear 'needs modelling' charges**. It creates corrected *draft* versions only for accounts that still have an unmodelled charge, skips dropped accounts, never touches locked versions and is safe to repeat. Finance reviews, confirms the assumptions and locks each draft.

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
