"""Hardcoded CSV parsers for the three monthly billing files. Schemas are
locked to the real client export formats — no column-mapping step needed.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict


def _rows(raw: bytes) -> csv.DictReader:
    text = raw.decode("utf-8-sig", errors="replace")
    return csv.DictReader(io.StringIO(text))


def parse_loading_csv(raw: bytes) -> dict[str, float]:
    """Filter: TransactionCategory == 'Add Money' AND Status == 'COMPLETED'.
    Billing metric: sum(TotalTransactionAmount) per EnterpriseId."""
    totals: dict[str, float] = defaultdict(float)
    for row in _rows(raw):
        if (row.get("TransactionCategory") or "").strip() != "Add Money":
            continue
        if (row.get("Status") or "").strip().upper() != "COMPLETED":
            continue
        eid = (row.get("EnterpriseId") or "").strip()
        if not eid:
            continue
        try:
            amount = float(row.get("TotalTransactionAmount") or 0)
        except ValueError:
            amount = 0.0
        totals[eid] += amount
    return dict(totals)


UPI_BILLABLE_CATEGORIES = {"QR Payment", "Upi Pay", "Authorize", "Collect"}


def parse_upi_csv(raw: bytes) -> dict[str, dict[str, float]]:
    """Filter: TransactionType == 'DEBIT' AND Status == 'COMPLETED' AND
    TransactionCategory in {QR Payment, Upi Pay, Authorize, Collect}.
    Billing metric: COUNT of rows per EnterpriseId. Amount kept for reference only."""
    result: dict[str, dict[str, float]] = defaultdict(lambda: {"upi_transactions": 0, "upi_amount": 0.0})
    for row in _rows(raw):
        if (row.get("TransactionType") or "").strip().upper() != "DEBIT":
            continue
        if (row.get("Status") or "").strip().upper() != "COMPLETED":
            continue
        category = (row.get("TransactionCategory") or "").strip()
        if category not in UPI_BILLABLE_CATEGORIES:
            continue
        eid = (row.get("EnterpriseId") or "").strip()
        if not eid:
            continue
        try:
            amount = float(row.get("TotalTrxnAmount") or 0)
        except ValueError:
            amount = 0.0
        result[eid]["upi_transactions"] += 1
        result[eid]["upi_amount"] += amount
    return {k: dict(v) for k, v in result.items()}


def parse_users_csv(raw: bytes) -> dict[str, dict[str, float]]:
    """One row per EnterpriseId. admin_users = Admin + CO_ADMIN.
    spender_users = CHECKER + APPROVER + MAKER. total_users = TotalUsers column."""
    result: dict[str, dict[str, float]] = {}

    def _num(row, key):
        try:
            return float(row.get(key) or 0)
        except ValueError:
            return 0.0

    for row in _rows(raw):
        eid = (row.get("EnterpriseId") or "").strip()
        if not eid:
            continue
        admin_users = _num(row, "Admin") + _num(row, "CO_ADMIN")
        spender_users = _num(row, "CHECKER") + _num(row, "APPROVER") + _num(row, "MAKER")
        total_users = _num(row, "TotalUsers")
        result[eid] = {
            "admin_users": admin_users,
            "spender_users": spender_users,
            "total_users": total_users,
        }
    return result


def aggregate_usage(
    loading: dict[str, float],
    upi: dict[str, dict[str, float]],
    users: dict[str, dict[str, float]],
    known_enterprise_ids: set[str],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Combine the three parsed CSVs into one usage object per EnterpriseId.
    Returns (usage_by_client, unknown_enterprise_ids) — ids present in the
    CSVs but not registered as clients."""
    all_ids = set(loading) | set(upi) | set(users)
    usage: dict[str, dict[str, float]] = {}
    for eid in all_ids:
        u = {
            "loading": loading.get(eid, 0.0),
            "upi_transactions": upi.get(eid, {}).get("upi_transactions", 0),
            "upi_amount": upi.get(eid, {}).get("upi_amount", 0.0),
            "admin_users": users.get(eid, {}).get("admin_users", 0),
            "spender_users": users.get(eid, {}).get("spender_users", 0),
            "total_users": users.get(eid, {}).get("total_users", 0),
        }
        usage[eid] = u
    unknown = sorted(eid for eid in all_ids if eid not in known_enterprise_ids)
    return usage, unknown
