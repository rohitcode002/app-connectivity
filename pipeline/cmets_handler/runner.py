"""
cmets_handler/runner.py — CMETS Orchestration (discovery, cache, Excel)
=========================================================================
Discovers all PDFs in source/cmets_pdfs/, checks JSON cache in
output/cmets_cache/, extracts only un-cached PDFs, flattens results,
writes excels/cmets.xlsx.

This is the only file that performs I/O orchestration for Module 1.
Edit extraction.py or normalization.py to change how data is extracted
or cleaned — this file only handles discovery, caching, and output.

IMPORTANT: The extraction Excel (01_cmets_extracted.xlsx) produced here
contains EXACT extracted data with NO formatting.  All formatting is
applied ONLY in the final dtbc_format Excel (via formatter.py).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Optional

from config import RuntimeConfig, load_runtime_config
from pipeline.cmets_handler.models import PipelineResult, CMETS_COLUMNS
from pipeline.cmets_handler.extraction import run_single_pdf
from pipeline.cmets_handler.meeting_classifier import classify_meeting
from pipeline.cmets_handler.normalization import consolidate_application_duplicates
from pipeline.token_usage import DEFAULT_TOKEN_USAGE_PATH, get_module_token_usage

logger = logging.getLogger(__name__)

# ─── Default I/O paths ────────────────────────────────────────────────────────
_START_DIR  = Path(__file__).resolve().parent.parent.parent   # …/start/

# Root of the downloaded CMETS output
_CMETS_DOWNLOAD_ROOT : Path = _START_DIR / "output" / "source_output" / "CTUIL-ISTS-CMETS"
# Two download layouts for minutes PDFs:
#   Layout 1: CTUIL-ISTS-CMETS/minutes/<Region>/*.pdf   (lowercase "minutes")
#   Layout 2: CTUIL-ISTS-CMETS/<Region>/Minutes/*.pdf   (uppercase "Minutes")
_DOWNLOAD_MINUTES_LC : Path = _CMETS_DOWNLOAD_ROOT / "minutes"
# Fallback: legacy manual-drop folder
_LEGACY_SOURCE       : Path = _START_DIR / "source" / "cmets_pdfs"


def _collect_minutes_pdfs(root: Path) -> list[Path]:
    """Collect CMETS minutes PDFs from both download layouts under *root*.

    Layout 1: root/minutes/<Region>/*.pdf
    Layout 2: root/<Region>/Minutes/*.pdf
    """
    pdfs: set[Path] = set()
    # Layout 1: minutes/ (lowercase) with region subfolders
    lc_dir = root / "minutes"
    if lc_dir.exists():
        pdfs.update(p for p in lc_dir.rglob("*.pdf") if p.is_file())
    # Layout 2: <Region>/Minutes/ (uppercase) at root level
    for child in root.iterdir():
        if child.is_dir() and child.name != "minutes" and child.name != "agenda":
            minutes_sub = child / "Minutes"
            if minutes_sub.is_dir():
                pdfs.update(p for p in minutes_sub.rglob("*.pdf") if p.is_file())
    return sorted(pdfs)


def _default_source_dir() -> Path:
    """Prefer downloaded PDFs; fall back to the legacy source folder."""
    if _CMETS_DOWNLOAD_ROOT.exists() and _collect_minutes_pdfs(_CMETS_DOWNLOAD_ROOT):
        return _CMETS_DOWNLOAD_ROOT
    return _LEGACY_SOURCE


SOURCE_DIR  : Path = _default_source_dir()
OUTPUT_DIR  : Path = _START_DIR / "output" / "cmets_cache"
CMETS_EXCEL : Path = _START_DIR / "excels" / "cmets.xlsx"


# ─── Region derivation from PDF path ─────────────────────────────────────────

_REGION_PATTERNS = [
    (r"\bnorth[\s_-]*eastern[\s_-]*region\b", "NER"),
    (r"\bnorth[\s_-]*east(?:ern)?\b", "NER"),
    (r"\bnorthern[\s_-]*region\b", "NR"),
    (r"\bsouthern[\s_-]*region\b", "SR"),
    (r"\bwestern[\s_-]*region\b", "WR"),
    (r"\beastern[\s_-]*region\b", "ER"),
    (r"\bCMETS[\s_-]*NER\b", "NER"),
    (r"\bCMETS[\s_-]*NR\b", "NR"),
    (r"\bCMETS[\s_-]*SR\b", "SR"),
    (r"\bCMETS[\s_-]*WR\b", "WR"),
    (r"\bCMETS[\s_-]*ER\b", "ER"),
    (r"\bNER\b", "NER"),
    (r"\bNR\b", "NR"),
    (r"\bSR\b", "SR"),
    (r"\bWR\b", "WR"),
    (r"\bER\b", "ER"),
]


def _derive_region_from_pdf_path(pdf_path: str) -> str | None:
    """Derive the region code from the PDF path and filename.

    Checks both the full path (folder names) and the PDF filename
    for region keywords like 'Northern Region', 'NR', 'SR', etc.
    """
    text = str(pdf_path)
    for pattern, region in _REGION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return region
    return None


# ─── Serialisation ────────────────────────────────────────────────────────────

# Meeting-level columns that are injected from meeting_meta (same for all rows per PDF)
_MEETING_COLS = [
    "CMETS GNA Approved", "CMETS LTA Approved",
    "CMETS GNA Meeting Date", "CMETS LTA Meeting Date",
]


def _inject_meeting_meta_into_rows(data: dict, meeting_meta: dict | None) -> dict:
    """Write meeting metadata directly into every row dict in the JSON payload."""
    if not meeting_meta:
        return data

    for page in data.get("results", []):
        for row in page.get("rows", []):
            if isinstance(row, dict):
                for col in _MEETING_COLS:
                    row[col] = meeting_meta.get(col)
    return data


def _serialize(result: PipelineResult, meeting_meta: dict | None = None) -> dict:
    out = result.model_dump()
    for i, pr in enumerate(out["results"]):
        pr["rows"] = [r.model_dump(by_alias=True) for r in result.results[i].rows]
    if meeting_meta:
        out["meeting_meta"] = meeting_meta
        _inject_meeting_meta_into_rows(out, meeting_meta)
    return out


def _attach_meeting_diagnostics(data: dict, meeting_meta) -> dict:
    """Save first-page/classifier evidence in the per-PDF JSON cache only."""
    data["first_page"] = meeting_meta.as_first_page_dict()
    data["meeting_classifier"] = meeting_meta.as_diagnostics_dict()
    return data


def _save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(all_serialized: list[dict]) -> list[dict]:
    """Flatten nested per-PDF results into flat rows for Excel.

    This also derives and injects the Region column from the PDF path.
    """
    records = []
    for pr in all_serialized:
        pdf_path = pr.get("pdf_path", "")
        # Derive region from PDF path/filename
        region = _derive_region_from_pdf_path(pdf_path)
        # Meeting-level metadata (same for every row from this PDF)
        meeting = pr.get("meeting_meta") or {}
        for page in pr.get("results", []):
            pnum = page.get("page_number")
            for row in page.get("rows", []):
                rec = {"PDF": pdf_path, "Page Number": pnum}
                # Inject region
                rec["Region"] = region
                # Inject meeting columns
                for mcol in _MEETING_COLS:
                    rec[mcol] = meeting.get(mcol) or row.get(mcol)
                # Row-level columns
                for col in CMETS_COLUMNS:
                    if col not in rec:  # skip PDF, Page, meeting cols already set
                        rec[col] = row.get(col)
                start_col = (
                    "Applied Start of Connectivity sought by developer date"
                    "( start date of connectivity as per the application)"
                )
                rec[start_col] = (
                    row.get(start_col)
                    or row.get("Applied Start of Connectivity sought by developer date")
                    or row.get("Start Date of Connectivity (As per Application)")
                )
                records.append(rec)
    return records


def _agg_stats(all_serialized: list[dict]) -> dict:
    return {
        "pdfs_processed":          len(all_serialized),
        "total_pages_extracted":   sum(p.get("total_pages_extracted",   0) for p in all_serialized),
        "total_pages_passed_gate": sum(p.get("pages_passed_gate",       0) for p in all_serialized),
        "total_pages_skipped":     sum(p.get("pages_skipped",           0) for p in all_serialized),
        "total_rows":              sum(p.get("total_rows",              0) for p in all_serialized),
    }


def _ensure_excel_workbook(xlsx: Path) -> Path:
    """Create the CMETS workbook with headers if it does not already exist.

    NO formatting/styling is applied — this is raw extraction output.
    """
    if xlsx.exists():
        return xlsx.resolve()

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Extracted Data"
    ws.append(CMETS_COLUMNS)
    ws.freeze_panes = "A2"

    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return xlsx


def _remove_existing_pdf_rows(ws, pdf_path: str) -> int:
    """Delete stale rows for this PDF name before appending current JSON rows."""
    if ws.max_row < 2:
        return 0

    headers = [cell.value for cell in ws[1]]
    if "PDF" not in headers:
        return 0

    pdf_col = headers.index("PDF") + 1
    target_name = Path(pdf_path).name
    removed = 0
    for row_idx in range(ws.max_row, 1, -1):
        value = ws.cell(row=row_idx, column=pdf_col).value
        if value and Path(str(value)).name == target_name:
            ws.delete_rows(row_idx, 1)
            removed += 1
    return removed


def _append_pdf_to_excel(
    data: dict,
    xlsx: Path,
    started_at: datetime,
    updated_at: datetime,
    runtime_s: float,
    stats: dict,
) -> Path:
    """Append one PDF's flattened JSON rows into the existing CMETS workbook.

    NO formatting/styling is applied — this is raw extraction output.
    """
    from openpyxl import load_workbook
    if not xlsx.exists():
        _ensure_excel_workbook(xlsx)

    wb = load_workbook(xlsx)
    ws = wb["Extracted Data"] if "Extracted Data" in wb.sheetnames else wb.active
    if ws.max_row == 0:
        ws.append(CMETS_COLUMNS)

    _remove_existing_pdf_rows(ws, data.get("pdf_path", ""))

    for record in _flatten([data]):
        ws.append([record.get(col) for col in CMETS_COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    if "Run Summary" in wb.sheetnames:
        del wb["Run Summary"]
    ws_summary = wb.create_sheet("Run Summary")
    for row in [
        ("Run started at",           started_at.isoformat(timespec="seconds")),
        ("Last updated at",          updated_at.isoformat(timespec="seconds")),
        ("Runtime so far (seconds)", round(runtime_s, 2)),
        ("Last appended PDF",        Path(data.get("pdf_path", "")).name),
        ("PDFs processed",           stats["pdfs_processed"]),
        ("Total pages extracted",    stats["total_pages_extracted"]),
        ("Total pages passed gate",  stats["total_pages_passed_gate"]),
        ("Total pages skipped",      stats["total_pages_skipped"]),
        ("Total rows",               stats["total_rows"]),
    ]:
        ws_summary.append(list(row))

    wb.save(xlsx)
    return xlsx


def _rewrite_extracted_sheet(
    records: list[dict],
    xlsx: Path,
    started_at: datetime,
    updated_at: datetime,
    runtime_s: float,
    stats: dict,
    duplicates_removed: int,
) -> Path:
    """Rewrite Extracted Data with final CMETS-only consolidated rows.

    NO formatting/styling is applied — this is raw extraction output.
    """
    from openpyxl import Workbook, load_workbook
    wb = load_workbook(xlsx) if xlsx.exists() else Workbook()

    if "Extracted Data" in wb.sheetnames:
        ws = wb["Extracted Data"]
        ws.delete_rows(1, ws.max_row)
    else:
        ws = wb.active
        ws.title = "Extracted Data"

    ws.append(CMETS_COLUMNS)
    for record in records:
        ws.append([record.get(col) for col in CMETS_COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    if "Run Summary" in wb.sheetnames:
        del wb["Run Summary"]
    ws_summary = wb.create_sheet("Run Summary")
    for row in [
        ("Run started at",           started_at.isoformat(timespec="seconds")),
        ("Last updated at",          updated_at.isoformat(timespec="seconds")),
        ("Runtime so far (seconds)", round(runtime_s, 2)),
        ("PDFs processed",           stats["pdfs_processed"]),
        ("Total pages extracted",    stats["total_pages_extracted"]),
        ("Total pages passed gate",  stats["total_pages_passed_gate"]),
        ("Total pages skipped",      stats["total_pages_skipped"]),
        ("Raw extracted rows",       stats["total_rows"]),
        ("Rows after consolidation", len(records)),
        ("Duplicate rows removed",   duplicates_removed),
    ]:
        ws_summary.append(list(row))

    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return xlsx


# ─── Public API ───────────────────────────────────────────────────────────────

def run_cmets_extraction(
    source_dir:  Path | str | None = None,
    output_dir:  Path | str | None = None,
    excel_path:  Path | str | None = None,
    single_pdf:  Optional[str] = None,
    runtime:     Optional[RuntimeConfig] = None,
    max_pages:   int = -1,
) -> Path:
    """Discover CMETS PDFs → extract (skip cached) → dump JSON → write cmets.xlsx.

    Returns the absolute path to cmets.xlsx.
    """
    src   = Path(source_dir).resolve()  if source_dir else SOURCE_DIR
    out   = Path(output_dir).resolve()  if output_dir else OUTPUT_DIR
    xlsx  = Path(excel_path).resolve()  if excel_path else CMETS_EXCEL

    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    if runtime is None:
        runtime = load_runtime_config()

    # Discover PDFs
    if single_pdf:
        p = Path(single_pdf).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"[CMETS] PDF not found: {p}")
        pdf_paths = [p]
    else:
        # If src is the download root, collect from both minutes layouts;
        # otherwise use recursive glob on the provided directory.
        if src == _CMETS_DOWNLOAD_ROOT:
            pdf_paths = _collect_minutes_pdfs(src)
        else:
            pdf_paths = sorted(p for p in src.rglob("*.pdf") if p.is_file())
        if not pdf_paths:
            raise SystemExit(f"[CMETS] No PDFs found in '{src}'.")

    cached = sum(1 for p in pdf_paths if (out / f"{p.stem}.json").exists())

    print("\n" + "=" * 64)
    print("  MODULE 1 — CMETS PDF EXTRACTION")
    print("=" * 64)
    print(f"  Source dir     : {src}")
    print(f"  Output dir     : {out}")
    print(f"  Excel output   : {xlsx}")
    print(f"  PDFs found     : {len(pdf_paths)}")
    print(f"  Cached (skip)  : {cached}")
    print(f"  To extract     : {len(pdf_paths) - cached}")
    print(f"  Mode           : {runtime.execution_target}")
    print(f"  Max pages/PDF  : {max_pages if max_pages != -1 else 'ALL'}")
    print("=" * 64)

    started_at = datetime.now()
    t0         = perf_counter()
    all_data:  list[dict] = []
    _ensure_excel_workbook(xlsx)

    for idx, pdf_path in enumerate(pdf_paths, 1):
        cache = out / f"{pdf_path.stem}.json"

        # Meeting-level metadata is computed exactly once per PDF for this run.
        # The same values are injected into every row flattened from this PDF.
        print(f"\n[{idx}/{len(pdf_paths)}] METADATA {pdf_path.name}")
        print(f"  [M] Classifying meeting type …", end=" ", flush=True)
        meeting_meta = classify_meeting(
            str(pdf_path),
            vm_mode=runtime.vm_mode,
            api_key=runtime.api_key or None,
            llm_script_path=runtime.llm_script_path,
        )
        print(
            f"#{meeting_meta.meeting_number or '?'} "
            f"({meeting_meta.meeting_date or 'no date'}) → "
            f"{meeting_meta.classification} "
            f"(GNA:{meeting_meta.gna_count} LTA:{meeting_meta.lta_count})"
        )

        if cache.exists():
            print(f"  [X] SKIP extraction — cache found")
            data = _load_json(cache)
            row_meeting_meta = meeting_meta.as_row_dict()
            data["meeting_meta"] = row_meeting_meta
            _inject_meeting_meta_into_rows(data, row_meeting_meta)
            _attach_meeting_diagnostics(data, meeting_meta)
            _save_json(data, cache)
            all_data.append(data)
            out_path = _append_pdf_to_excel(
                data,
                xlsx,
                started_at,
                datetime.now(),
                perf_counter() - t0,
                _agg_stats(all_data),
            )
            print(f"  → Excel appended/verified: {out_path.name}")
            continue

        print(f"  [X] EXTRACT {pdf_path.name}")
        print("-" * 64)

        result = run_single_pdf(
            pdf_path=str(pdf_path),
            api_key=runtime.api_key or None,
            vm_mode=runtime.vm_mode,
            llm_script_path=runtime.llm_script_path,
            max_pages=max_pages,
        )
        data = _serialize(result, meeting_meta=meeting_meta.as_row_dict())
        _attach_meeting_diagnostics(data, meeting_meta)
        _save_json(data, cache)
        print(f"  → JSON: {cache.name}")
        all_data.append(data)
        out_path = _append_pdf_to_excel(
            data,
            xlsx,
            started_at,
            datetime.now(),
            perf_counter() - t0,
            _agg_stats(all_data),
        )
        print(f"  → Excel appended/verified: {out_path.name}")

    runtime_s   = perf_counter() - t0
    finished_at = datetime.now()
    stats       = _agg_stats(all_data)
    raw_records = _flatten(all_data)
    consolidated_records = consolidate_application_duplicates(raw_records)
    duplicates_removed = len(raw_records) - len(consolidated_records)
    _rewrite_extracted_sheet(
        consolidated_records,
        xlsx,
        started_at,
        finished_at,
        runtime_s,
        stats,
        duplicates_removed,
    )

    print("\n" + "=" * 64)
    print("  CMETS SUMMARY")
    print(f"    PDFs          : {len(pdf_paths)}  (skipped {cached})")
    print(f"    Pages ext.    : {stats['total_pages_extracted']}")
    print(f"    Pages passed  : {stats['total_pages_passed_gate']}")
    print(f"    Raw rows      : {stats['total_rows']}")
    print(f"    Final rows    : {len(consolidated_records)}  (removed {duplicates_removed})")
    print(f"    Runtime (s)   : {runtime_s:.1f}")
    token_usage = get_module_token_usage("cmets")
    token_total = token_usage["total_tokens"] + token_usage["estimated_total_tokens"]
    print(
        f"    LLM tokens    : {token_total} cumulative "
        f"(actual {token_usage['total_tokens']}, estimated {token_usage['estimated_total_tokens']})"
    )
    print(f"    Token usage   : {DEFAULT_TOKEN_USAGE_PATH}")
    print("=" * 64)

    print(f"\n[CMETS] 01_cmets_extracted.xlsx → {xlsx.resolve()}")
    return xlsx
