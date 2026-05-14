"""
cmets_handler/extraction.py — PDF reading & LLM extraction sub-layers
=======================================================================
Sub-layer A: PDF page text extraction (pdfplumber for text, camelot for tables)
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
import pdfplumber

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


# ── Helper: build enriched page text with camelot tables ──────────────────────

def _clean_multiline(text) -> str:
    if text is None:
        return ""
    return "\n".join(" ".join(line.split()) for line in str(text).splitlines() if line.strip())


def _camelot_table_text(pdf_path: str, page_number: int) -> str:
    """Extract table text from a page using camelot for LLM context."""
    try:
        tables = camelot.read_pdf(
            pdf_path, pages=str(page_number), flavor='lattice',
            suppress_stdout=True,
        )
        if not tables or not tables.n:
            tables = camelot.read_pdf(
                pdf_path, pages=str(page_number), flavor='stream',
                suppress_stdout=True,
            )
    except Exception:
        return ""

    if not tables:
        return ""

    rendered: list[str] = []
    for table_idx, table in enumerate(tables, 1):
        rows = []
        for _, row in table.df.iterrows():
            rows.append(" | ".join(_clean_multiline(str(v)) for v in row.values))
        if rows:
            rendered.append(f"Table {table_idx}:\n" + "\n".join(rows))
    return "\n\n".join(rendered)


# ── Sub-layer A: PDF page extraction ──────────────────────────────────────────

def extract_pages(pdf_path: str, max_pages: int = -1) -> list[dict]:
    """Read page text from *pdf_path* using pdfplumber for text + camelot for tables.

    Parameters
    ----------
    max_pages : int
        Maximum number of pages to process per PDF.
        -1 means process all pages.

    Returns a list of ``{"page_number": int, "text": str, "table_text": str}`` dicts.
    """
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        limit = total if max_pages == -1 else min(max_pages, total)
        label = "all" if max_pages == -1 else f"first {limit}"
        print(f"  [A] {total} pages total — processing {label}")
        for i in range(limit):
            text = pdf.pages[i].extract_text() or ""
            pages.append({"page_number": i + 1, "text": text})
    return pages


# ── Sub-layer C: LLM row extraction ──────────────────────────────────────────



def llm_extract_rows(
    page_text: str,
    active_fields: list[str],
    vm_mode: bool,
    api_key: Optional[str],
    llm_script_path: Optional[str],
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

        # Sub-layer B: regex gate
        passed, active_fields = page_passes_gate(text)
        if not passed:
            print(f"  [B] Page {pnum:>3}: SKIP")
            pages_skipped += 1
            continue

        print(f"  [B] Page {pnum:>3}: PASS ✓  fields={active_fields}")
        pages_passed += 1

        # Enrich page text with camelot table view for better LLM context
        table_text = _camelot_table_text(pdf_path, pnum)
        enriched_text = text
        if table_text:
            enriched_text = f"{text}\n\nTABLE CELL VIEW:\n{table_text}"

        # Sub-layer C: LLM extraction
        print(f"  [C] Page {pnum} ({len(enriched_text)} chars) → LLM …", end="", flush=True)
        raw_rows   = llm_extract_rows(enriched_text, active_fields, vm_mode, api_key, llm_script_path)
        print(f" {len(raw_rows)} raw")

        raw_rows   = dedup_dicts(raw_rows)
        validated  = validate_rows(raw_rows)
        normalized = normalize(validated)
        print(f"         → {len(normalized)} normalised rows")

        # Sub-layer V: Contextual voltage extraction (per row)
        # Primary: LLM-provided Voltage field + row cell scan (substation, location…)
        # Fallback: page-level voltage if row gives nothing
        page_voltage = extract_voltage_from_page(text)
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
