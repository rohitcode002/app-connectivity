"""
effectiveness_handler/extraction.py — PDF extraction via Camelot
=================================================================
Uses Camelot to directly extract well-structured effectiveness tables
from PDFs. No LLM needed — these tables are clean and reliably parsed
by Camelot's lattice/stream detection.

Edit this file to change how data is extracted from effectiveness PDFs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import camelot
from pypdf import PdfReader

from pipeline.effectiveness_handler.models import RERecord, safe_record


# ── Header mapping: PDF column names → RERecord field names ──────────────────

_HEADER_MAP: dict[str, str] = {
    "si no": "sl_no", "sl. no.": "sl_no", "sl no": "sl_no",
    "sl.no": "sl_no", "sl.no.": "sl_no", "s.no": "sl_no",
    "s.no.": "sl_no", "s. no": "sl_no", "s. no.": "sl_no",
    "application id": "application_id",
    "application no": "application_id",
    "application no.": "application_id",
    "name of applicant": "name_of_applicant",
    "name of the applicant": "name_of_applicant",
    "applicant": "name_of_applicant",
    "region": "region",
    "type of project": "type_of_project",
    "type": "type_of_project",
    "installed capacity (mw)": "installed_capacity_mw",
    "installed capacity": "installed_capacity_mw",
    "total capacity": "installed_capacity_mw",
    "solar": "solar_mw", "wind": "wind_mw", "ess": "ess_mw",
    "bess": "ess_mw", "hydro": "hydro_mw",
    "connectivity (mw)": "connectivity_mw", "connectivity": "connectivity_mw",
    "connectivity quantum": "connectivity_mw",
    "connectivity quantum (mw)": "connectivity_mw",
    "present connectivity /deemed gna": "present_connectivity_mw",
    "present connectivity/deemed gna": "present_connectivity_mw",
    "present connectivity": "present_connectivity_mw",
    "deemed gna": "present_connectivity_mw",
    "substation": "substation",
    "pooling station": "substation",
    "state": "state",
    "expected date of connectivity/ gna to be made effective": "expected_date",
    "expected date of connectivity/gna to be made effective": "expected_date",
    "expected date of connectivity/ gna to be  made effective": "expected_date",
    "expected date": "expected_date",
    "effective date": "expected_date",
    "date of effectiveness": "expected_date",
}


def _clean(text) -> str:
    """Collapse whitespace and strip."""
    if text is None:
        return ""
    return " ".join(str(text).split())


def _map_headers(raw: list) -> dict[int, str]:
    """Map column indices to RERecord field names by matching header text."""
    mapping: dict[int, str] = {}
    for i, h in enumerate(raw):
        key = re.sub(r"\s+", " ", (h or "").lower().strip())
        if key in _HEADER_MAP:
            mapping[i] = _HEADER_MAP[key]
        else:
            # Fuzzy substring match
            for pat, field in _HEADER_MAP.items():
                if key and pat in key:
                    mapping[i] = field
                    break
    return mapping


def _is_header_row(row: list) -> bool:
    """Return True if the row looks like a table header."""
    text = " ".join(str(c or "") for c in row).lower()
    return ("application" in text and ("name" in text or "applicant" in text)) or \
           ("sl. no" in text or "si no" in text or "s.no" in text or "sl.no" in text)


def _is_data_row(row: list, mapping: dict[int, str]) -> bool:
    """Return True if row has enough content to be a data row (not noise)."""
    if not mapping:
        return False
    # Must have at least application_id or name_of_applicant
    app_idx = next((i for i, f in mapping.items() if f == "application_id"), None)
    name_idx = next((i for i, f in mapping.items() if f == "name_of_applicant"), None)

    has_app = app_idx is not None and app_idx < len(row) and _clean(row[app_idx])
    has_name = name_idx is not None and name_idx < len(row) and _clean(row[name_idx])
    return has_app or has_name


# ── Main extraction function ─────────────────────────────────────────────────

def extract_effectiveness_pdf(
    pdf_path: str,
    source_name: str,
    max_pages: int = -1,
) -> list[RERecord]:
    """Extract all records from one effectiveness PDF using Camelot.

    Camelot reads tables directly — no LLM needed for these well-structured
    PDF tables. Tries lattice first, then stream as fallback.

    Parameters
    ----------
    pdf_path    : Path to the PDF file.
    source_name : Name of the source file (stored in each record).
    max_pages   : Maximum pages to process (-1 = all).

    Returns
    -------
    list[RERecord]
        Extracted effectiveness records.
    """
    # Determine total pages and limit
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    limit = total if max_pages == -1 else min(max_pages, total)
    page_str = f"1-{limit}" if limit > 1 else "1"

    print(f"  [Effectiveness] {total} pages — extracting {page_str} with Camelot")

    # ── Extract tables with Camelot (lattice first, stream fallback) ──────
    all_tables = []
    flavor_used = ""

    for flavor in ('lattice', 'stream'):
        try:
            tables = camelot.read_pdf(
                pdf_path, pages=page_str, flavor=flavor,
                suppress_stdout=True,
            )
            if tables and tables.n:
                all_tables = list(tables)
                flavor_used = flavor
                print(f"  [Effectiveness] Camelot {flavor}: {len(all_tables)} table(s) found")
                break
        except Exception as exc:
            print(f"  [Effectiveness] Camelot {flavor} failed: {exc}")

    if not all_tables:
        print(f"  [Effectiveness] No tables found in {Path(pdf_path).name}")
        return []

    # ── Parse tables into records ─────────────────────────────────────────
    records: list[RERecord] = []
    mapping: dict[int, str] = {}

    for table_idx, table in enumerate(all_tables, 1):
        acc = table.parsing_report.get("accuracy", "n/a")
        row_count = len(table.df)
        print(f"    Table {table_idx}: {row_count} rows (accuracy: {acc}, {flavor_used})")

        for _, df_row in table.df.iterrows():
            row = [str(v) if v else None for v in df_row.values]
            if not row or all(c is None for c in row):
                continue

            # Detect header row and build column mapping
            if _is_header_row(row):
                mapping = _map_headers(row)
                if mapping:
                    mapped_fields = list(mapping.values())
                    print(f"      Header detected → {len(mapping)} columns mapped: {mapped_fields}")
                continue

            # Skip rows before we have a header mapping
            if not mapping:
                continue

            # Skip non-data rows
            if not _is_data_row(row, mapping):
                continue

            # Build record dict from column mapping
            raw: dict = {}
            for col_idx, field_name in mapping.items():
                if col_idx < len(row):
                    raw[field_name] = _clean(row[col_idx]) or None
            raw["source_file"] = source_name

            rec = safe_record(raw)
            if rec:
                records.append(rec)

    print(f"  [Effectiveness] {len(records)} records extracted from {Path(pdf_path).name}")
    return records
