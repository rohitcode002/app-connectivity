"""
jcc_handler/jcc_postprocess.py — Post-processing for JCC extracted data
========================================================================
JCC extraction rules:
- The PDF/LLM stage extracts only the five raw JCC table columns.
- This postprocess stage is the only place that derives total_COD,
  COD_Found, effective_date, TGNA, and GNA.
- total_COD is the sum of MW values under the Generation section only.
  A MW value counts only when the same entry includes a date and an
  explicit COD/CoD/Commissioned/DOCO marker.
- If COD_Found is false, TGNA and GNA stay blank.
- If COD_Found is true and effective_date is today or earlier, total_COD
  goes to GNA; if effective_date is in the future, total_COD goes to TGNA.

Three responsibilities:

1. **COD Detection (deterministic)**
   Multiple regex patterns cover the different COD formats found across
   JCC PDFs. The LLM is not used here; if no qualifying Generation entry
   is found, COD_Found is false.
2. **Row Continuation (page-spanning rows)**
   When a table row spans two PDF pages, the continuation row on the next
   page will have an empty ``pooling_station``. This module detects those
   continuation rows and merges them into the preceding page's last row.

3. **Effective Date Extraction (improved)**
   Extracts the connectivity effective date from the
   ``connectivity_start_date_under_gna`` column using multiple regex
   strategies, prioritising the last date line in the text.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Date pattern shared across all helpers
# ─────────────────────────────────────────────────────────────────────────────
_DATE_PATTERN = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. ROBUST COD DETECTION
# ─────────────────────────────────────────────────────────────────────────────

# Pattern A: "<MW> MW ... <date> ... (COD/CoD/Commissioned/DOCO)"
#   e.g.  "238 MW: 02.01.2025 (CoD)"
#         "300 MW: 08.04.2027"
_COD_PATTERN_A = re.compile(
    rf"([\d,]+(?:\.\d+)?)\s*MW\s*[:\-]?\s*"
    rf"(?:{_DATE_PATTERN})"
    rf"[^()\n\r]{{0,80}}?"
    rf"\(\s*(?:CoD|COD|Commissioned|DOCO)\s*\)",
    re.IGNORECASE,
)

# Pattern B: "<MW>- <date> (COD)"  (no MW keyword, dash-separated)
#   e.g.  "57.22- 29.01.22 (COD)"
#         "102.35-22.07.22 (COD)"
_COD_PATTERN_B = re.compile(
    rf"([\d,]+(?:\.\d+)?)\s*-\s*"
    rf"(?:{_DATE_PATTERN})"
    rf"[^()\n\r]{{0,80}}?"
    rf"\(\s*(?:CoD|COD|Commissioned|DOCO)\s*\)",
    re.IGNORECASE,
)

# Pattern D: "<MW> MW: <date> (CoD)" — colon-separated
#   e.g.  "238 MW: 02.01.2025 (CoD)"
_COD_PATTERN_D = re.compile(
    rf"([\d,]+(?:\.\d+)?)\s*MW\s*:\s*"
    rf"({_DATE_PATTERN})\s*"
    rf"\(\s*(?:CoD|COD|Commissioned|DOCO)\s*\)",
    re.IGNORECASE,
)

# All patterns in order of specificity. Every pattern requires a date and an
# explicit COD/Commissioned marker.
_COD_PATTERNS = [_COD_PATTERN_D, _COD_PATTERN_A, _COD_PATTERN_B]


def _generation_block(text: str) -> str:
    """Return only the Generation section from a JCC schedule cell.

    Stops at 'Dedicated system', 'DTL:', or 'Generation Pooling Station'.
    If no Generation header is found, returns empty text because COD must
    be calculated only from the Generation section.
    """
    if not text:
        return ""
    match = re.search(
        r"(?:\bre\s+generation\b|\bgeneration(?:\s*\(\s*mw\s*\))?)\s*:?(.*?)"
        r"(?:\bdedicated\s+system\b|\bdtl\s*:|\bgeneration\s+pooling\s+station\b|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _extract_cod_mw_regex(text: str) -> list[float]:
    """Extract all COD/Commissioned MW values using multiple regex patterns.

    Scans the Generation block of the schedule text with every COD pattern,
    collecting MW values found by any pattern.  Dedup is by full match text
    (not by MW value alone) so identical MW values at different dates are
    counted separately.
    """
    gen_text = _generation_block(text)
    if not gen_text:
        return []

    values: list[float] = []
    seen_match: set[str] = set()

    for pattern in _COD_PATTERNS:
        for match in pattern.finditer(gen_text):
            match_key = match.group(0).strip()
            if match_key in seen_match:
                continue
            seen_match.add(match_key)
            mw_raw = match.group(1)
            try:
                values.append(float(mw_raw.replace(",", "")))
            except ValueError:
                continue

    return values


def calculate_total_cod(
    schedule_text: str,
    runtime=None,
) -> tuple[float | None, bool]:
    """Calculate total COD from schedule text using deterministic parsing only.

    Returns (total_mw, cod_found).
    """
    values = _extract_cod_mw_regex(schedule_text)
    if not values:
        return None, False
    return round(sum(values), 2), True


# ─────────────────────────────────────────────────────────────────────────────
# 2. EFFECTIVE DATE EXTRACTION (improved)
# ─────────────────────────────────────────────────────────────────────────────

# Strategy: try multiple regex patterns in priority order, then fall back to
# extracting the last date-like string in the text.

# P1: "effective w.e.f. <date>"  (strongest signal)
_EFF_PATTERN_WEF = re.compile(
    rf"\beffective\b\s*w\.?e\.?f\.?\s*[^0-9]{{0,30}}(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)

# P2: "Connectivity effective w.e.f. <date>"
_EFF_PATTERN_CONN_WEF = re.compile(
    rf"\bconnectivity\s+effective\b\s*w\.?e\.?f\.?\s*[^0-9]{{0,30}}(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)

# P3: "Effectiveness date" / "effective date" followed by date
_EFF_PATTERN_DATE_LABEL = re.compile(
    rf"(?:effectiveness|effective)\s+date\s*[:\-]?\s*(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)

# P4: "<date> (final)" — common in the data
_EFF_PATTERN_FINAL = re.compile(
    rf"(?P<date>{_DATE_PATTERN})\s*\(\s*(?:final|Final)\s*\.?\s*\)",
    re.IGNORECASE,
)

# P5: "Start date of Connectivity under GNA: <date>"
_EFF_PATTERN_START_DATE = re.compile(
    rf"start\s+date\s+of\s+connectivity\s+under\s+GNA\s*[:\-]?\s*(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)

# P6: Generic "effective" ... <date> anywhere within 80 chars
_EFF_PATTERN_GENERIC = re.compile(
    rf"\beffective\b[^0-9]{{0,80}}(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)


def extract_effective_date(text: str) -> str:
    """Extract the connectivity effective date from the GNA/effectiveness column.

    Tries specific patterns first, then falls back to the last date in the text.
    """
    if not text:
        return ""

    # Priority 1: "Connectivity effective w.e.f. <date>"
    m = _EFF_PATTERN_CONN_WEF.search(text)
    if m:
        return m.group("date")

    # Priority 2: "effective w.e.f. <date>"
    m = _EFF_PATTERN_WEF.search(text)
    if m:
        return m.group("date")

    # Priority 3: "Effectiveness date: <date>"
    m = _EFF_PATTERN_DATE_LABEL.search(text)
    if m:
        return m.group("date")

    # Priority 4: "<date> (final)"
    m = _EFF_PATTERN_FINAL.search(text)
    if m:
        return m.group("date")

    # Priority 5: "Start date of Connectivity under GNA: <date>"
    m = _EFF_PATTERN_START_DATE.search(text)
    if m:
        return m.group("date")

    # Priority 6: Generic "effective" near a date
    m = _EFF_PATTERN_GENERIC.search(text)
    if m:
        return m.group("date")

    # Final fallback: return the LAST date found in the text (the effective date
    # is typically in the last line of this column)
    all_dates = re.findall(_DATE_PATTERN, text)
    if all_dates:
        return all_dates[-1]

    return ""


def _parse_date(value: str) -> date | None:
    """Parse a date string in dd.mm.yyyy or dd.mm.yy format."""
    if not value:
        return None
    normalized = value.replace("/", ".").replace("-", ".")
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. ROW CONTINUATION LOGIC (page-spanning rows)
# ─────────────────────────────────────────────────────────────────────────────

def _is_continuation_row(row: dict) -> bool:
    """Return True if this row appears to be a continuation of a previous row.

    A continuation row lacks a pooling_station (and often lacks a connectivity
    applicant), meaning it was extracted from a page where the table row started
    on the previous page.
    """
    pooling = (row.get("pooling_station") or "").strip()
    applicant = (row.get("connectivity_applicant") or "").strip()

    # If both pooling_station and applicant are empty, it's very likely a continuation
    if not pooling and not applicant:
        return True

    # If only pooling_station is empty, check if applicant looks like a continuation
    # (i.e. it's just leftover text, not a real new applicant entry)
    if not pooling:
        return True

    return False


def _merge_row_text(base_val: str, continuation_val: str) -> str:
    """Merge two cell values, appending continuation text with a newline."""
    base = (base_val or "").strip()
    cont = (continuation_val or "").strip()
    if not cont:
        return base
    if not base:
        return cont
    return f"{base}\n{cont}"


def _normalize_bay_value(value: str) -> list[str]:
    if not value:
        return []
    return re.findall(r"[A-Za-z]*\d+[A-Za-z]*", value)


def _extract_bay_no(text: str) -> str:
    """Extract bay number(s) from row text.

    Rules:
    - Prefer the value after "Main Bay" when present.
    - Otherwise, capture bay numbers after "Bay", "Bay No", etc.
    - If multiple bay numbers are listed, return them in order separated by semicolons.
    """
    if not text:
        return ""

    main_match = re.search(
        r"\bmain\s*bay\b[^A-Za-z0-9]*"
        r"(?P<val>[A-Za-z]*\d+[A-Za-z]*(?:\s*[,/]+\s*[A-Za-z]*\d+[A-Za-z]*)*)",
        text,
        flags=re.IGNORECASE,
    )
    if main_match:
        values = _normalize_bay_value(main_match.group("val"))
        if values:
            return "; ".join(values)

    bay_matches = re.findall(
        r"\bbay(?:\s*no\.?|\s*no)?\s*[:\-]?\s*"
        r"([A-Za-z]*\d+[A-Za-z]*(?:\s*[,/]+\s*[A-Za-z]*\d+[A-Za-z]*)*)",
        text,
        flags=re.IGNORECASE,
    )
    for match in bay_matches:
        values = _normalize_bay_value(match)
        if values:
            return "; ".join(values)

    return ""


def merge_continuation_rows(all_pages: list[dict]) -> list[dict]:
    """Merge continuation rows into their parent rows across pages.

    When a table row spans two pages, the continuation on the second page
    produces a row with an empty ``pooling_station``. This function detects
    those rows and merges their text into the last row of the previous page.

    Operates on the per-page result list produced by ``extract_jcc_pdf``.
    Modifies pages in-place and returns the same list.
    """
    # Build a flat list of (page_index, row_index, row_dict) for easy traversal
    all_rows: list[tuple[int, int, dict]] = []
    for pi, page in enumerate(all_pages):
        for ri, row in enumerate(page.get("rows", [])):
            all_rows.append((pi, ri, row))

    if not all_rows:
        return all_pages

    merge_columns = [
        "pooling_station",
        "connectivity_applicant",
        "connectivity_quantum_mw",
        "schedule_as_per_current_jcc",
        "connectivity_start_date_under_gna",
        "bayno",
    ]

    rows_to_remove: list[tuple[int, int]] = []  # (page_index, row_index) to remove

    for i in range(1, len(all_rows)):
        pi, ri, row = all_rows[i]

        if not _is_continuation_row(row):
            continue

        # Find the previous non-continuation row
        prev_pi, prev_ri, prev_row = all_rows[i - 1]

        # Merge text from continuation into the previous row
        for col in merge_columns:
            prev_row[col] = _merge_row_text(
                prev_row.get(col, ""),
                row.get(col, ""),
            )

        # Mark continuation row for removal
        rows_to_remove.append((pi, ri))
        logger.info(
            "[JCC Postprocess] Merged continuation row (page %d, row %d) → previous row (page %d, row %d)",
            all_pages[pi].get("page_number", pi),
            ri,
            all_pages[prev_pi].get("page_number", prev_pi),
            prev_ri,
        )

    # Remove continuation rows (reverse order to preserve indices)
    for pi, ri in reversed(rows_to_remove):
        page_rows = all_pages[pi].get("rows", [])
        if ri < len(page_rows):
            page_rows.pop(ri)

    return all_pages


# ─────────────────────────────────────────────────────────────────────────────
# MASTER POSTPROCESS: recompute all derived fields on every row
# ─────────────────────────────────────────────────────────────────────────────

_APP_ID_PATTERN = re.compile(r"\b(?:\d[\s\-/.]*){8,14}\b")


def _extract_gna_lta_ids(text: str) -> str:
    """Extract application-like numeric IDs from Connectivity Applicant text."""
    if not text:
        return ""
    ids: list[str] = []
    seen: set[str] = set()
    for match in _APP_ID_PATTERN.findall(text):
        normalized = re.sub(r"\D", "", match)
        if len(normalized) < 8 or len(normalized) > 14:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ids.append(normalized)
    return "; ".join(ids)


def recompute_row_fields(row: dict, runtime=None) -> dict:
    """Recompute all derived fields (COD, effective_date, GNA, TGNA, etc.).

    This replaces the old ``_add_computed_fields`` with the improved logic.
    """
    from pipeline.jcc_handler.models import COLUMN_NAMES

    schedule_text = row.get("schedule_as_per_current_jcc", "")
    gna_text = row.get("connectivity_start_date_under_gna", "")
    bay_text = "\n".join(
        value for value in [
            row.get("bayno", ""),
            row.get("pooling_station", ""),
            row.get("connectivity_applicant", ""),
            row.get("schedule_as_per_current_jcc", ""),
            row.get("connectivity_start_date_under_gna", ""),
        ]
        if value
    )

    # Robust COD extraction
    total_cod, cod_found = calculate_total_cod(schedule_text, runtime=runtime)

    # Improved effective date extraction
    effective_date = extract_effective_date(gna_text)

    # GNA / TGNA logic
    tgna = ""
    gna = ""
    if cod_found and total_cod is not None:
        parsed_eff = _parse_date(effective_date)
        if parsed_eff:
            if parsed_eff <= date.today():
                gna = total_cod
            else:
                tgna = total_cod

    # Build output row
    output = {col: row.get(col, "") for col in COLUMN_NAMES}
    if not output.get("bayno"):
        output["bayno"] = _extract_bay_no(bay_text)
    output["total_COD"] = total_cod if cod_found else ""
    output["COD_Found"] = cod_found
    output["effective_date"] = effective_date
    output["TGNA"] = tgna
    output["GNA"] = gna
    output["substation"] = row.get("pooling_station", "")
    output["gna_lta_id"] = _extract_gna_lta_ids(row.get("connectivity_applicant", ""))
    return output


def postprocess_all_pages(all_pages: list[dict], runtime=None) -> list[dict]:
    """Run the full postprocessing pipeline on extracted JCC pages.

    Steps:
      1. Merge continuation rows (page-spanning rows with empty pooling_station)
      2. Recompute all derived fields (COD, effective_date, GNA/TGNA)

    Parameters
    ----------
    all_pages : list[dict]
        Per-page result dicts as produced by ``extract_jcc_pdf``.
    runtime : RuntimeConfig, optional
        LLM runtime config for COD fallback extraction.

    Returns
    -------
    list[dict] — the same list, modified in place (for chaining convenience).
    """
    logger.info("[JCC Postprocess] Starting postprocessing on %d pages …", len(all_pages))

    # Step 1: Merge continuation rows
    merge_continuation_rows(all_pages)

    # Step 2: Recompute derived fields on every row
    recomputed = 0
    cod_improved = 0
    eff_improved = 0

    for page in all_pages:
        new_rows = []
        for row in page.get("rows", []):
            old_cod_found = row.get("COD_Found", False)
            old_eff = row.get("effective_date", "")

            updated = recompute_row_fields(row, runtime=runtime)

            if not old_cod_found and updated.get("COD_Found", False):
                cod_improved += 1
            if not old_eff and updated.get("effective_date", ""):
                eff_improved += 1

            new_rows.append(updated)
            recomputed += 1
        page["rows"] = new_rows

    logger.info(
        "[JCC Postprocess] Done — %d rows recomputed, %d COD improvements, %d effective_date improvements",
        recomputed,
        cod_improved,
        eff_improved,
    )
    print(f"  [JCC Postprocess] {recomputed} rows recomputed")
    if cod_improved:
        print(f"  [JCC Postprocess] {cod_improved} rows: COD now detected (was missing)")
    if eff_improved:
        print(f"  [JCC Postprocess] {eff_improved} rows: effective_date now populated (was empty)")

    return all_pages
