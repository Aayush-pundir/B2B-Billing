"""
Offline commercial-terms extraction. Zero external API cost — everything
runs locally.

File type -> tool:
  .docx           python-docx   -> reads the LAST table only (commercial
                                    pricing table is always last in these
                                    agreements). Deduplicates merged cells.
  .pdf (digital)  pdfplumber    -> last 2 pages, last table per page, falls
                                    back to raw text extraction.
  .pdf (scanned)  PyMuPDF+easyocr -> rasterises each page at 200dpi, OCRs it.
  .png/.jpg       easyocr       -> direct image OCR (covers phone photos too).

All of this lands as a DRAFT commercial. A human always reviews before
anything is billed — nothing here is ever auto-approved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Text extraction per file type (heavy libraries imported lazily so the app
# starts fast and works even before the OCR extras are installed).
# ---------------------------------------------------------------------------


def extract_text_from_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    if not doc.tables:
        return "\n".join(p.text for p in doc.paragraphs)

    table = doc.tables[-1]  # commercial pricing table is always last
    seen_rows = []
    last_row_text = None
    for row in table.rows:
        cells = [c.text.strip() for c in row.cells]
        # dedupe merged cells that repeat the same text across columns
        deduped = []
        prev = None
        for c in cells:
            if c != prev:
                deduped.append(c)
            prev = c
        row_text = " | ".join(deduped)
        if row_text != last_row_text:
            seen_rows.append(row_text)
        last_row_text = row_text
    return "\n".join(seen_rows)


def extract_text_from_pdf_digital(path: str) -> str:
    import pdfplumber

    chunks = []
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[-2:] if len(pdf.pages) >= 2 else pdf.pages
        for page in pages:
            table = page.extract_table()
            if table:
                for row in table:
                    chunks.append(" | ".join(c or "" for c in row))
            else:
                text = page.extract_text() or ""
                chunks.append(text)
    return "\n".join(chunks)


def is_pdf_scanned(path: str) -> bool:
    """Heuristic: if pdfplumber finds no extractable text, it's a scan."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if (page.extract_text() or "").strip():
                return False
    return True


def extract_text_from_scanned_pdf(path: str) -> str:
    import fitz  # PyMuPDF
    import easyocr

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    doc = fitz.open(path)
    chunks = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        result = reader.readtext(img_bytes, detail=0, paragraph=True)
        chunks.append("\n".join(result))
    return "\n".join(chunks)


def extract_text_from_image(path: str) -> str:
    import easyocr

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    result = reader.readtext(path, detail=0, paragraph=True)
    return "\n".join(result)


def extract_raw_text(path: str, file_type: str) -> str:
    file_type = file_type.lower().lstrip(".")
    if file_type == "docx":
        return extract_text_from_docx(path)
    if file_type == "pdf":
        if is_pdf_scanned(path):
            return extract_text_from_scanned_pdf(path)
        return extract_text_from_pdf_digital(path)
    if file_type in ("png", "jpg", "jpeg"):
        return extract_text_from_image(path)
    raise ValueError(f"Unsupported file type: {file_type}")


# ---------------------------------------------------------------------------
# Rule parser — regex patterns for the known commercial formats.
# OCR output is normalised first (line-break-joined floors, curly quotes,
# stray whitespace) so patterns match text with mid-token line breaks.
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    # join a lone trailing token onto the next line — handles OCR splits like
    # "or 12k\n(whichever...)" so % / floor patterns aren't broken mid-clause
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _to_amount(num_str: str) -> float:
    """Parse '5000', '5k', '1.5 lakh', '10,000' etc into a plain float."""
    s = num_str.strip().lower().replace(",", "")
    m = re.match(r"([\d.]+)\s*(k|l|lakh|lac)?", s)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "k":
        value *= 1_000
    elif unit in ("l", "lakh", "lac"):
        value *= 100_000
    return value


@dataclass
class ExtractedRule:
    rule: dict[str, Any]
    confidence: int
    source: str


PATTERNS = []
CURRENCY = r"(?:₹|rs\.?|inr)?"


def _pattern(name):
    def deco(fn):
        PATTERNS.append((name, fn))
        return fn
    return deco


@_pattern("percent_or_floor")
def _match_percent_or_floor(text: str) -> list[ExtractedRule]:
    # "1.5% of loading or 5k whichever is higher" / "of the value added"
    rx = re.compile(
        r"([\d.]+)\s*%\s*of\s*(?:the\s*)?(?:loading|value\s*added|load\s*value)"
        r"\s*(?:or|,)\s*(?:₹\s*)?([\d.,]+\s*(?:k|l|lakh|lac)?)"
        r"(?:\s*whichever\s*is\s*higher)?",
        re.IGNORECASE,
    )
    out = []
    for m in rx.finditer(text):
        rate_pct = float(m.group(1))
        floor_amount = _to_amount(m.group(2))
        out.append(ExtractedRule(
            rule={
                "label": "Loading fee",
                "metric": "loading",
                "rate_type": "percent",
                "slab_mode": "cliff",
                "rate_pct": rate_pct,
                "floor_amount": floor_amount,
            },
            confidence=90,
            source=m.group(0),
        ))
    return out


@_pattern("flat_monthly")
def _match_flat_monthly(text: str) -> list[ExtractedRule]:
    rx = re.compile(
        r"(?:services?\s*fees?\s*per\s*month|fixed\s*per\s*month)\s*(?:of|:|-)?\s*"
        + CURRENCY + r"\s*([\d.,]+\s*(?:k|l|lakh|lac)?)",
        re.IGNORECASE,
    )
    out = []
    for m in rx.finditer(text):
        out.append(ExtractedRule(
            rule={
                "label": "Flat monthly fee",
                "metric": "loading",
                "rate_type": "flat",
                "flat_amount": _to_amount(m.group(1)),
            },
            confidence=85,
            source=m.group(0),
        ))
    return out


@_pattern("per_unit_monthly")
def _match_per_unit(text: str) -> list[ExtractedRule]:
    rx = re.compile(
        r"(?:₹\s*)?([\d.,]+)\s*per\s*(truck|driver|user)s?\s*per\s*month",
        re.IGNORECASE,
    )
    out = []
    for m in rx.finditer(text):
        unit = m.group(2).lower()
        metric = {"truck": "trucks", "driver": "trucks", "user": "spender_users"}[unit]
        out.append(ExtractedRule(
            rule={
                "label": f"Per {unit} fee",
                "metric": metric,
                "rate_type": "per_unit",
                "unit_rate": _to_amount(m.group(1)),
                "minimum_qty": 0,
            },
            confidence=80,
            source=m.group(0),
        ))
    return out


@_pattern("hybrid_fixed_then_per_unit")
def _match_hybrid(text: str) -> list[ExtractedRule]:
    rx = re.compile(
        CURRENCY + r"\s*([\d.,]+)\s*fixed\s*upto\s*([\d.,]+)\s*trucks?,?\s*then\s*"
        + CURRENCY + r"\s*([\d.,]+)\s*/?\s*truck\s*above\s*(?:[\d.,]+)?",
        re.IGNORECASE,
    )
    out = []
    for m in rx.finditer(text):
        out.append(ExtractedRule(
            rule={
                "label": "Hybrid truck fee",
                "metric": "trucks",
                "rate_type": "hybrid",
                "hybrid_fixed_amount": _to_amount(m.group(1)),
                "hybrid_included_qty": _to_amount(m.group(2)),
                "hybrid_over_rate": _to_amount(m.group(3)),
            },
            confidence=75,
            source=m.group(0),
        ))
    return out


@_pattern("annual_per_user")
def _match_annual_per_user(text: str) -> list[ExtractedRule]:
    rx = re.compile(
        r"(?:₹\s*)?([\d.,]+)\s*/?-?\s*per\s*annum\s*per\s*user",
        re.IGNORECASE,
    )
    out = []
    for m in rx.finditer(text):
        out.append(ExtractedRule(
            rule={
                "label": "Annual per-user fee",
                "metric": "total_users",
                "rate_type": "annual_per_unit",
                "unit_rate": _to_amount(m.group(1)),
                "invoice_as": "annual_lump",
            },
            confidence=80,
            source=m.group(0),
        ))
    return out


@_pattern("per_user_per_month")
def _match_per_user_month(text: str) -> list[ExtractedRule]:
    rx = re.compile(r"(?:₹\s*)?([\d.,]+)\s*/\s*user\s*/\s*month", re.IGNORECASE)
    out = []
    for m in rx.finditer(text):
        out.append(ExtractedRule(
            rule={
                "label": "Per spender-user fee",
                "metric": "spender_users",
                "rate_type": "per_unit",
                "unit_rate": _to_amount(m.group(1)),
            },
            confidence=80,
            source=m.group(0),
        ))
    return out


@_pattern("tiered_slab")
def _match_tiered_slab(text: str) -> list[ExtractedRule]:
    # "7-10L" / "7 lakh -10 lakh" style band -> rate% / floor
    band_rx = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:l|lakh|lac)?\s*-\s*(\d+(?:\.\d+)?)\s*(?:l|lakh|lac)"
        r".{0,40}?([\d.]+)\s*%.{0,40}?" + CURRENCY + r"\s*([\d.,]+\s*k?)",
        re.IGNORECASE,
    )
    plus_rx = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:l|lakh|lac)\s*\+.{0,40}?([\d.]+)\s*%.{0,40}?" + CURRENCY + r"\s*([\d.,]+\s*k?)",
        re.IGNORECASE,
    )
    if "slab" not in text.lower():
        return []
    tiers = []
    for m in band_rx.finditer(text):
        lo = _to_amount(m.group(1) + "l")
        hi = _to_amount(m.group(2) + "l")
        tiers.append({"min": lo, "max": hi, "rate_pct": float(m.group(3)), "floor": _to_amount(m.group(4))})
    for m in plus_rx.finditer(text):
        lo = _to_amount(m.group(1) + "l")
        tiers.append({"min": lo, "max": None, "rate_pct": float(m.group(2)), "floor": _to_amount(m.group(3))})
    if not tiers:
        return []
    return [ExtractedRule(
        rule={
            "label": "Tiered loading fee",
            "metric": "loading",
            "rate_type": "percent",
            "slab_mode": "tiered",
            "tiers": tiers,
        },
        confidence=70,
        source="slab-wise table",
    )]


ESCALATION_RX = re.compile(r"increased?\s*by\s*([\d.]+)\s*%\s*every\s*year", re.IGNORECASE)


def extract_escalation(text: str) -> float | None:
    m = ESCALATION_RX.search(text)
    return float(m.group(1)) if m else None


def parse_rules_from_text(raw_text: str) -> dict[str, Any]:
    text = _normalise(raw_text)
    rules: list[dict] = []
    confidences: list[dict] = []
    for name, matcher in PATTERNS:
        for extracted in matcher(text):
            rules.append(extracted.rule)
            confidences.append({"rule": extracted.rule.get("label"), "pattern": name, "confidence": extracted.confidence})

    escalation = extract_escalation(text)
    return {
        "rules": rules,
        "field_confidence": confidences,
        "overall_confidence": (sum(c["confidence"] for c in confidences) // len(confidences)) if confidences else 0,
        "escalation_pct_annual": escalation,
        "raw_text": raw_text,
    }


def extract_commercial_terms(path: str, file_type: str) -> dict[str, Any]:
    raw_text = extract_raw_text(path, file_type)
    result = parse_rules_from_text(raw_text)
    result["file_type"] = file_type
    return result
