"""CRM sync — one-way pull. The CRM stays the system of record for who a
client *is* (name, GSTIN, state, contact); this platform stays the system
of record for what they're *billed* (commercials, invoices, receivables).
Every sync overwrites only identity fields and never touches billing data.

Built as an adapter interface so a live API-based adapter (Zoho CRM, HubSpot,
Salesforce) can be dropped in later without changing the sync logic below —
today only the generic CSV-export adapter is implemented, since no specific
CRM has been named yet. Point it at whatever your CRM lets you export.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass


@dataclass
class ClientRecord:
    crm_id: str
    enterprise_id: str
    name: str
    gst: str = ""
    pan: str = ""
    state: str = ""
    state_code: str = ""
    billing_contact_email: str = ""


class CrmAdapter:
    source_name = "generic_csv"

    def fetch_clients(self, raw: bytes) -> list[ClientRecord]:
        raise NotImplementedError


class GenericCsvAdapter(CrmAdapter):
    """Works with any CRM that can export accounts to CSV — Zoho CRM,
    HubSpot, Salesforce, or a plain spreadsheet. Expected columns (case
    insensitive, common aliases accepted):
      crm_id / account_id / id
      enterprise_id
      name / account_name / company
      gst / gstin
      pan
      state
      state_code
      billing_contact_email / email
    """
    source_name = "generic_csv"

    ALIASES = {
        "crm_id": ["crm_id", "account_id", "id", "record_id"],
        "enterprise_id": ["enterprise_id", "enterpriseid"],
        "name": ["name", "account_name", "company", "company_name"],
        "gst": ["gst", "gstin"],
        "pan": ["pan"],
        "state": ["state"],
        "state_code": ["state_code", "statecode"],
        "billing_contact_email": ["billing_contact_email", "email", "billing_email"],
    }

    def _col(self, fieldnames: list[str], canonical: str) -> str | None:
        lowered = {f.strip().lower(): f for f in fieldnames}
        for alias in self.ALIASES[canonical]:
            if alias in lowered:
                return lowered[alias]
        return None

    def fetch_clients(self, raw: bytes) -> list[ClientRecord]:
        text = raw.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return []
        cols = {k: self._col(reader.fieldnames, k) for k in self.ALIASES}

        records = []
        for row in reader:
            eid = (row.get(cols["enterprise_id"]) or "").strip() if cols["enterprise_id"] else ""
            name = (row.get(cols["name"]) or "").strip() if cols["name"] else ""
            if not eid or not name:
                continue
            crm_id = (row.get(cols["crm_id"]) or eid).strip() if cols["crm_id"] else eid
            records.append(ClientRecord(
                crm_id=crm_id, enterprise_id=eid, name=name,
                gst=(row.get(cols["gst"]) or "").strip() if cols["gst"] else "",
                pan=(row.get(cols["pan"]) or "").strip() if cols["pan"] else "",
                state=(row.get(cols["state"]) or "").strip() if cols["state"] else "",
                state_code=(row.get(cols["state_code"]) or "").strip() if cols["state_code"] else "",
                billing_contact_email=(row.get(cols["billing_contact_email"]) or "").strip() if cols["billing_contact_email"] else "",
            ))
        return records


ADAPTERS: dict[str, CrmAdapter] = {
    "generic_csv": GenericCsvAdapter(),
}
