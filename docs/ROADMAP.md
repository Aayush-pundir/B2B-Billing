# BillingOS roadmap — beyond Zoho Books

Goal: everything Zoho Books does for a B2B billing team, plus the automation Zoho can't do because it
doesn't own your usage data. Ordered by value to this business, not by Zoho's menu.

## Done
- **Terms, finance-only.** Only the Finance role can create, edit, lock or unlock billing terms, via manual entry,
  import from a reviewed agreement, or bulk Excel. Every change records source and who/when.
- **Usage from the database** (replaces monthly CSVs; CSV kept as fallback). Read-only, SELECT-only, queries editable by admin.
- **Customer master data.** Identity, tax (GST treatment, PAN, TAN, CIN, MSME), address, contacts, CC emails,
  currency, per-customer payment terms, credit limit, account manager, status. Bulk import/update/export (Excel/CSV).
- Already present: slab billing engine, GST/TDS, invoice PDF + email, bank reconciliation, aging, reminders, credit notes, statements, CRM sync, audit log.

## Phase 2 — close the order-to-cash loop (DONE)
1. **Approval workflow** on billing runs: maker → Finance checker (maker can't approve own run unless allowed), auto-approve for clean runs under a limit, mandatory rejection reason, high-severity findings must be acknowledged. Invoices can't be issued or pushed to Zoho until approved.
2. **Scheduled runs:** auto-creates last month's run from the usage DB, auto-issues + emails on approval, dunning ladder (default T-3, T+1, T+7, T+15) with per-customer opt-out; paused for disputes and customer promises.
3. **Usage anomaly checks** on every run: month-on-month swing, zero usage on a previously billed customer, usage with no locked terms, unknown enterprise IDs, credit-limit breach.
4. **Customer portal / pay links:** public per-invoice link — invoice, PDF, bank + UPI details with QR, promise-to-pay date, raise a query.
5. **Credit control:** exposure vs limit, auto-block/unblock on overdue days, collections forecast from each customer's real payment delay.

## Phase 3 — Zoho Books parity (first slice DONE: items, quotes, recurring/ad-hoc invoices, debit notes, receipts/advances, write-offs, vendors & bills, chart of accounts, derived double-entry ledger, journals, period lock, trial balance / P&L / balance sheet / aging / GST + GSTR-1 export, integrity checks)
- **Sales:** quotes/estimates, sales orders, recurring invoices, proforma, delivery challans, debit notes, advance receipts & retainers, customer credits applied to invoices, write-offs.
- **Purchases (AP):** vendors, bills, vendor credits, purchase orders, payments made, expense claims, recurring bills.
- **Accounting:** chart of accounts, manual journals, opening balances, multi-currency with exchange-rate gains/losses, bank feeds + rules, period locking, fixed assets.
- **India compliance:** GSTR-1 / GSTR-3B data, e-invoicing (IRN + QR), e-way bill, TDS/TCS tracking + Form 26AS reconciliation, HSN/SAC master, reverse charge.
- **Reports:** P&L, balance sheet, cash flow, trial balance, aged AR/AP, GST summary, sales by customer/product, budget vs actual.
- **Inventory/items:** item catalogue, price lists, taxes, units.
- **Platform:** multi-organisation, granular roles/permissions, custom fields, templates (invoice/email), webhooks + public API, document attachments, import/export for every module.

## Phase 4 — beyond Zoho (our edge, because we own usage + terms)
- **Revenue intelligence:** MRR/ARR by customer, usage trend, churn-risk (falling usage), expansion signals, slab-threshold alerts ("customer is ₹40k from the next tier").
- **What-if pricing:** simulate a rate/slab change against last N months of real usage before amending terms.
- **Contract lifecycle:** renewal calendar, auto-escalation on anniversary (already modelled), expiry alerts, amendment history diff.
- **Leakage detection:** usage billed vs usage consumed, customers with usage but no locked terms, terms never invoiced.
- **Cash application AI:** learn from past matches to raise auto-match confidence; split/partial/advance handling.
- **Accounting sync:** two-way Zoho Books / Tally push for GL, so finance keeps its books of record if desired.


## Billing engine v2 follow-ups (DONE)
- Zoho Books push: one line item per charge, idempotent by invoice reference, auto-creates the contact from the Zoho billing name, flags no-auto-debit accounts.
- Subscription renewals: forecast (overdue/due, expected amount from escalation rules), one-click raise that records the subscription and issues the invoice into the books and GST; scheduler step creates escalation drafts.
- Revenue insights: trend, mix by charge type, concentration (top-10, HHI), movers, NRR/churn, take rate, minimum-top-up analysis, declining customers, DSO/on-time, cohorts, forecast, data health.
- Hardening: database indexes, ALLOW_DEV_LOGIN switch, 37-test automated suite (`pytest backend/tests`).
