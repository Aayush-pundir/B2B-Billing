"""GST state codes (the first two digits of a GSTIN) and the short codes Zoho Books uses for place of supply.
A few short codes differ between sources (Odisha, Chhattisgarh, Uttarakhand, Telangana...), so the table below is only
a default: customers pulled from Zoho teach the app Zoho's own spelling (see services/zoho_sync.py)."""
from __future__ import annotations

GST_TO_SHORT = {
    "01": "JK", "02": "HP", "03": "PB", "04": "CH", "05": "UT", "06": "HR", "07": "DL", "08": "RJ", "09": "UP", "10": "BR",
    "11": "SK", "12": "AR", "13": "NL", "14": "MN", "15": "MZ", "16": "TR", "17": "ML", "18": "AS", "19": "WB", "20": "JH",
    "21": "OD", "22": "CG", "23": "MP", "24": "GJ", "26": "DN", "27": "MH", "28": "AP", "29": "KA", "30": "GA", "31": "LD",
    "32": "KL", "33": "TN", "34": "PY", "35": "AN", "36": "TS", "37": "AP", "38": "LA",
}
NAMES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}


def state_code_from_gstin(gstin: str) -> str:
    g = (gstin or "").strip()
    return g[:2] if len(g) >= 2 and g[:2].isdigit() and g[:2] in GST_TO_SHORT else ""


def short_code(gst_state_code: str, learned: dict[str, str] | None = None) -> str:
    """Zoho's short code for a GST state code ('09' -> 'UP'), preferring what Zoho itself has told us."""
    code = (gst_state_code or "").strip().zfill(2) if (gst_state_code or "").strip().isdigit() else (gst_state_code or "").strip()
    if learned and code in learned:
        return learned[code]
    return GST_TO_SHORT.get(code, "")


def gst_code_from_short(short: str, learned: dict[str, str] | None = None) -> str:
    s = (short or "").strip().upper()
    for code, v in (learned or {}).items():
        if v.upper() == s:
            return code
    return next((c for c, v in GST_TO_SHORT.items() if v == s), "")
