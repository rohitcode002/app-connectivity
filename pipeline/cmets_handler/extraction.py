"""
cmets_handler/extraction.py — PDF reading & LLM extraction sub-layers
=======================================================================
Sub-layer A: PDF page text extraction (pypdf for text, camelot for tables)
Sub-layer B: regex column-header gate (delegates to gate.py)
Sub-layer C: LLM row extraction (sends page text → GPT → parsed rows)

Edit this file to change how pages are read from PDFs or how the LLM
response is parsed.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import camelot
from pypdf import PdfReader

from config import MODEL
from llm_client import call_llm, extract_text_from_response
from pipeline.cmets_handler.prompts import SYSTEM_PROMPT, USER_TEMPLATE
from pipeline.cmets_handler.gate import page_passes_gate
from pipeline.cmets_handler.models import MappedRow, PageResult, PipelineResult
from pipeline.cmets_handler.normalization import validate_rows, normalize, dedup_dicts
from pipeline.cmets_handler.voltage_extractor import (
    extract_voltage_from_row,
    extract_voltage_from_page,
)
from pipeline.shared_utils import parse_json
from pipeline.token_usage import record_llm_token_usage


# ── Helper: route 5.2 application-number columns ─────────────────────────────

_APPLICATIONS_UNDER_52_RE = re.compile(
    r"\bApplications?\s+under\s+5\.?\s*2\s+received\b",
    re.IGNORECASE,
)


def _page_has_applications_under_52(page_text: str) -> bool:
    """Return True for CMETS tables headed 'Applications under 5.2 received'."""
    return bool(_APPLICATIONS_UNDER_52_RE.search(page_text or ""))


def _extract_numeric_ids(value: object) -> list[str]:
    """Extract application-like numeric IDs from an LLM cell value."""
    if value is None:
        return []
    return re.findall(r"\b\d{6,}\b", str(value))


def _dedup_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def route_applications_under_52_rows(raw_rows: list[dict], page_text: str) -> list[dict]:
    """Move Application No. IDs into the 5.2 column for 5.2-received pages.

    Some CMETS PDFs use the normal "Application No. & Date" table header under
    a section titled "Applications under 5.2 received". On those pages, the
    application number is an Enhancement 5.2/revision ID, not a GNA/ST-II ID.
    """
    if not _page_has_applications_under_52(page_text):
        return raw_rows

    routed: list[dict] = []
    id_source_keys = (
        "Application ID under Enhancement 5.2 or revision",
        "GNA/ST II Application ID",
        "Application No. & Date",
        "Application No.\n& Date",
        "Application/Submission Date",
    )

    for row in raw_rows:
        if not isinstance(row, dict):
            routed.append(row)
            continue

        patched = dict(row)
        ids: list[str] = []
        for key in id_source_keys:
            ids.extend(_extract_numeric_ids(patched.get(key)))
        ids = _dedup_preserve_order(ids)

        if ids:
            patched["Application ID under Enhancement 5.2 or revision"] = ", ".join(ids)
            patched["GNA/ST II Application ID"] = None

        routed.append(patched)
    return routed


# ── Helper: build enriched page text with camelot tables ──────────────────────

def _clean_multiline(text) -> str:
    if text is None:
        return ""
    return "\n".join(" ".join(line.split()) for line in str(text).splitlines() if line.strip())


def _rows_to_markdown(rows: list) -> str:
    """Render table rows using the same Markdown-table method as extract_test_main.py."""
    if not rows:
        return "(empty table)"

    cleaned = []
    for row in rows:
        cleaned.append([str(cell or "").replace("\n", " ").strip() for cell in row])

    col_count = max(len(row) for row in cleaned)
    for row in cleaned:
        while len(row) < col_count:
            row.append("")

    col_widths = [
        max(len(row[c]) for row in cleaned)
        for c in range(col_count)
    ]
    col_widths = [max(w, 3) for w in col_widths]

    def fmt_row(row):
        cells = [row[c].ljust(col_widths[c]) for c in range(col_count)]
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * col_widths[c] for c in range(col_count)) + " |"
    lines = [fmt_row(cleaned[0]), separator]
    lines.extend(fmt_row(row) for row in cleaned[1:])
    return "\n".join(lines)


def _camelot_table_text(pdf_path: str, page_number: int) -> str:
    """Extract table text from a page using Camelot Markdown tables for LLM context."""
    try:
        tables = camelot.read_pdf(
            pdf_path, pages=str(page_number), flavor="lattice",
            suppress_stdout=True,
        )
        if not tables or not tables.n:
            tables = camelot.read_pdf(
                pdf_path, pages=str(page_number), flavor="stream",
                suppress_stdout=True,
            )
    except Exception:
        return ""

    if not tables:
        return ""

    rendered: list[str] = []
    for table_idx, table in enumerate(tables, 1):
        acc = table.parsing_report.get("accuracy", "n/a")
        rows = [table.df.columns.tolist()] + table.df.values.tolist()
        rows = [[_clean_multiline(cell) for cell in row] for row in rows]
        rendered.append(
            f"{'=' * 60}\n"
            f"TABLE {table_idx}  (accuracy: {acc})\n"
            f"{'=' * 60}\n"
            f"{_rows_to_markdown(rows)}"
        )
    return "\n\n".join(rendered)


# ── Sub-layer A: PDF page extraction ──────────────────────────────────────────

def extract_pages(pdf_path: str, max_pages: int = -1) -> list[dict]:
    """Read page text from *pdf_path* using pypdf text + Camelot Markdown tables.

    Parameters
    ----------
    max_pages : int
        Maximum number of pages to process per PDF.
        -1 means process all pages.

    Returns a list of ``{"page_number": int, "text": str, "raw_text": str, "table_text": str}`` dicts.
    """
    pages = []
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    limit = total if max_pages == -1 else min(max_pages, total)
    label = "all" if max_pages == -1 else f"first {limit}"
    print(f"  [A] {total} pages total — processing {label}")
    for i in range(limit):
        pnum = i + 1
        text = reader.pages[i].extract_text() or ""
        table_text = _camelot_table_text(pdf_path, pnum)
        enriched_text = text
        if table_text:
            enriched_text = f"{text}\n\nCAMELOT TABLE VIEW:\n{table_text}"
        pages.append({
            "page_number": pnum,
            "text": enriched_text,
            "raw_text": text,
            "table_text": table_text,
        })
    return pages


# ── Conditional fields: only extract when detected in active_fields ───────────
# Map: gate column name → list of LLM JSON keys to blank when the column is absent.
# Add entries here for any column that should ONLY be extracted when its header
# is detected on the page.
CONDITIONAL_FIELDS: dict[str, list[str]] = {
    "Nature of Applicant": ["Nature of Applicant"],
}


def _blank_conditional_fields(
    raw_rows: list[dict], active_fields: list[str],
) -> list[dict]:
    """Set conditional field values to None when the column was not detected."""
    keys_to_blank: list[str] = []
    for gate_col, llm_keys in CONDITIONAL_FIELDS.items():
        if gate_col not in active_fields:
            keys_to_blank.extend(llm_keys)
    if not keys_to_blank:
        return raw_rows
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        for key in keys_to_blank:
            if key in row:
                row[key] = None
    return raw_rows


# ── Sub-layer C: LLM row extraction ──────────────────────────────────────────



def llm_extract_rows(
    page_text: str,
    active_fields: list[str],
    vm_mode: bool,
    api_key: Optional[str],
    llm_script_path: Optional[str],
    pdf_name: str = "",
    page_number: int | None = None,
) -> list[dict]:
    """Send a single page to the LLM and return raw row dicts."""
    prompt = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(
                active_fields=", ".join(active_fields),
                page_text=page_text,
            )},
        ],
        "temperature": 0,
        "max_tokens":  4000,
    }
    try:
        resp    = call_llm(prompt, vm=vm_mode, api_key=api_key, model=MODEL, script_path=llm_script_path)
        content = extract_text_from_response(resp)
        totals = record_llm_token_usage(
            "cmets",
            prompt,
            resp,
            content,
            pdf_name=pdf_name,
            page_number=page_number,
            purpose="page_row_extraction",
            model=MODEL,
        )
        total_display = totals["total_tokens"] + totals["estimated_total_tokens"]
        print(f" tokens_total={total_display}", end="", flush=True)
        result  = parse_json(content)
        rows    = result.get("rows", []) if isinstance(result, dict) else []
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        print(f"      [LLM error] {exc}")
        return []


# ── Combined: run all sub-layers for one PDF ──────────────────────────────────

def run_single_pdf(
    pdf_path: str,
    api_key: Optional[str],
    vm_mode: bool = False,
    llm_script_path: Optional[str] = None,
    max_pages: int = -1,
) -> PipelineResult:
    """Run sub-layers A→B→C for a single PDF and return PipelineResult."""
    pages         = extract_pages(pdf_path, max_pages=max_pages)
    results       = []
    pages_passed  = 0
    pages_skipped = 0

    for page in pages:
        pnum = page["page_number"]
        text = page["text"]
        raw_text = page.get("raw_text") or text

        # Sub-layer B: regex gate
        passed, active_fields = page_passes_gate(text)
        if not passed:
            print(f"  [B] Page {pnum:>3}: SKIP")
            pages_skipped += 1
            continue

        print(f"  [B] Page {pnum:>3}: PASS ✓  fields={active_fields}")
        pages_passed += 1

        # Sub-layer C: LLM extraction
        print(f"  [C] Page {pnum} ({len(text)} chars) → LLM …", end="", flush=True)
        raw_rows   = llm_extract_rows(
            text,
            active_fields,
            vm_mode,
            api_key,
            llm_script_path,
            pdf_name=pdf_path,
            page_number=pnum,
        )
        print(f" {len(raw_rows)} raw")

        # Blank out conditional fields not detected on this page
        raw_rows = _blank_conditional_fields(raw_rows, active_fields)
        raw_rows = route_applications_under_52_rows(raw_rows, text)
        raw_rows   = dedup_dicts(raw_rows)
        validated  = validate_rows(raw_rows)
        normalized = normalize(validated)
        print(f"         → {len(normalized)} normalised rows")

        # Sub-layer V: Contextual voltage extraction (per row)
        # Primary: LLM-provided Voltage field + row cell scan (substation, location…)
        # Fallback: page-level voltage if row gives nothing
        page_voltage = extract_voltage_from_page(raw_text)
        injected: list[MappedRow] = []
        for row in normalized:
            d = row.model_dump(by_alias=True)
            row_voltage = extract_voltage_from_row(d) or page_voltage
            d["Voltage"] = row_voltage
            injected.append(MappedRow.model_validate(d))
        normalized = injected

        if page_voltage:
            print(f"  [V] Page {pnum}: page-level voltage → {page_voltage}")

        results.append(PageResult(page_number=pnum, rows_found=len(normalized), rows=normalized))

    return PipelineResult(
        pdf_path=pdf_path,
        total_pages_extracted=len(pages),
        pages_passed_gate=pages_passed,
        pages_skipped=pages_skipped,
        total_rows=sum(r.rows_found for r in results),
        results=results,
    )
