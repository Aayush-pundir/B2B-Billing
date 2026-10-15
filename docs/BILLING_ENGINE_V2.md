# Billing engine v2

Typed, parameterised charge components — no per-customer code. A contract is a list of components
(`fixed_saas`, `load_percent`, `load_percent_tiered`, `upi_per_txn`, `per_user`, `per_admin`, `module_package`, …) plus
contract-level rules: overall minimum (optionally starting on a date), monthly cap, pro-rata, subscription coverage.

## Concepts
| Concept | Where |
|---|---|
| Billing account ↔ platform enterprises (many-to-many, dated, `include_*` flags) | Billing accounts → Enterprises |
| Versioned contracts — locked versions are immutable; changes create a new draft | Billing accounts → Contract |
| Lifecycle events (dropped, on hold, not live, MG deferred, …) replace free-text remarks | Billing accounts → Lifecycle |
| Periodic subscriptions cover the platform fee for their months | Billing accounts → Subscriptions |
| Escalation rules create **draft** versions for Finance to lock | Billing accounts → Escalation |
| Groups: net-parent, equal-split, pooled, per-entity minimum | Billing accounts → Groups |
| Usage snapshots: frozen per enterprise per month; `—` means "not supplied", never 0 | Billing accounts → Usage inputs |
| Simulator: full calculation trace for any month / what-if usage / draft version | Account → Simulate |
| Exceptions (spec §10): high ones may **block** an invoice; all are tracked and resolvable | Billing accounts → Exceptions |

Only the **Finance** role can import or amend terms. Money is calculated with `Decimal` and rounded half-up per line.
Missing usage is 0 — a fixed fee or minimum is still billed. A charge the importer cannot model becomes
"needs modelling" and **withholds that invoice** rather than billing ₹0.

## Going live
1. Import the rulebook (Import & mapping → 1). Imported contracts can be locked immediately for the parallel run.
2. Fill in the enterprise-ID mapping (template provided) and import it. Unmapped accounts are withheld.
3. Import the periodic register.
4. Make sure the usage queries (Settings → Usage data source) return the columns in the default queries (`maker_users`,
   `admin_users`, `cnt_le_2500`, `amt_gt_2500`; optional `extras` query for spend/fleet inputs).
5. Parallel-run: run a month, compare with the sheet, resolve or waive each exception.

## Regression against your legacy sheet
```
BILLING_RULES_JSONL=/secure/customer_rules_sep26.jsonl BILLING_REGISTER_CSV=/secure/periodic_register.csv \
  python backend/tests/regression_real_data.py [--verbose] [--rows=72,264]
```
The data stays outside the repo (gitignored). `pytest backend/tests` runs the spec's golden cases (anonymised).
