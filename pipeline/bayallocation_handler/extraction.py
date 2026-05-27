"""
bayallocation_handler/extraction.py -- PDF table extraction logic
=================================================================
Uses Camelot as the primary page-wise table extractor for the bay-allocation
table, then sends the extracted table text to the LLM for row extraction.
pdfplumber remains only as a fallback. Each page is treated as one independent
extraction unit.

Camelot produces higher accuracy tables (99%+ with lattice mode)
compared to pdfplumber's less reliable cell detection.

The output keeps the old substation-level ``220kv.bay_no`` / ``400kv.bay_no``
dicts for compatibility, and also emits richer row-level JSON:

* ``substations[].allocations``: searchable allocation records containing
  voltage, bay number, entity name, connectivity quantum, substation name and
  coordinates together.
* ``pages[].table_rows``: formatted table rows with all canonical column names
  preserved for debugging/searching.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any
from typing import Optional

import pdfplumber

try:
    import camelot
    _HAS_CAMELOT = True
except ImportError:
    _HAS_CAMELOT = False

from pipeline.bayallocation_handler.models import (
    REQUIRED_KEYWORDS,
    TARGET_COLUMN_FRAGMENTS,
    COLUMN_NAMES,
    HEADER_ROW_COUNT,
)
from pipeline.shared_utils import parse_json
from pipeline.token_usage import record_llm_token_usage

logger = logging.getLogger(__name__)
MODEL = "gpt-4o-mini"
_START_DIR = Path(__file__).resolve().parent.parent.parent
_BAY_PAGE_TEXT_ROOT = _START_DIR / "temp" / "bayallocation_pages"

BAY_LLM_SYSTEM_PROMPT = """You extract CTUIL Bay Allocation table data from one PDF page.
Return only valid JSON. Do not include markdown fences or commentary.
Preserve text exactly as seen where practical. If a cell is blank, return an empty string.
Extract only RE Capacity Granted allocation rows, not margin-only or space-provision rows."""

BAY_LLM_USER_TEMPLATE = """Extract Bay Allocation rows from page {page_number}.

Return JSON in this exact shape:
{{
  "rows": [
    {{
      "sl_no": "",
      "name_of_substation": "",
      "substation_coordinates": "",
      "region": "",
      "transformation_capacity_planned_mva": "",
      "transformation_capacity_existing_mva": "",
      "transformation_capacity_under_implementation_mva": "",
      "voltage_key": "220kv or 400kv",
      "bay_no": "",
      "connectivity_quantum_mw": "",
      "name_of_entity": "",
      "margin_bay_no": "",
      "margin_available_mw": "",
      "section": ""
    }}
  ]
}}

Rules:
- The source table has separate RE Capacity Granted groups for 220kV and 400kV. Emit one row per granted bay entry.
- Carry forward substation fields from row-spanned cells until a new substation begins.
- Use voltage_key="220kv" for the 220kV grant columns and voltage_key="400kv" for the 400kV grant columns.
- Include rows even when the entity name is blank if a granted bay number is present.
- Do not emit section headers, totals, margin-only rows, or space-provision-only rows.
- If the page has no extractable granted bay rows, return {{"rows": []}}.

PDF page text:
{page_text}

Extracted table text:
{table_text}
"""

BAY_IMAGE_LLM_USER_TEMPLATE = """Extract Bay Allocation rows from this page image.
This is page {page_number} from image file: {image_name}

Return JSON in this exact shape:
{{
  "rows": [
    {{
      "sl_no": "",
      "name_of_substation": "",
      "substation_coordinates": "",
      "region": "",
      "transformation_capacity_planned_mva": "",
      "transformation_capacity_existing_mva": "",
      "transformation_capacity_under_implementation_mva": "",
      "voltage_key": "220kv or 400kv",
      "bay_no": "",
      "connectivity_quantum_mw": "",
      "name_of_entity": "",
      "margin_bay_no": "",
      "margin_available_mw": "",
      "space_provision_220kv": "",
      "space_provision_400kv": "",
      "remarks": "",
      "section": ""
    }}
  ]
}}

Rules:
- Read the page image carefully; it is a wide spreadsheet-style table.
- Emit one row per RE Capacity Granted bay allocation entry.
- For the 220kV RE Capacity Granted columns, use voltage_key="220kv".
- For the 400kV RE Capacity Granted columns, use voltage_key="400kv".
- Carry forward merged cells: substation name, coordinates, region, transformation capacity, space provision, and remarks apply to all allocation rows below that substation until the next substation starts.
- Preserve multi-line entity names in one cell as a single string.
- Include rows when a bay number exists even if quantum/entity is blank.
- Do not emit section header rows, purple total rows, margin-only rows, or empty rows.
- If no allocation rows are visible, return {{"rows": []}}.
"""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _clean(text) -> str:
    """Collapse whitespace and strip a cell value."""
    if text is None:
        return ""
    return " ".join(str(text).split())


def _compact(text: str) -> str:
    """Lowercase text and remove non-alphanumerics for PDF header matching."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _safe_path_part(value: str) -> str:
    value = re.sub(r"[^\w .()&+-]+", "_", value.strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "unknown"


def _page_text_output_dir(pdf_path: str) -> Path:
    pdf = Path(pdf_path)
    return _BAY_PAGE_TEXT_ROOT / _safe_path_part(pdf.stem)


def _save_page_text(pdf_path: str, page_number: int, page_text: str) -> Path:
    dest = _page_text_output_dir(pdf_path)
    dest.mkdir(parents=True, exist_ok=True)
    out_file = dest / f"page {page_number}.txt"
    out_file.write_text(page_text, encoding="utf-8")
    return out_file


def _is_target_table(table: list) -> bool:
    """Return True if the table looks like the bay-allocation master table."""
    if not table or len(table) < 2:
        return False
    header_text = " ".join(
        _clean(c).lower()
        for row in table[:3]
        for c in (row or [])
        if c
    )
    compact_header = _compact(header_text)
    hits = sum(
        1
        for kw in TARGET_COLUMN_FRAGMENTS
        if kw.lower() in header_text or _compact(kw) in compact_header
    )
    return hits >= 3


def _normalise_row(row: list, n_cols: int) -> list[str]:
    """Pad or trim a row to exactly *n_cols* cells."""
    cleaned = [_clean(c) for c in row]
    if len(cleaned) < n_cols:
        cleaned += [""] * (n_cols - len(cleaned))
    else:
        cleaned = cleaned[:n_cols]
    return cleaned


def _table_x_edges(table) -> list[float]:
    """Return column boundaries inferred from pdfplumber table cells."""
    return sorted({x for cell in table.cells for x in (cell[0], cell[2])})


def _cell_text(page, cell) -> str:
    """Extract text from a pdfplumber cell bbox with spacing preserved."""
    if not cell:
        return ""
    text = page.crop(cell).extract_text(x_tolerance=1, y_tolerance=3) or ""
    return _clean(text)


def _spanned_cell_text(page, table, row_index: int, col_index: int) -> str:
    """Return text from a row-spanned cell covering row_index/col_index.

    pdfplumber returns ``None`` for cells covered by a rowspan. The bay
    allocation PDFs use rowspans for quantum/entity cells when multiple bay
    numbers share the same grant. This helper looks upward in the same column
    and reuses the cell whose bbox still covers the current row midpoint.
    """
    row = table.rows[row_index]
    y_mid = (row.bbox[1] + row.bbox[3]) / 2

    for prev_index in range(row_index - 1, -1, -1):
        if col_index >= len(table.rows[prev_index].cells):
            continue
        prev_cell = table.rows[prev_index].cells[col_index]
        if not prev_cell:
            continue
        if prev_cell[1] <= y_mid <= prev_cell[3]:
            return _cell_text(page, prev_cell)
        if prev_cell[3] < y_mid:
            break
    return ""


def _extract_row_from_cells(page, table, row_index: int, n_cols: int) -> list[str]:
    """Extract one table row using cell bboxes and fill same-column rowspans."""
    cells = table.rows[row_index].cells
    values: list[str] = []
    for col_index in range(n_cols):
        cell = cells[col_index] if col_index < len(cells) else None
        value = _cell_text(page, cell)
        if not value and cell is None:
            value = _spanned_cell_text(page, table, row_index, col_index)
        values.append(value)
    return values


def _extract_left_column_values(page, table, row_index: int, n_cols: int) -> list[str]:
    """Extract left fixed columns by x/y crop.

    Some rows begin a new substation in left-side columns while pdfplumber's
    table extraction leaves those row-spanned cells empty. Cropping by inferred
    grid boundaries recovers the serial number, substation name and coordinates.
    """
    try:
        x_edges = _table_x_edges(table)
    except Exception:
        return [""] * min(7, n_cols)

    if len(x_edges) < n_cols + 1:
        return [""] * min(7, n_cols)

    row_bbox = table.rows[row_index].bbox
    values: list[str] = []
    for col_index in range(min(7, n_cols)):
        bbox = (x_edges[col_index], row_bbox[1], x_edges[col_index + 1], row_bbox[3])
        text = page.crop(bbox).extract_text(x_tolerance=1, y_tolerance=3) or ""
        values.append(_clean(text))
    return values


def _is_section_header(row: list[str]) -> bool:
    """Return True if the entire row is just one section label (e.g. 'Section-A')."""
    non_empty = [v for v in row if v]
    if len(non_empty) == 1:
        val = non_empty[0].strip()
        if re.match(r"^section[\s\-]*[a-zA-Z]", val, re.IGNORECASE):
            return True
    return False


def _is_total_row(row: list[str]) -> bool:
    """Return True if the row is just a subtotal / grand-total row."""
    non_empty = [v for v in row if v]
    if len(non_empty) == 1:
        try:
            float(non_empty[0].replace(",", ""))
            return True
        except ValueError:
            pass
    return False


# Tokens used to detect leaked sub-header rows
_SUB_HEADER_TOKENS = {
    "220kv", "400kv", "bay no.", "bay no", "name of entity",
    "connectivity", "quantum (mw)", "quantum", "margins available",
    "bay-wise margins", "available (mw)",
}
_VOLTAGE_LABELS = {"220kv", "400kv", "220kv*", "400kv*"}


def _is_sub_header_row(row: list[str]) -> bool:
    """Return True if every non-empty cell is a sub-header / voltage label.

    Catches two kinds of leaked header rows that pdfplumber sometimes emits
    past the fixed HEADER_ROW_COUNT skip:
      1. The voltage row  ('220kV', '400kV', '220kV*', '400kV*', ...)
      2. The column-label row ('Bay No.', 'Name of Entity', ...)
    """
    non_empty = [v.strip().lower() for v in row if v.strip()]
    if not non_empty:
        return False
    if all(cell in _VOLTAGE_LABELS for cell in non_empty):
        return True
    return all(
        any(tok in cell for tok in _SUB_HEADER_TOKENS)
        for cell in non_empty
    )


def _is_section_str(v: str) -> bool:
    """Return True if *v* looks like a section label ('Section-A', etc.)."""
    return bool(re.match(r"^section[\s\-]*[a-zA-Z]", v.strip(), re.IGNORECASE))


def _section_label(row: list[str]) -> str:
    """Return the first section label found in the row."""
    for value in row:
        if _is_section_str(value):
            return value.strip()
    return ""


def _is_number_like(value: str) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value.replace(",", "").strip()))


def _split_entities(entity_text: str) -> list[dict]:
    """Split an entity cell into searchable entity parts.

    The PDF often stores multiple developers in one cell separated by semicolon
    or plus signs. The raw cell is still authoritative; this list helps matching
    without losing the original wording.
    """
    if not entity_text:
        return []

    text = _clean(entity_text)
    pieces = re.split(r";|\s+\+\s+(?=[A-Z])", text)
    entities: list[dict] = []
    for piece in pieces:
        part = _clean(piece.strip(" ;,+"))
        if not part:
            continue
        capacity_match = re.search(r"\(([^)]*?(?:MW|\d)[^)]*)\)", part, flags=re.IGNORECASE)
        name = _clean(re.sub(r"\([^)]*\)", "", part).strip(" ;,+"))
        entities.append({
            "name": name or part,
            "raw": part,
            "capacity_text": capacity_match.group(1) if capacity_match else "",
        })
    return entities


# ---------------------------------------------------------------------------
# Page-level gate
# ---------------------------------------------------------------------------

def page_passes_gate(text: str) -> bool:
    """Check that the page text contains all required bay-allocation keywords."""
    compact_text = _compact(text)
    lower_text = (text or "").lower()
    return all(
        kw.lower() in lower_text or _compact(kw) in compact_text
        for kw in REQUIRED_KEYWORDS
    )


# ---------------------------------------------------------------------------
# Helper: create an empty substation dict
# ---------------------------------------------------------------------------

def _new_substation(sl_no: str = "",
                    name: str = "",
                    coords: str = "",
                    region: str = "",
                    planned: str = "",
                    existing: str = "",
                    under_implementation: str = "") -> dict:
    """Return a fresh substation dict with empty allocation containers."""
    return {
        "sl_no":                                          sl_no,
        "name_of_substation":                             name,
        "substation_coordinates":                         coords,
        "region":                                         region,
        "transformation_capacity_planned_mva":            planned,
        "transformation_capacity_existing_mva":           existing,
        "transformation_capacity_under_implementation_mva": under_implementation,
        "allocations": [],
        "220kv": {
            "bay_no": {},
            "entries": [],
        },
        "400kv": {
            "bay_no": {},
            "entries": [],
        },
    }


def _merge_substation_header(sub: dict, row: list[str]) -> None:
    """Fill missing substation header fields from a newly detected header row."""
    field_map = {
        "sl_no": 0,
        "name_of_substation": 1,
        "substation_coordinates": 2,
        "region": 3,
        "transformation_capacity_planned_mva": 4,
        "transformation_capacity_existing_mva": 5,
        "transformation_capacity_under_implementation_mva": 6,
    }
    for key, idx in field_map.items():
        if row[idx] and not sub.get(key):
            sub[key] = row[idx]


def _allocation_entry(
    *,
    page_number: int,
    table_row_index: int,
    section: str,
    voltage_key: str,
    bay_no: str,
    quantum: str,
    entity: str,
    margin_bay_no: str,
    margin_available: str,
    substation: dict,
) -> dict:
    voltage_label = "220 kV" if voltage_key == "220kv" else "400 kV"
    return {
        "page_number": page_number,
        "table_row_index": table_row_index,
        "section": section,
        "voltage": voltage_label,
        "voltage_key": voltage_key,
        "bay_no": bay_no,
        "connectivity_quantum_mw": quantum,
        "name_of_entity": entity,
        "entities": _split_entities(entity),
        "margin_bay_no": margin_bay_no,
        "margin_available_mw": margin_available,
        "name_of_substation": substation.get("name_of_substation", ""),
        "substation_coordinates": substation.get("substation_coordinates", ""),
        "region": substation.get("region", ""),
        "sl_no": substation.get("sl_no", ""),
        "search_fields": {
            "entity": entity,
            "substation": substation.get("name_of_substation", ""),
            "coordinates": substation.get("substation_coordinates", ""),
            "voltage": voltage_label,
            "connectivity_quantum_mw": quantum,
            "bay_no": bay_no,
        },
    }


def _row_value(row: dict, *keys: str) -> str:
    """Read a value from an LLM row using case/spacing-insensitive keys."""
    if not isinstance(row, dict):
        return ""
    compact_lookup = {_compact(str(k)): v for k, v in row.items()}
    for key in keys:
        if key in row:
            return _clean(row.get(key))
        compact_key = _compact(key)
        if compact_key in compact_lookup:
            return _clean(compact_lookup[compact_key])
    return ""


def _normalise_llm_voltage(row: dict) -> str:
    voltage = _row_value(row, "voltage_key", "voltage", "Voltage Level")
    compact = _compact(voltage)
    if "400" in compact:
        return "400kv"
    if "220" in compact:
        return "220kv"
    return ""


def _iter_llm_rows(result: Any) -> list[dict]:
    """Accept either {"rows": [...]} or {"substations": [...]} LLM JSON."""
    if isinstance(result, list):
        candidates = result
    elif isinstance(result, dict):
        if isinstance(result.get("rows"), list):
            candidates = result["rows"]
        elif isinstance(result.get("substations"), list):
            candidates = []
            for sub in result["substations"]:
                if not isinstance(sub, dict):
                    continue
                allocations = sub.get("allocations")
                if not isinstance(allocations, list):
                    continue
                for alloc in allocations:
                    if isinstance(alloc, dict):
                        merged = dict(sub)
                        merged.update(alloc)
                        candidates.append(merged)
        else:
            candidates = next((v for v in result.values() if isinstance(v, list)), [])
    else:
        candidates = []

    return [row for row in candidates if isinstance(row, dict)]


def _substations_from_llm_rows(rows: list[dict], page_number: int) -> tuple[list[dict], list[dict]]:
    """Convert flat LLM allocation rows to the existing Bay JSON schema."""
    substations: list[dict] = []
    table_rows: list[dict] = []
    sub_index: dict[tuple[str, str, str], dict] = {}

    for row_index, row in enumerate(rows, 1):
        voltage_key = _normalise_llm_voltage(row)
        bay_no = _row_value(row, "bay_no", "Bay No")
        entity = _row_value(row, "name_of_entity", "Name of Entity")
        quantum = _row_value(
            row,
            "connectivity_quantum_mw",
            "connectivity_quantum",
            "Connectivity Quantum (MW)",
        )
        if voltage_key not in {"220kv", "400kv"} or not any([bay_no, entity, quantum]):
            continue

        sl_no = _row_value(row, "sl_no", "Sl. No.", "Serial No")
        substation_name = _row_value(row, "name_of_substation", "Name of Substation")
        coords = _row_value(row, "substation_coordinates", "Substation Coordinates")
        region = _row_value(row, "region")
        section = _row_value(row, "section")

        sub_key = (sl_no, substation_name, coords)
        if sub_key not in sub_index:
            sub_index[sub_key] = _new_substation(
                sl_no=sl_no,
                name=substation_name,
                coords=coords,
                region=region,
                planned=_row_value(row, "transformation_capacity_planned_mva"),
                existing=_row_value(row, "transformation_capacity_existing_mva"),
                under_implementation=_row_value(row, "transformation_capacity_under_implementation_mva"),
            )
            substations.append(sub_index[sub_key])

        sub = sub_index[sub_key]
        if not sub.get("space_provision_220kv"):
            sub["space_provision_220kv"] = _row_value(row, "space_provision_220kv")
        if not sub.get("space_provision_400kv"):
            sub["space_provision_400kv"] = _row_value(row, "space_provision_400kv")
        if not sub.get("remarks"):
            sub["remarks"] = _row_value(row, "remarks")
        margin_bay_no = _row_value(row, "margin_bay_no")
        margin_available = _row_value(row, "margin_available_mw")
        entry = _allocation_entry(
            page_number=page_number,
            table_row_index=row_index,
            section=section,
            voltage_key=voltage_key,
            bay_no=bay_no,
            quantum=quantum,
            entity=entity,
            margin_bay_no=margin_bay_no,
            margin_available=margin_available,
            substation=sub,
        )
        sub[voltage_key]["bay_no"][bay_no] = entity
        sub[voltage_key]["entries"].append(entry)
        sub["allocations"].append(entry)

        norm = [""] * len(COLUMN_NAMES)
        norm[0] = sub.get("sl_no", "")
        norm[1] = sub.get("name_of_substation", "")
        norm[2] = sub.get("substation_coordinates", "")
        norm[3] = sub.get("region", "")
        norm[4] = sub.get("transformation_capacity_planned_mva", "")
        norm[5] = sub.get("transformation_capacity_existing_mva", "")
        norm[6] = sub.get("transformation_capacity_under_implementation_mva", "")
        norm[17] = sub.get("space_provision_220kv", "")
        norm[18] = sub.get("space_provision_400kv", "")
        norm[19] = sub.get("remarks", "")
        if voltage_key == "220kv":
            norm[7], norm[8], norm[9] = bay_no, quantum, entity
            norm[13], norm[14] = margin_bay_no, margin_available
        else:
            norm[10], norm[11], norm[12] = bay_no, quantum, entity
            norm[15], norm[16] = margin_bay_no, margin_available
        table_rows.append(
            _table_row_record(
                page_number=page_number,
                table_row_index=row_index,
                section=section,
                row=norm,
                substation=sub,
            )
        )

    return substations, table_rows


def _table_row_record(
    *,
    page_number: int,
    table_row_index: int,
    section: str,
    row: list[str],
    substation: dict,
) -> dict:
    record = {
        "page_number": page_number,
        "table_row_index": table_row_index,
        "section": section,
    }
    for idx, column_name in enumerate(COLUMN_NAMES):
        record[column_name] = row[idx] if idx < len(row) else ""

    # Make every row directly searchable even if the PDF used row-spans.
    for key in (
        "sl_no",
        "name_of_substation",
        "substation_coordinates",
        "region",
        "transformation_capacity_planned_mva",
        "transformation_capacity_existing_mva",
        "transformation_capacity_under_implementation_mva",
    ):
        if not record.get(key):
            record[key] = substation.get(key, "")
    return record


# ---------------------------------------------------------------------------
# Core extraction:  page  ->  list of substations
# ---------------------------------------------------------------------------

def _camelot_extract_tables(pdf_path: str, page_number: int) -> tuple[list, str]:
    """Primary: extract tables from a page using Camelot (lattice first, stream).

    Returns (camelot_table_objects, flavor_used).
    """
    if not _HAS_CAMELOT:
        return [], ""

    for flavor in ('lattice', 'stream'):
        try:
            tables = camelot.read_pdf(
                pdf_path, pages=str(page_number), flavor=flavor,
                suppress_stdout=True,
            )
            if tables and tables.n:
                return tables, flavor
        except Exception:
            pass
    return [], ""


def _camelot_to_raw_rows(camelot_tables) -> list[list[str]]:
    """Convert camelot table objects to flat list of row-lists."""
    all_rows: list[list[str]] = []
    for tbl in camelot_tables:
        for _, row in tbl.df.iterrows():
            all_rows.append([_clean(str(v)) if v else "" for v in row.values])
    return all_rows


def _rows_to_table_text(rows: list[list[str]], label: str = "table 1") -> str:
    """Render already-extracted table rows as compact tab-separated LLM input."""
    chunks = [f"[{label}]"]
    for row in rows or []:
        chunks.append("\t".join(_clean(cell) for cell in (row or [])))
    return "\n".join(chunks)[:50000]


def _camelot_page_text(
    pdf_path: str,
    page_number: int,
    camelot_tables,
    flavor: str,
) -> str:
    """Render one page's Camelot tables to the saved text format used by LLM."""
    rows = _camelot_to_raw_rows(camelot_tables)
    lines = [
        "=" * 60,
        f"PDF: {Path(pdf_path).name}",
        f"PAGE {page_number} — CAMELOT {flavor or 'unknown'}",
        "=" * 60,
        _rows_to_table_text(rows, f"camelot {flavor or 'unknown'}"),
    ]
    return "\n".join(lines).strip()[:50000]


def _page_table_text(page) -> str:
    """Render pdfplumber table cells as compact tab-separated text for fallback LLM input."""
    chunks: list[str] = []
    try:
        tables = page.extract_tables() or []
    except Exception:
        tables = []
    for table_index, table in enumerate(tables, 1):
        chunks.append(f"[table {table_index}]")
        for row in table or []:
            chunks.append("\t".join(_clean(cell) for cell in (row or [])))
    text = "\n".join(chunks)
    return text[:50000]


def llm_extract_page_data(
    page_text: str,
    table_text: str,
    page_number: int,
    runtime,
    pdf_name: str = "",
) -> Optional[dict]:
    """Extract one Bay Allocation page with the configured LLM runtime."""
    if runtime is None or (not getattr(runtime, "vm_mode", False) and not getattr(runtime, "api_key", "")):
        print(f"      [page {page_number}] Bay LLM not configured — using table parser fallback")
        return None

    try:
        from llm_client import call_llm, extract_text_from_response
    except Exception as exc:
        print(f"      [page {page_number}] Bay LLM unavailable: {exc} — using fallback")
        return None

    prompt = {
        "messages": [
            {"role": "system", "content": BAY_LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": BAY_LLM_USER_TEMPLATE.format(
                    page_number=page_number,
                    page_text=(page_text or "")[:50000],
                    table_text=table_text or "(no table cells extracted)",
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 8000,
    }

    print(f"      [page {page_number}] Sending Bay Allocation page to LLM …")
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
            totals = record_llm_token_usage(
                "bayallocation",
                prompt,
                resp,
                content,
                pdf_name=pdf_name,
                page_number=page_number,
                purpose="page_allocation_extraction",
                model=MODEL,
            )
            total_display = totals["total_tokens"] + totals["estimated_total_tokens"]
            rows = _iter_llm_rows(parse_json(content))
            substations, table_rows = _substations_from_llm_rows(rows, page_number)
            if not substations:
                print(f"      [page {page_number}] Bay LLM returned 0 usable rows (tokens total: {total_display})")
                return None

            print(
                f"      [page {page_number}] Bay LLM extracted "
                f"{sum(len(s.get('allocations', [])) for s in substations)} rows "
                f"across {len(substations)} substations (tokens total: {total_display})"
            )
            return {
                "page_number": page_number,
                "raw_text": page_text or "",
                "columns": COLUMN_NAMES,
                "table_rows": table_rows,
                "substations": substations,
                "extraction_method": "llm",
            }
        except Exception as exc:
            if attempt < 2:
                print(f"      [page {page_number}] Bay LLM attempt {attempt + 1} failed, retrying …")
                logger.warning(
                    "[BayAllocation] page=%d llm retry attempt=%d error=%s",
                    page_number,
                    attempt + 1,
                    exc,
                )
                time.sleep(5)
            else:
                print(f"      [page {page_number}] Bay LLM failed: {exc} — using fallback")
                logger.error("[BayAllocation] page=%d llm failed error=%s", page_number, exc)
    return None


def _image_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def extract_bayallocation_image(
    image_path: str | Path,
    page_number: int,
    runtime,
) -> Optional[dict]:
    """Extract one Bay Allocation page image using LLM vision."""
    path = Path(image_path)
    if runtime is None or (not getattr(runtime, "vm_mode", False) and not getattr(runtime, "api_key", "")):
        print(f"      [page {page_number}] Bay image LLM not configured — image not extracted")
        return None

    try:
        from llm_client import call_llm, extract_text_from_response
    except Exception as exc:
        print(f"      [page {page_number}] Bay image LLM unavailable: {exc}")
        return None

    prompt = {
        "messages": [
            {"role": "system", "content": BAY_LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": BAY_IMAGE_LLM_USER_TEMPLATE.format(
                            page_number=page_number,
                            image_name=path.name,
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(path),
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 10000,
    }

    print(f"      [page {page_number}] Sending Bay Allocation image to LLM …")
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
            totals = record_llm_token_usage(
                "bayallocation",
                prompt,
                resp,
                content,
                pdf_name=path.name,
                page_number=page_number,
                purpose="page_image_allocation_extraction",
                model=MODEL,
            )
            total_display = totals["total_tokens"] + totals["estimated_total_tokens"]
            rows = _iter_llm_rows(parse_json(content))
            substations, table_rows = _substations_from_llm_rows(rows, page_number)
            if not substations:
                print(f"      [page {page_number}] Bay image LLM returned 0 usable rows (tokens total: {total_display})")
                return None

            print(
                f"      [page {page_number}] Bay image LLM extracted "
                f"{sum(len(s.get('allocations', [])) for s in substations)} rows "
                f"across {len(substations)} substations (tokens total: {total_display})"
            )
            return {
                "page_number": page_number,
                "raw_text": "",
                "columns": COLUMN_NAMES,
                "table_rows": table_rows,
                "substations": substations,
                "extraction_method": "llm_image",
            }
        except Exception as exc:
            if attempt < 2:
                print(f"      [page {page_number}] Bay image LLM attempt {attempt + 1} failed, retrying …")
                logger.warning(
                    "[BayAllocation] image=%s page=%d llm retry attempt=%d error=%s",
                    path.name,
                    page_number,
                    attempt + 1,
                    exc,
                )
                time.sleep(5)
            else:
                print(f"      [page {page_number}] Bay image LLM failed: {exc}")
                logger.error("[BayAllocation] image=%s page=%d llm failed error=%s", path.name, page_number, exc)
    return None


def extract_page_data(page, page_number: int, pdf_path: str = "", runtime=None) -> Optional[dict]:
    """Extract all substations from one page.

    Extracts table rows with Camelot page-wise first, then sends those table
    rows to the configured LLM. If the LLM is unavailable or returns no usable
    rows, falls back to local Camelot/pdfplumber table parsing.
    Each unique substation (identified by sl_no appearing in column 0)
    becomes **exactly one item** in the returned ``substations`` list.
    Each bay number is mapped to its entity name (or empty string) in
    the ``bay_no`` dict under the ``220kv`` and ``400kv`` keys.

    Returns None if no allocation table is found on this page.
    """
    page_text = page.extract_text() or ""

    target = None
    target_table = None  # pdfplumber table object (for cell-level extraction)
    extraction_method = ""
    use_camelot_rows = False
    camelot_rows: list[list[str]] = []
    table_text = ""

    # ── Primary: Camelot table extraction → .txt dump → LLM row extraction ─
    if pdf_path and _HAS_CAMELOT:
        camelot_tables, flavor = _camelot_extract_tables(pdf_path, page_number)
        if camelot_tables:
            raw_rows = _camelot_to_raw_rows(camelot_tables)
            if raw_rows:
                table_text = _camelot_page_text(pdf_path, page_number, camelot_tables, flavor)
                saved_path = _save_page_text(pdf_path, page_number, table_text)
                txt_text = saved_path.read_text(encoding="utf-8")
                print(
                    f"  + Page {page_number:3d} camelot text saved "
                    f"({len(txt_text)} chars) -> {saved_path}"
                )
                if page_passes_gate(txt_text):
                    llm_result = llm_extract_page_data(
                        "",
                        txt_text,
                        page_number,
                        runtime,
                        pdf_name=Path(pdf_path).name if pdf_path else "",
                    )
                    if llm_result is not None:
                        llm_result["raw_text"] = txt_text
                        llm_result["page_text_file"] = str(saved_path)
                        llm_result["extraction_method"] = f"camelot_{flavor}+llm"
                        return llm_result
                else:
                    print(f"  o Page {page_number:3d} -- LLM skipped (saved txt keyword gate)")

            if _is_target_table(raw_rows):
                camelot_rows = raw_rows
                target = raw_rows
                use_camelot_rows = True
                extraction_method = f"camelot_{flavor}"
                print(f"  + Page {page_number:3d} camelot found {len(camelot_tables)} table(s) via {flavor}")

    # ── Fallback: pdfplumber ──────────────────────────────────────────────
    if target is None:
        if extraction_method == "":
            print(f"    Page {page_number:3d} camelot found 0 tables, trying pdfplumber")
        table_objects = page.find_tables()
        for tbl in table_objects:
            extracted = tbl.extract()
            if _is_target_table(extracted):
                target_table = tbl
                target = extracted
                extraction_method = "pdfplumber_fallback"
                table_text = _rows_to_table_text(extracted, "pdfplumber fallback")
                saved_path = _save_page_text(pdf_path, page_number, table_text) if pdf_path else None
                txt_text = (
                    saved_path.read_text(encoding="utf-8")
                    if saved_path
                    else table_text or _page_table_text(page)
                )
                if saved_path:
                    print(
                        f"  + Page {page_number:3d} pdfplumber text saved "
                        f"({len(txt_text)} chars) -> {saved_path}"
                    )
                if page_passes_gate(txt_text):
                    llm_result = llm_extract_page_data(
                        "",
                        txt_text,
                        page_number,
                        runtime,
                        pdf_name=Path(pdf_path).name if pdf_path else "",
                    )
                    if llm_result is not None:
                        llm_result["raw_text"] = txt_text
                        if saved_path:
                            llm_result["page_text_file"] = str(saved_path)
                        llm_result["extraction_method"] = "pdfplumber_fallback+llm"
                        return llm_result
                else:
                    print(f"  o Page {page_number:3d} -- LLM skipped (saved txt keyword gate)")
                break

    if target is None:
        return None

    n_cols = len(COLUMN_NAMES)

    substations: list[dict] = []
    current_sub: dict | None = None
    table_rows: list[dict] = []
    current_section = ""

    for table_row_index in range(HEADER_ROW_COUNT, len(target) if use_camelot_rows else len(target_table.rows)):
        if use_camelot_rows:
            # Camelot path: rows are already clean string lists
            norm = _normalise_row(target[table_row_index], n_cols)
        else:
            # pdfplumber fallback path: extract from cell bboxes
            raw_row = _extract_row_from_cells(page, target_table, table_row_index, n_cols)
            norm = _normalise_row(raw_row, n_cols)

            # Recover left fixed columns for rows where pdfplumber drops rowspans.
            left_values = _extract_left_column_values(page, target_table, table_row_index, n_cols)
            for idx, value in enumerate(left_values):
                if value and (idx <= 3 or not norm[idx]):
                    norm[idx] = value

        # ── Skip noise rows ────────────────────────────────────────────────
        if not any(norm):
            continue
        if _is_sub_header_row(norm):
            continue

        section = _section_label(norm)
        if section:
            current_section = section

        # Pure section rows carry no allocation data but define context.
        if _is_section_header(norm):
            continue
        if _is_total_row(norm):
            continue

        # ── Unpack only the columns we care about ──────────────────────────
        sl_no           = norm[0]
        substation_name = norm[1]
        coordinates     = norm[2]
        region          = norm[3]
        planned         = norm[4]
        existing        = norm[5]
        under_impl      = norm[6]

        bay_no_220      = norm[7]
        quantum_220     = norm[8]
        entity_220      = norm[9]
        bay_no_400      = norm[10]
        quantum_400     = norm[11]
        entity_400      = norm[12]
        margin_bay_220  = norm[13]
        margin_220      = norm[14]
        margin_bay_400  = norm[15]
        margin_400      = norm[16]

        if _is_section_str(bay_no_220) and not any([entity_220, bay_no_400, entity_400]):
            continue
        if _is_section_str(bay_no_400) and not any([entity_400, bay_no_220, entity_220]):
            continue

        # ── New substation header row (identified by sl_no) ────────────────
        starts_substation = _is_number_like(sl_no) or (substation_name and coordinates)
        if starts_substation:
            # Flush the previous substation before starting a new one
            if current_sub is not None:
                substations.append(current_sub)

            current_sub = _new_substation(
                sl_no=sl_no,
                name=substation_name,
                coords=coordinates,
                region=region,
                planned=planned,
                existing=existing,
                under_implementation=under_impl,
            )
        elif current_sub is not None:
            _merge_substation_header(current_sub, norm)

        # ── Collect bay data into current substation's lists ───────────────
        has_bay_data = any([
            bay_no_220, quantum_220, entity_220,
            bay_no_400, quantum_400, entity_400,
            margin_bay_220, margin_220, margin_bay_400, margin_400,
        ])
        if not has_bay_data:
            continue

        # If a bay row appears before any substation header, create
        # an anonymous placeholder so no data is lost.
        if current_sub is None:
            current_sub = _new_substation()

        table_rows.append(
            _table_row_record(
                page_number=page_number,
                table_row_index=table_row_index,
                section=current_section,
                row=norm,
                substation=current_sub,
            )
        )

        if bay_no_220 and not _is_section_str(bay_no_220):
            current_sub["220kv"]["bay_no"][bay_no_220] = entity_220
            entry = _allocation_entry(
                page_number=page_number,
                table_row_index=table_row_index,
                section=current_section,
                voltage_key="220kv",
                bay_no=bay_no_220,
                quantum=quantum_220,
                entity=entity_220,
                margin_bay_no=margin_bay_220,
                margin_available=margin_220,
                substation=current_sub,
            )
            current_sub["220kv"]["entries"].append(entry)
            current_sub["allocations"].append(entry)

        if bay_no_400 and not _is_section_str(bay_no_400):
            current_sub["400kv"]["bay_no"][bay_no_400] = entity_400
            entry = _allocation_entry(
                page_number=page_number,
                table_row_index=table_row_index,
                section=current_section,
                voltage_key="400kv",
                bay_no=bay_no_400,
                quantum=quantum_400,
                entity=entity_400,
                margin_bay_no=margin_bay_400,
                margin_available=margin_400,
                substation=current_sub,
            )
            current_sub["400kv"]["entries"].append(entry)
            current_sub["allocations"].append(entry)

    # Flush the last substation
    if current_sub is not None:
        substations.append(current_sub)

    if not substations:
        return None

    return {
        "page_number":  page_number,
        "raw_text":     page_text,
        "columns":      COLUMN_NAMES,
        "table_rows":   table_rows,
        "substations":  substations,
        "extraction_method": extraction_method,
    }


# ---------------------------------------------------------------------------
# Single-PDF extraction
# ---------------------------------------------------------------------------

def extract_bayallocation_pdf(pdf_path: str, max_pages: int = -1, runtime=None) -> list[dict]:
    """Extract all pages from one Bay Allocation PDF.

    Uses Camelot page-wise first, sends the extracted table text to the LLM,
    and falls back to local Camelot/pdfplumber table parsing if needed.

    Parameters
    ----------
    max_pages : int
        Maximum number of pages to scan per PDF. -1 means all pages.

    Each page is treated as one independent extraction unit.

    Returns
    -------
    list[dict]
        One element per page that contains a valid allocation table.
        Each element has 'page_number', 'raw_text', and 'substations'
        (a list of substation dicts with aggregated 220kV/400kV lists).
    """
    all_pages: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        limit = total if max_pages == -1 else min(max_pages, total)
        label = "all" if max_pages == -1 else f"first {limit} of"
        print(f"  [BayAllocation] {total} pages ({label}) -- scanning ...")
        print(f"      Page txt dumps -> {_page_text_output_dir(pdf_path)}")

        for i in range(limit):
            page = pdf.pages[i]
            page_number = i + 1

            result = extract_page_data(page, page_number, pdf_path=pdf_path, runtime=runtime)
            if result is None:
                print(f"  o Page {page_number:3d} -- no allocation table found")
                continue

            sub_count = len(result["substations"])
            print(f"  + Page {page_number:3d} -> {sub_count} substations")
            all_pages.append(result)

    return all_pages
