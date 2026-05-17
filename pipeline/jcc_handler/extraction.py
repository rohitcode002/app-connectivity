"""
jcc_handler/extraction.py — PDF table extraction logic
========================================================
Uses a column-name gate to find connectivity/pooling station pages in
JCC Meeting PDFs, then extracts the target rows using pdfplumber's
table detection (primary) with camelot as fallback.

Edit this file to change how tables are detected, headers are matched,
or data rows are parsed.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Optional

import pdfplumber

try:
    import camelot
    _HAS_CAMELOT = True
except ImportError:
    _HAS_CAMELOT = False

from config import MODEL
from pipeline.jcc_handler.models import (
    REQUIRED_KEYWORDS,
    TARGET_COLUMN_FRAGMENTS,
    TARGET_COLUMN_FRAGMENT_GROUPS,
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
    """Return True if the table looks like the connectivity table.

    Uses grouped fragments so that regional naming differences
    (e.g. 'Grantee' vs 'Applicant', 'GNA Quantum' vs 'Connectivity Quantum')
    each count as one hit.
    """
    if not table or len(table) < 2:
        return False
    # Gather text from first 5 rows to account for multi-row headers
    header_text = " ".join(
        _clean(c).lower()
        for row in table[:5]
        for c in (row or [])
        if c
    )
    # Count how many fragment groups have at least one match
    group_hits = sum(
        1 for group in TARGET_COLUMN_FRAGMENT_GROUPS
        if any(frag in header_text for frag in group)
    )
    return group_hits >= 3


def _normalise_row(row: list, n_cols: int) -> list[str]:
    """Pad or trim a row to exactly *n_cols* cells."""
    cleaned = [_clean(c) for c in (row or [])]
    if len(cleaned) < n_cols:
        cleaned += [""] * (n_cols - len(cleaned))
    else:
        cleaned = cleaned[:n_cols]
    return cleaned


# Keywords that indicate a row is a header (not data)
_HEADER_ROW_KEYWORDS = [
    "pooling", "grantee scope", "under ists", "grantee", "applicant",
    "quantum", "commissioning", "schedule", "connectivity start",
    "start date", "sl.", "sl .", "s .", "under applicant",
    "dedicated line", "ists scope", "operationalization",
    "remarks", "deliberation",
]


def _detect_column_mapping(table: list) -> dict[str, int]:
    """Auto-detect which column index maps to each target field.

    Scans the first 5 rows (header area) for keyword matches and
    returns a mapping of canonical field names to column indices.
    Falls back to positional defaults if detection fails.
    """
    n_cols = max((len(row) for row in table[:5] if row), default=0)
    if n_cols == 0:
        return {}

    # Build per-column text from the header rows
    col_texts: list[str] = []
    for col_idx in range(n_cols):
        parts = []
        for row in table[:5]:
            if row and col_idx < len(row) and row[col_idx]:
                parts.append(_clean(row[col_idx]).lower())
        col_texts.append(" ".join(parts))

    mapping: dict[str, int] = {}

    # Applicant / Grantee column
    for idx, txt in enumerate(col_texts):
        if "applicant" in txt or "grantee" in txt:
            mapping["connectivity_applicant"] = idx
            break

    # Quantum column
    for idx, txt in enumerate(col_texts):
        if "quantum" in txt or "quant" in txt:
            mapping["connectivity_quantum_mw"] = idx
            break

    # Pooling station (may be absent in some regions)
    for idx, txt in enumerate(col_texts):
        if "pooling" in txt and idx != mapping.get("connectivity_applicant"):
            mapping["pooling_station"] = idx
            break

    # Schedule column — look for the CURRENT JCC schedule, not previous
    # Typically there are two schedule column blocks; take the second one
    schedule_candidates = []
    for idx, txt in enumerate(col_texts):
        if idx in mapping.values():
            continue
        if ("schedule as per" in txt or "schedule" in txt) and (
            "commissioning" in txt or "gen comm" in txt or "scope" in txt
            or "applicant" in txt
        ):
            schedule_candidates.append(idx)
    # Prefer the column labeled as "current" or the later one
    if schedule_candidates:
        for idx in schedule_candidates:
            if any(kw in col_texts[idx] for kw in ["current", "mar", "jun", "sep", "dec"]):
                mapping["schedule_as_per_current_jcc"] = idx
                break
        if "schedule_as_per_current_jcc" not in mapping:
            # Take the last schedule candidate (usually the updated one)
            mapping["schedule_as_per_current_jcc"] = schedule_candidates[-1]

    # Connectivity start date / effectiveness date
    for idx, txt in enumerate(col_texts):
        if idx in mapping.values():
            continue
        if ("start date" in txt or "connectivity start" in txt
                or "effectiveness" in txt or "operationalization" in txt):
            mapping["connectivity_start_date_under_gna"] = idx
            break

    return mapping


def _requested_columns_from_pdf_row(raw_row: list, col_map: dict[str, int] | None = None) -> dict:
    """Map a full JCC table row to the requested five raw columns.

    If *col_map* is provided (auto-detected from the header), uses it.
    Otherwise falls back to positional defaults (8-9 column layout).
    """
    if col_map:
        cells = [_clean(c) for c in (raw_row or [])]
        n = len(cells)
        return {
            field: cells[idx] if idx < n else ""
            for field, idx in col_map.items()
        }

    # Legacy positional fallback
    cells = _normalise_row(raw_row, 9)
    return {
        "pooling_station": cells[1],
        "connectivity_applicant": cells[2],
        "connectivity_quantum_mw": cells[3],
        "schedule_as_per_current_jcc": cells[5],
        "connectivity_start_date_under_gna": cells[7],
    }


def _count_header_rows(table: list) -> int:
    """Count how many leading rows are header/sub-header rows.

    Uses broad keyword matching to work across all regional naming
    conventions.
    """
    count = 0
    for row in table:
        row_text = " ".join(_clean(c).lower() for c in row if c)
        if not row_text.strip():
            count += 1
            continue
        # A row is a header row if it contains header-like keywords
        # and does NOT look like a data row (starts with a serial number)
        is_header = any(kw in row_text for kw in _HEADER_ROW_KEYWORDS)
        # Data rows typically start with a serial number like "1.", "2.", etc.
        first_cell = _clean(row[0]).strip() if row and row[0] else ""
        looks_like_data = bool(re.match(r'^\d+\.?$', first_cell))
        if is_header and not looks_like_data:
            count += 1
        else:
            break
    return count


# ── Page-level extraction ─────────────────────────────────────────────────────

def page_passes_gate(text: str) -> bool:
    """Check whether *text* contains the target JCC table column names.

    Uses both grouped fragments and flat fragment counting to handle
    all regional naming conventions.
    """
    norm = _clean(text).lower()
    if not norm:
        return False

    # Grouped fragment check (more accurate)
    group_hits = sum(
        1 for group in TARGET_COLUMN_FRAGMENT_GROUPS
        if any(frag in norm for frag in group)
    )

    # Flat fragment check (broader)
    flat_hits = sum(1 for frag in TARGET_COLUMN_FRAGMENTS if frag in norm)

    has_core_columns = (
        ("pooling" in norm or "grantee" in norm)
        and ("applicant" in norm or "grantee" in norm)
        and "quantum" in norm
        and ("connectivity start" in norm or "start date" in norm)
    )
    # Legacy broad gate kept as a weak fallback for PDFs where header wrapping
    # makes exact fragments disappear from pdfplumber text.
    legacy_hits = sum(1 for kw in REQUIRED_KEYWORDS if kw.lower() in norm)
    return (
        has_core_columns
        or group_hits >= 3
        or flat_hits >= 4
        or (legacy_hits == len(REQUIRED_KEYWORDS) and "schedule" in norm)
    )


def _pdfplumber_tables(page) -> list[list[list[str]]]:
    """Extract tables from a pdfplumber page object.

    Returns a list of tables, each table being a list of rows,
    each row being a list of cell strings.
    """
    try:
        raw_tables = page.extract_tables() or []
    except Exception:
        raw_tables = []

    tables: list[list[list[str]]] = []
    for tbl in raw_tables:
        if not tbl or len(tbl) < 2:
            continue
        rows = []
        for row in tbl:
            rows.append([str(c) if c else "" for c in row])
        tables.append(rows)
    return tables


def _camelot_tables_fallback(pdf_path: str, page_number: int) -> list[list[list[str]]]:
    """Fallback: extract tables using camelot when pdfplumber finds nothing.

    Tries lattice first, then stream.
    """
    if not _HAS_CAMELOT:
        return []

    for flavor in ('lattice', 'stream'):
        try:
            tables = camelot.read_pdf(
                pdf_path, pages=str(page_number), flavor=flavor,
                suppress_stdout=True,
            )
            if tables and tables.n:
                result = []
                for tbl in tables:
                    rows = []
                    for _, row in tbl.df.iterrows():
                        rows.append([str(v) if v else "" for v in row.values])
                    if len(rows) >= 2:
                        result.append(rows)
                if result:
                    return result
        except Exception:
            pass
    return []


def _page_table_text_from_tables(tables: list[list[list[str]]]) -> str:
    """Build a table-cell text view from pre-extracted tables for LLM context."""
    if not tables:
        return ""

    rendered: list[str] = []
    for table_idx, tbl in enumerate(tables, 1):
        rows = []
        for row in tbl:
            rows.append(" | ".join(_clean_multiline(c) for c in row))
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


# COD patterns and effective date extraction are now in jcc_postprocess.py
# These legacy definitions are kept for backward compatibility with
# jcc_output_layer.py's _extract_cod_mw helper.
_DATE_PATTERN = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
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
    """Sum MW rows in the Generation block that have date + COD/Commissioned.

    Uses the robust multi-pattern approach from jcc_postprocess.
    """
    from pipeline.jcc_handler.jcc_postprocess import calculate_total_cod
    return calculate_total_cod(text, runtime=None)


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
    """Extract the connectivity effective date using the improved logic."""
    from pipeline.jcc_handler.jcc_postprocess import extract_effective_date
    return extract_effective_date(text)


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


def extract_page_data(
    page,
    page_number: int,
    pdf_path: str = "",
    page_text: str = "",
    continuation: bool = False,
    prev_col_map: dict[str, int] | None = None,
) -> tuple[Optional[dict], dict[str, int] | None]:
    """Extract the connectivity table from a single page.

    Uses pdfplumber as primary extractor (clean cell text) with
    camelot as fallback when pdfplumber finds no tables.

    Returns a tuple of:
      - dict with page metadata and row dicts (or None if no table)
      - detected column mapping (for use on subsequent continuation pages)

    When *continuation* is True, we accept tables even without a header
    row, using the column mapping from the previous page.
    """
    # Primary: pdfplumber
    all_tables = _pdfplumber_tables(page)

    # Fallback: camelot (only if pdfplumber found nothing)
    extraction_method = "pdfplumber"
    if not all_tables and pdf_path:
        all_tables = _camelot_tables_fallback(pdf_path, page_number)
        if all_tables:
            extraction_method = "camelot_fallback"

    if not all_tables:
        return None, prev_col_map

    target = None
    is_header_page = False
    for tbl in all_tables:
        if _is_target_table(tbl):
            target = tbl
            is_header_page = True
            break

    # If no header-bearing table found but we're in continuation mode,
    # take the largest table on the page as continuation data
    if target is None and continuation and prev_col_map:
        largest = None
        max_rows = 0
        for tbl in all_tables:
            if len(tbl) > max_rows:
                max_rows = len(tbl)
                largest = tbl
        if largest and max_rows >= 1:
            target = largest
            is_header_page = False

    if target is None:
        return None, prev_col_map if continuation else None

    # Detect column mapping from header, or reuse from previous page
    col_map = _detect_column_mapping(target) if is_header_page else prev_col_map

    header_count = _count_header_rows(target) if is_header_page else 0
    data_rows = target[header_count:]

    rows = []
    for raw_row in data_rows:
        if not any(_clean(cell) for cell in (raw_row or [])):
            continue
        row = _requested_columns_from_pdf_row(raw_row, col_map)
        # Ensure all canonical columns exist
        for col in COLUMN_NAMES:
            row.setdefault(col, "")
        rows.append(_add_computed_fields(row))

    result = {
        "page_number": page_number,
        "raw_text":    page_text,
        "rows":        rows,
        "extraction_method": extraction_method,
    }
    return result, col_map


# ── Single-PDF extraction ────────────────────────────────────────────────────

def extract_jcc_pdf(pdf_path: str, runtime=None, max_pages: int = -1) -> list[dict]:
    """Extract all matching pages from one JCC PDF.

    Uses pdfplumber for primary table extraction (cleaner cell text)
    with camelot as fallback when pdfplumber finds no tables.

    For each page:
      1. Extract tables with pdfplumber (primary) or camelot (fallback)
      2. Check if any table contains the target columns
      3. If yes → extract data rows from that table
      4. If no  → if previous page was a match, treat as continuation

    After extraction, runs postprocessing to:
      1. Merge continuation rows (page-spanning rows with empty pooling_station)
      2. Recompute all derived fields with improved COD + effective_date logic
    """
    from pipeline.jcc_handler.jcc_postprocess import postprocess_all_pages

    all_pages: list[dict] = []
    in_table_region = False       # True after we detect a header page
    active_col_map: dict | None = None   # column mapping from last header page
    consecutive_misses = 0        # pages without data since last match
    _MAX_CONTINUATION_GAP = 2     # allow up to N blank pages in a table run

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        limit = total if max_pages == -1 else min(max_pages, total)
        label = "all" if max_pages == -1 else f"first {limit} of"
        print(f"  [JCC] {total} pages ({label}) — scanning for target tables …")

        for i in range(limit):
            page_number = i + 1
            plumber_page = pdf.pages[i]
            page_text = plumber_page.extract_text() or ""

            # Decide whether to try continuation extraction
            is_continuation = in_table_region and consecutive_misses < _MAX_CONTINUATION_GAP

            result, col_map = extract_page_data(
                plumber_page, page_number,
                pdf_path=pdf_path,
                page_text=page_text,
                continuation=is_continuation,
                prev_col_map=active_col_map,
            )

            if result is not None and result.get("rows"):
                row_count = len(result["rows"])
                method = result.get("extraction_method", "pdfplumber")
                tag = "cont" if is_continuation and not col_map else "hdr"
                print(f"  ✓ Page {page_number:3d} → {row_count} data rows [{tag}] ({method})")
                all_pages.append(result)
                in_table_region = True
                consecutive_misses = 0
                if col_map:
                    active_col_map = col_map
            else:
                if in_table_region:
                    consecutive_misses += 1
                    if consecutive_misses >= _MAX_CONTINUATION_GAP:
                        # Table region ended
                        in_table_region = False
                        active_col_map = None
                        consecutive_misses = 0

    # ── Postprocessing: merge continuations + recompute COD/effective_date ──
    if all_pages:
        postprocess_all_pages(all_pages, runtime=runtime)

    return all_pages
