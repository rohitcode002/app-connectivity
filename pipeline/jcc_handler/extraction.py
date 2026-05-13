"""
jcc_handler/extraction.py — PDF table extraction logic
========================================================
Uses a column-name gate to find connectivity/pooling station pages in
JCC Meeting PDFs, then extracts the target rows with an LLM primary path
and a pdfplumber table fallback.

Edit this file to change how tables are detected, headers are matched,
or data rows are parsed.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Optional

import pdfplumber

from config import MODEL
from pipeline.jcc_handler.models import (
    REQUIRED_KEYWORDS,
    TARGET_COLUMN_FRAGMENTS,
    COLUMN_NAMES,
)
from pipeline.shared_utils import parse_json


SYSTEM_PROMPT = """You extract rows from JCC meeting PDF table pages.

Return only valid JSON. Do not include markdown.
Extract one object per data row from the connectivity schedule table.
Use exactly these keys:
pooling_station, connectivity_applicant, connectivity_quantum_mw,
schedule_as_per_current_jcc, connectivity_start_date_under_gna.

Column meanings:
- pooling_station is the table's Pooling Station column. Extract it with the same rule as CMETS Substation:
  use the connectivity/interconnection/pooling station name only, e.g. "Sirohi PS" or "Fatehgarh-II".
  Do not include bay numbers, voltage, developer names, schedule text, or remarks.
- schedule_as_per_current_jcc is "Under Grantee scope Gen Commissioning / Connectivity line schedule".
- connectivity_start_date_under_gna is "Connectivity Start Date under GNA and Connectivity Effectiveness date".
- The schedule column may label the generation section as "RE generation", "Generation", or "Generation (MW)".

Rules:
- Preserve application IDs inside connectivity_applicant.
- Preserve MW values, dates, COD/CoD/DOCO/Commissioned tags, and line breaks as readable text.
- Extract only the raw text present in those five columns. Do not infer, summarize, or move text between columns.
- Do not invent values. Use empty string for missing cells.
- Ignore repeated header rows, footers, and page titles.
"""

USER_TEMPLATE = """Extract the JCC connectivity table rows from this page.

The page was selected because it contains the target column names. Extract only rows under those columns.

PAGE {page_number}
TEXT:
{page_text}

TABLE CELL VIEW:
{table_text}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(text) -> str:
    """Strip excess whitespace from a cell value."""
    if text is None:
        return ""
    return " ".join(str(text).split())


def _clean_multiline(text) -> str:
    if text is None:
        return ""
    return "\n".join(" ".join(line.split()) for line in str(text).splitlines() if line.strip())


def _is_target_table(table: list) -> bool:
    """Return True if the table looks like the connectivity table."""
    if not table or len(table) < 2:
        return False
    header_text = " ".join(
        _clean(c).lower()
        for row in table[:3]
        for c in (row or [])
        if c
    )
    hits = sum(1 for kw in TARGET_COLUMN_FRAGMENTS if kw in header_text)
    return hits >= 3


def _normalise_row(row: list, n_cols: int) -> list[str]:
    """Pad or trim a row to exactly *n_cols* cells."""
    cleaned = [_clean(c) for c in (row or [])]
    if len(cleaned) < n_cols:
        cleaned += [""] * (n_cols - len(cleaned))
    else:
        cleaned = cleaned[:n_cols]
    return cleaned


def _requested_columns_from_pdf_row(raw_row: list) -> dict:
    """Map a full JCC table row to the requested five raw columns.

    The source table normally has 8-9 columns:
    sr, pooling, applicant, quantum, previous schedule, current grantee
    scope, ISTS scope, connectivity/effectiveness date, remarks.
    """
    cells = _normalise_row(raw_row, 9)
    return {
        "pooling_station": cells[1],
        "connectivity_applicant": cells[2],
        "connectivity_quantum_mw": cells[3],
        "schedule_as_per_current_jcc": cells[5],
        "connectivity_start_date_under_gna": cells[7],
    }


def _count_header_rows(table: list) -> int:
    """Count how many leading rows are header/sub-header rows."""
    count = 0
    for row in table:
        row_text = " ".join(_clean(c).lower() for c in row if c)
        if any(kw in row_text for kw in ["pooling", "grantee scope", "under ists"]):
            count += 1
        else:
            break
    return count


# ── Page-level extraction ─────────────────────────────────────────────────────

def page_passes_gate(text: str) -> bool:
    """Check whether *text* contains the target JCC table column names."""
    norm = _clean(text).lower()
    if not norm:
        return False

    hits = sum(1 for frag in TARGET_COLUMN_FRAGMENTS if frag in norm)
    has_core_columns = (
        "pooling" in norm
        and "applicant" in norm
        and "quantum" in norm
        and "connectivity start" in norm
    )
    # Legacy broad gate kept as a weak fallback for PDFs where header wrapping
    # makes exact fragments disappear from pdfplumber text.
    legacy_hits = sum(1 for kw in REQUIRED_KEYWORDS if kw.lower() in norm)
    return has_core_columns or hits >= 4 or (legacy_hits == len(REQUIRED_KEYWORDS) and "schedule" in norm)


def _page_table_text(page) -> str:
    tables = page.extract_tables() or []
    rendered: list[str] = []
    for table_idx, table in enumerate(tables, 1):
        rows = []
        for row in table or []:
            rows.append(" | ".join(_clean_multiline(cell) for cell in (row or [])))
        if rows:
            rendered.append(f"Table {table_idx}:\n" + "\n".join(rows))
    return "\n\n".join(rendered)


_LLM_KEY_ALIASES = {
    "Pooling Station": "pooling_station",
    "Connectivity Applicant": "connectivity_applicant",
    "Connectivity Quantum (MW)": "connectivity_quantum_mw",
    "Under Grantee scope Gen Commissioning /Connectivity line schedule": "schedule_as_per_current_jcc",
    "Under Grantee scope Gen Commissioning / Connectivity line schedule": "schedule_as_per_current_jcc",
    "Connectivity Start Date under GNA and Connectivity Effectiveness date": "connectivity_start_date_under_gna",
}


def _get_row_value(row: dict, key: str) -> str:
    if key in row:
        return _clean_multiline(row.get(key, ""))
    for raw_key, canonical in _LLM_KEY_ALIASES.items():
        if canonical == key and raw_key in row:
            return _clean_multiline(row.get(raw_key, ""))
    return ""


def _rows_from_llm_result(result) -> list[dict]:
    if isinstance(result, list):
        rows = result
    elif isinstance(result, dict):
        rows = result.get("rows", [])
        if not isinstance(rows, list):
            rows = next((v for v in result.values() if isinstance(v, list)), [])
    else:
        rows = []

    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = {col: _get_row_value(row, col) for col in COLUMN_NAMES}
        if normalized.get("pooling_station") or normalized.get("connectivity_applicant"):
            cleaned.append(_add_computed_fields(normalized))
    return cleaned


def llm_extract_page_rows(page_text: str, table_text: str, page_number: int, runtime) -> list[dict]:
    """Extract target rows from a page using the configured LLM runtime."""
    if runtime is None or (not getattr(runtime, "vm_mode", False) and not getattr(runtime, "api_key", "")):
        return []

    try:
        from llm_client import call_llm, extract_text_from_response
    except Exception as exc:
        print(f"      [JCC LLM unavailable] {exc}")
        return []

    prompt = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    page_number=page_number,
                    page_text=page_text,
                    table_text=table_text or "(no table cells extracted)",
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 5000,
    }

    for attempt in range(3):
        try:
            resp = call_llm(
                prompt,
                vm=runtime.vm_mode,
                api_key=runtime.api_key or None,
                model=MODEL,
                script_path=runtime.llm_script_path,
            )
            content = extract_text_from_response(resp)
            return _rows_from_llm_result(parse_json(content))
        except Exception as exc:
            if attempt < 2:
                time.sleep(5)
            else:
                print(f"      [JCC LLM failed] {exc}")
    return []


_DATE_PATTERN = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
_COD_LINE_PATTERN = re.compile(
    rf"([\d,]+(?:\.\d+)?)\s*MW[^\n\r]{{0,120}}?({_DATE_PATTERN})[^\n\r]{{0,80}}?\((?:CoD|COD|Commissioned)\)",
    re.IGNORECASE,
)
_EFFECTIVE_DATE_PATTERN = re.compile(
    rf"\beffective\b(?:\s*w\.?e\.?f\.?)?[^0-9]{{0,80}}(?P<date>{_DATE_PATTERN})",
    re.IGNORECASE,
)
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


def _generation_block(text: str) -> str:
    """Return only the Generation section from a JCC schedule cell."""
    if not text:
        return ""
    match = re.search(
        r"(?:\bre\s+generation\b|\bgeneration(?:\s*\(\s*mw\s*\))?)\s*:?(.*?)(?:\bdedicated\s+system\b|\bdtl\s*:|\bgeneration\s+pooling\s+station\b|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _calculate_total_cod(text: str) -> tuple[float | None, bool]:
    """Sum MW rows in the Generation block that have date + COD/Commissioned."""
    values: list[float] = []
    for mw, _dt in _COD_LINE_PATTERN.findall(_generation_block(text)):
        try:
            values.append(float(mw.replace(",", "")))
        except ValueError:
            continue
    if not values:
        return None, False
    return round(sum(values), 2), True


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    normalized = value.replace("/", ".").replace("-", ".")
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def _extract_effective_date(text: str) -> str:
    if not text:
        return ""
    match = _EFFECTIVE_DATE_PATTERN.search(text)
    if match:
        return match.group("date")
    return ""


def _add_computed_fields(row: dict) -> dict:
    grantee_schedule = row.get("schedule_as_per_current_jcc", "")
    total_cod, cod_found = _calculate_total_cod(grantee_schedule)
    effective_date = _extract_effective_date(row.get("connectivity_start_date_under_gna", ""))

    tgna = None
    gna = None
    if cod_found and total_cod is not None:
        parsed_effective_date = _parse_date(effective_date)
        if parsed_effective_date:
            if parsed_effective_date <= date.today():
                gna = total_cod
            else:
                tgna = total_cod

    output = {col: row.get(col, "") for col in COLUMN_NAMES}
    output["total_COD"] = total_cod
    output["COD_Found"] = cod_found
    output["effective_date"] = effective_date
    output["TGNA"] = tgna
    output["GNA"] = gna
    row["substation"] = row.get("pooling_station", "")
    row["gna_lta_id"] = _extract_gna_lta_ids(row.get("connectivity_applicant", ""))
    output["substation"] = row["substation"]
    output["gna_lta_id"] = row["gna_lta_id"]
    return output


def extract_page_data(page, page_number: int) -> Optional[dict]:
    """Extract the connectivity table from a single pdfplumber Page.

    Returns a dict with page metadata and a list of row dicts,
    or None if no target table is found.
    """
    tables = page.extract_tables()
    target = None
    for tbl in tables:
        if _is_target_table(tbl):
            target = tbl
            break

    if target is None:
        return None

    header_rows = _count_header_rows(target)
    data_rows   = target[header_rows:]

    rows = []
    for raw_row in data_rows:
        if not any(_clean(cell) for cell in (raw_row or [])):
            continue
        row = _requested_columns_from_pdf_row(raw_row)
        rows.append(_add_computed_fields(row))

    return {
        "page_number": page_number,
        "raw_text":    page.extract_text() or "",
        "rows":        rows,
    }


# ── Single-PDF extraction ────────────────────────────────────────────────────

def extract_jcc_pdf(pdf_path: str, runtime=None) -> list[dict]:
    """Extract all matching pages from one JCC PDF.

    Returns a list of page result dicts (same shape as extract_page_data).
    """
    all_pages: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"  [JCC] {total} pages — scanning for target tables …")

        for i, page in enumerate(pdf.pages):
            page_number = i + 1
            text = page.extract_text() or ""

            if not page_passes_gate(text):
                continue

            result = None
            table_text = _page_table_text(page)
            print(f"  [JCC] Page {page_number:3d} target columns found → LLM …", end="", flush=True)
            llm_rows = llm_extract_page_rows(text, table_text, page_number, runtime)
            if llm_rows:
                print(f" {len(llm_rows)} rows")
                result = {
                    "page_number": page_number,
                    "raw_text": text,
                    "rows": llm_rows,
                    "extraction_method": "llm",
                }
            else:
                print(" fallback table")
                result = extract_page_data(page, page_number)
                if result is not None:
                    result["extraction_method"] = "pdfplumber_table"

            if result is None:
                continue

            row_count = len(result["rows"])
            print(f"  ✓ Page {page_number:3d} → {row_count} data rows")
            all_pages.append(result)

    return all_pages
