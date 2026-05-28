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

import re
from pathlib import Path
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


_START_DIR = Path(__file__).resolve().parent.parent.parent
_CAMELOT_OUTPUT_ROOT = _START_DIR / "cmets_camelot"


# ── Helper: route 5.2 application-number columns ─────────────────────────────

_APPLICATIONS_UNDER_52_RE = re.compile(
    r"\bApplications?\s+under\s+5\.?\s*2\s+received\b",
    re.IGNORECASE,
)

# Also detect "under regulation 5.2 of GNA Regulations" in description text
_REGULATION_52_RE = re.compile(
    r"\bunder\s+regulation\s+5\.?\s*2\b",
    re.IGNORECASE,
)

# Also detect "Applications under 5.2" without "received"
_UNDER_52_SHORT_RE = re.compile(
    r"\bApplications?\s+under\s+5\.?\s*2\b",
    re.IGNORECASE,
)


def _page_has_applications_under_52(page_text: str) -> bool:
    """Return True for CMETS tables with 5.2/regulation 5.2 context.

    Detects:
      - 'Applications under 5.2 received' (section heading)
      - 'under regulation 5.2 of GNA Regulations' (description text)
      - 'Applications under 5.2' (shorter variant)
    """
    text = page_text or ""
    return bool(
        _APPLICATIONS_UNDER_52_RE.search(text)
        or _REGULATION_52_RE.search(text)
        or _UNDER_52_SHORT_RE.search(text)
    )


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
    """Move Application No. IDs into the correct columns for 5.2-received pages.

    On 5.2 pages, the "Application No. & Date" column contains the Enhancement
    5.2 ID (usually 22-prefix), while ST-II and LTA IDs may appear in their
    respective columns.  This function ensures:
      - 22-prefix IDs → Enhancement 5.2 column
      - 12/11-prefix IDs → GNA/ST II column
      - 04/41-prefix IDs → LTA column

    Also handles cases where IDs from "Application No. & Date" column were
    incorrectly placed in GNA/ST II by the LLM.
    """
    if not _page_has_applications_under_52(page_text):
        return raw_rows

    routed: list[dict] = []
    # All keys where the LLM might put application IDs
    id_source_keys = (
        "Application ID under Enhancement 5.2 or revision",
        "GNA/ST II Application ID",
        "Application No. & Date",
        "Application No.\n& Date",
        "Application/Submission Date",
        "LTA Application ID",
    )

    for row in raw_rows:
        if not isinstance(row, dict):
            routed.append(row)
            continue

        patched = dict(row)

        # Collect ALL numeric IDs from all possible source keys
        all_ids: list[str] = []
        for key in id_source_keys:
            all_ids.extend(_extract_numeric_ids(patched.get(key)))
        all_ids = _dedup_preserve_order(all_ids)

        if all_ids:
            # Route IDs by prefix
            enh_ids: list[str] = []     # 22-prefix → Enhancement 5.2
            gna_ids: list[str] = []     # 12/11-prefix → GNA/ST II
            lta_ids: list[str] = []     # 04/41-prefix → LTA

            for app_id in all_ids:
                if app_id.startswith("22"):
                    enh_ids.append(app_id)
                elif app_id.startswith(("12", "11")):
                    gna_ids.append(app_id)
                elif app_id.startswith(("04", "41")):
                    lta_ids.append(app_id)
                else:
                    # Unknown prefix — put in enhancement as default for 5.2 pages
                    enh_ids.append(app_id)

            # Set the routed values
            patched["Application ID under Enhancement 5.2 or revision"] = (
                ", ".join(enh_ids) if enh_ids else None
            )
            patched["GNA/ST II Application ID"] = (
                gna_ids[0] if gna_ids else None  # GNA takes first only
            )
            patched["LTA Application ID"] = (
                ", ".join(lta_ids) if lta_ids else patched.get("LTA Application ID")
            )

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


def _safe_path_part(value: str) -> str:
    value = re.sub(r"[^\w .()&+-]+", "_", value.strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "unknown"


def _region_name_from_pdf_path(pdf_path: str) -> str:
    path = Path(pdf_path).resolve()
    parts = path.parts
    lower_parts = [part.lower() for part in parts]

    if "minutes" in lower_parts:
        idx = lower_parts.index("minutes")
        if idx + 1 < len(parts) - 1:
            return _safe_path_part(parts[idx + 1])
        if idx > 0:
            return _safe_path_part(parts[idx - 1])

    return _safe_path_part(path.parent.name)


def _camelot_output_dir(pdf_path: str) -> Path:
    pdf = Path(pdf_path)
    return _CAMELOT_OUTPUT_ROOT / _region_name_from_pdf_path(pdf_path) / _safe_path_part(pdf.stem)


def _read_camelot_tables(pdf_path: str, page_number: int):
    for flavor in ("lattice", "stream"):
        try:
            tables = camelot.read_pdf(
                pdf_path,
                pages=str(page_number),
                flavor=flavor,
                suppress_stdout=True,
            )
        except Exception:
            continue
        if tables and tables.n:
            return tables, flavor
    return [], ""


def _render_camelot_page(pdf_path: str, page_number: int) -> tuple[str, int, str]:
    """Extract one page using Camelot and render tables as Markdown."""
    tables, flavor = _read_camelot_tables(pdf_path, page_number)
    lines: list[str] = []

    if not tables:
        lines.append(f"{'=' * 60}")
        lines.append(f"PAGE {page_number} — no tables detected")
        lines.append(f"{'=' * 60}")
        return "\n".join(lines), 0, flavor

    for table_idx, table in enumerate(tables, 1):
        acc = table.parsing_report.get("accuracy", "n/a")
        rows = [table.df.columns.tolist()] + table.df.values.tolist()
        rows = [[_clean_multiline(cell) for cell in row] for row in rows]
        lines.append(f"{'=' * 60}")
        lines.append(f"PAGE {page_number} — TABLE {table_idx}  (accuracy: {acc}, flavor: {flavor})")
        lines.append(f"{'=' * 60}")
        lines.append(_rows_to_markdown(rows))
        lines.append("")

    return "\n".join(lines).rstrip(), len(tables), flavor


def _save_camelot_page_text(pdf_path: str, page_number: int, page_text: str) -> Path:
    dest = _camelot_output_dir(pdf_path)
    dest.mkdir(parents=True, exist_ok=True)
    out_file = dest / f"page {page_number}.txt"
    out_file.write_text(page_text, encoding="utf-8")
    return out_file


def _ensure_nature_field(active_fields: list[str]) -> list[str]:
    if "Nature of Applicant" in active_fields:
        return active_fields
    return [*active_fields, "Nature of Applicant"]


def _backfill_nature_of_applicant(raw_rows: list[dict], page_text: str) -> list[dict]:
    """Fill missing Nature of Applicant from visible page/neighbor values."""
    known_values = [
        str(row.get("Nature of Applicant")).strip()
        for row in raw_rows
        if isinstance(row, dict) and str(row.get("Nature of Applicant") or "").strip()
    ]

    page_matches = re.findall(
        r"\b(?:Generating station(?:\(s\))?, including REGS(?:\(s\))?, (?:with|without) ESS"
        r"(?: through a lead generator)?|Generator(?: with ESS|\s*\((?:Hybrid|Wind|Solar)\))?|"
        r"Standalone ESS|Renewable Power Park Developer|Renewable Power Park developer|"
        r"Captive generating plant|Pumped Storage)\b",
        page_text,
        flags=re.IGNORECASE,
    )
    known_values.extend(match.strip() for match in page_matches if match.strip())

    fallback = None
    unique = []
    seen = set()
    for value in known_values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    if len(unique) == 1:
        fallback = unique[0]

    last_seen = fallback
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        current = str(row.get("Nature of Applicant") or "").strip()
        if current:
            last_seen = current
            continue
        if last_seen:
            row["Nature of Applicant"] = last_seen
    return raw_rows


# ── Conditional fields: only extract when detected in active_fields ───────────
# Map: gate column name → list of LLM JSON keys to blank when the column is absent.
# Add entries here for any column that should ONLY be extracted when its header
# is detected on the page.
CONDITIONAL_FIELDS: dict[str, list[str]] = {
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


# ── Combined: run all sub-layers for one PDF (page-at-a-time) ─────────────────

def run_single_pdf(
    pdf_path: str,
    api_key: Optional[str],
    vm_mode: bool = False,
    llm_script_path: Optional[str] = None,
    max_pages: int = -1,
) -> PipelineResult:
    """Run sub-layers A→B→C for a single PDF and return PipelineResult.

    Flow per page (sequential):
        PDF → extract page N (Camelot) → save .txt → gate check → LLM extract
    """
    reader = PdfReader(pdf_path)
    total  = len(reader.pages)
    limit  = total if max_pages == -1 else min(max_pages, total)
    label  = "all" if max_pages == -1 else f"first {limit}"
    out_dir = _camelot_output_dir(pdf_path)

    print(f"  [A] {total} pages total — processing {label} with Camelot")
    print(f"      Camelot page dumps → {out_dir}")

    results:       list[PageResult] = []
    pages_passed  = 0
    pages_skipped = 0

    for i in range(limit):
        pnum = i + 1

        # ── Step 1: Extract page with Camelot ─────────────────────────────
        table_text, table_count, flavor = _render_camelot_page(pdf_path, pnum)

        # ── Step 2: Save .txt ─────────────────────────────────────────────
        saved_path = _save_camelot_page_text(pdf_path, pnum, table_text)
        print(
            f"  [A] Page {pnum}/{total}: {table_count} table(s)"
            f"{f' via {flavor}' if flavor else ''} → {saved_path.name}"
        )

        text = table_text

        # ── Step 3: Gate check ────────────────────────────────────────────
        passed, active_fields = page_passes_gate(text)
        if not passed:
            print(f"  [B] Page {pnum:>3}: SKIP")
            pages_skipped += 1
            continue

        active_fields = _ensure_nature_field(active_fields)
        print(f"  [B] Page {pnum:>3}: PASS ✓  fields={active_fields}")
        pages_passed += 1

        # ── Step 4: LLM extraction ────────────────────────────────────────
        print(f"  [C] Page {pnum} ({len(text)} chars) → LLM …", end="", flush=True)
        raw_rows = llm_extract_rows(
            text,
            active_fields,
            vm_mode,
            api_key,
            llm_script_path,
            pdf_name=pdf_path,
            page_number=pnum,
        )
        print(f" {len(raw_rows)} raw")

        # Post-process rows
        raw_rows = _blank_conditional_fields(raw_rows, active_fields)
        raw_rows = _backfill_nature_of_applicant(raw_rows, text)
        raw_rows = route_applications_under_52_rows(raw_rows, text)
        raw_rows   = dedup_dicts(raw_rows)
        validated  = validate_rows(raw_rows)
        normalized = normalize(validated)
        print(f"         → {len(normalized)} normalised rows")

        # Sub-layer V: Contextual voltage extraction (per row)
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
        total_pages_extracted=limit,
        pages_passed_gate=pages_passed,
        pages_skipped=pages_skipped,
        total_rows=sum(r.rows_found for r in results),
        results=results,
    )
