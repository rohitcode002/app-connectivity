"""
bayallocation_handler/runner.py — Bay Allocation Orchestration (Module 5)
==========================================================================
Discovers all Bay Allocation PDFs in source/bayallocation/, checks a JSON
cache, extracts un-cached PDFs, writes per-PDF JSON output, and incrementally
appends/upserts each PDF into the Excel workbook.

Each page of a PDF is treated as one independent extraction unit.

Usage (standalone):
    from pipeline.bayallocation_handler import run_bayallocation_extraction
    df = run_bayallocation_extraction()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Optional

import pandas as pd

from config import RuntimeConfig, load_runtime_config
from pipeline.excel_utils import (
    _apply_data_style,
    _apply_header_style,
    _autosize_columns,
    _get_openpyxl,
    export_to_excel,
)
from pipeline.bayallocation_handler.extraction import (
    extract_bayallocation_image,
    extract_bayallocation_pdf,
)

logger = logging.getLogger(__name__)

# ─── Default I/O paths ────────────────────────────────────────────────────────
_START_DIR = Path(__file__).resolve().parent.parent.parent   # …/start/

# Primary: where the downloader saves Bay Allocation PDFs
_DOWNLOAD_SOURCE : Path = _START_DIR / "output" / "source_output" / "CTUIL-Renewable-Energy" / "Bays Allocation"
# Fallback: legacy manual-drop folder
_LEGACY_SOURCE   : Path = _START_DIR / "source" / "bayallocation"


def _default_source_dir() -> Path:
    """Prefer downloaded PDFs; fall back to the legacy source folder."""
    if _DOWNLOAD_SOURCE.exists() and any(_DOWNLOAD_SOURCE.rglob("*.pdf")):
        return _DOWNLOAD_SOURCE
    return _LEGACY_SOURCE


BAY_SOURCE_DIR : Path = _default_source_dir()
BAY_OUTPUT_DIR : Path = _START_DIR / "output" / "bayallocation_cache"
BAY_EXCEL      : Path = _START_DIR / "excels" / "05_bayallocation_extracted.xlsx"

# Flat columns exported to Excel (in order)
EXCEL_COLUMNS = [
    "Source",
    "Page Number",
    "Extraction Method",
    "Name of Substation",
    "Substation Coordinates",
    "Voltage Level",
    "Bay No",
    "Connectivity Quantum (MW)",
    "Name of Entity",
]


# ─── Cache helpers ────────────────────────────────────────────────────────────

def _cache_path(pdf_name: str, out_dir: Path) -> Path:
    return out_dir / f"{Path(pdf_name).stem}.json"


def _save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(all_results: list[dict]) -> list[dict]:
    """Flatten per-PDF, per-page, per-substation into one row per bay entry.

    Each allocation entry (individual bay) becomes its own Excel row.
    Substation name and coordinates are repeated for every bay belonging
    to that substation.
    """
    flat: list[dict] = []
    for pdf_result in all_results:
        source = pdf_result.get("source", "")
        for page in pdf_result.get("pages", []):
            page_number = page.get("page_number", "")
            extraction_method = page.get("extraction_method", "")
            for sub in page.get("substations", []):
                sub_name   = sub.get("name_of_substation", "")
                sub_coords = sub.get("substation_coordinates", "")

                allocations = sub.get("allocations", [])
                if not allocations:
                    # Substation exists but has no allocation entries —
                    # still emit one row so it appears in the Excel.
                    flat.append({
                        "Source":                    source,
                        "Page Number":               page_number,
                        "Extraction Method":         extraction_method,
                        "Name of Substation":        sub_name,
                        "Substation Coordinates":    sub_coords,
                        "Voltage Level":             "",
                        "Bay No":                    "",
                        "Connectivity Quantum (MW)": "",
                        "Name of Entity":            "",
                    })
                    continue

                for entry in allocations:
                    flat.append({
                        "Source":                    source,
                        "Page Number":               page_number,
                        "Extraction Method":         extraction_method,
                        "Name of Substation":        sub_name,
                        "Substation Coordinates":    sub_coords,
                        "Voltage Level":             entry.get("voltage_key", ""),
                        "Bay No":                    entry.get("bay_no", ""),
                        "Connectivity Quantum (MW)": entry.get("connectivity_quantum_mw", ""),
                        "Name of Entity":            entry.get("name_of_entity", ""),
                    })
    return flat


def _agg_stats(all_results: list[dict]) -> dict:
    """Compute aggregate stats from processed Bay Allocation PDFs."""
    flat_rows = _flatten(all_results)
    return {
        "pdfs_processed": len(all_results),
        "pages_matched": sum(r.get("total_pages", 0) for r in all_results),
        "substations": sum(r.get("total_substations", 0) for r in all_results),
        "bay_entries": len(flat_rows),
    }


def _ensure_excel_workbook(xlsx: Path) -> Path:
    """Create the Bay Allocation workbook with headers if needed."""
    if xlsx.exists():
        return xlsx.resolve()

    opx = _get_openpyxl()
    wb = opx.Workbook()
    ws = wb.active
    ws.title = "Bay Allocation Data"
    ws.append(EXCEL_COLUMNS)
    ws.freeze_panes = "A2"
    _apply_header_style(ws, opx)
    _autosize_columns(ws)

    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return xlsx


def _remove_existing_pdf_rows(ws, source_pdf: str) -> int:
    """Delete stale rows for this PDF before appending current rows."""
    if ws.max_row < 2:
        return 0

    headers = [cell.value for cell in ws[1]]
    if "Source" not in headers:
        return 0

    source_col = headers.index("Source") + 1
    target_name = Path(source_pdf).name
    removed = 0
    for row_idx in range(ws.max_row, 1, -1):
        value = ws.cell(row=row_idx, column=source_col).value
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
    """Append one PDF's flattened rows into the Bay Allocation workbook."""
    opx = _get_openpyxl()
    if not xlsx.exists():
        _ensure_excel_workbook(xlsx)

    wb = opx.load_workbook(xlsx)
    ws = wb["Bay Allocation Data"] if "Bay Allocation Data" in wb.sheetnames else wb.active
    if ws.max_row == 0:
        ws.append(EXCEL_COLUMNS)
        _apply_header_style(ws, opx)

    _remove_existing_pdf_rows(ws, data.get("source", ""))

    for record in _flatten([data]):
        ws.append([record.get(col) for col in EXCEL_COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _apply_header_style(ws, opx)
    _apply_data_style(ws, opx)
    _autosize_columns(ws)

    if "Run Summary" in wb.sheetnames:
        del wb["Run Summary"]
    ws_summary = wb.create_sheet("Run Summary")
    for row in [
        ("Run started at", started_at.isoformat(timespec="seconds")),
        ("Last updated at", updated_at.isoformat(timespec="seconds")),
        ("Runtime so far (seconds)", round(runtime_s, 2)),
        ("Last appended PDF", Path(data.get("source", "")).name),
        ("PDFs processed", stats["pdfs_processed"]),
        ("Pages matched", stats["pages_matched"]),
        ("Substations", stats["substations"]),
        ("Bay entries", stats["bay_entries"]),
    ]:
        ws_summary.append(list(row))
    _autosize_columns(ws_summary)

    wb.save(xlsx)
    return xlsx


# ─── Public API ───────────────────────────────────────────────────────────────

def run_bayallocation_extraction(
    source_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    excel_path: Path | str | None = None,
    runtime:    Optional[RuntimeConfig] = None,
    max_pages:  int = -1,
) -> pd.DataFrame:
    """Discover Bay Allocation PDFs → extract (skip cached) → dump JSON → append Excel.

    Parameters
    ----------
    source_dir : path to folder containing Bay Allocation PDFs
                 (default: source/bayallocation/)
    output_dir : path for per-PDF JSON cache files
                 (default: output/bayallocation_cache/)
    excel_path : path for the combined Excel report
                 (default: excels/bayallocation_extracted.xlsx)
    runtime    : RuntimeConfig instance (auto-loaded if None)

    Returns
    -------
    pd.DataFrame
        Flat DataFrame with one row per bay entry (allocation).
        Substation name/coordinates are repeated for each bay.
    """
    src  = Path(source_dir).resolve() if source_dir else BAY_SOURCE_DIR
    out  = Path(output_dir).resolve() if output_dir else BAY_OUTPUT_DIR
    xlsx = Path(excel_path).resolve() if excel_path else BAY_EXCEL

    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    xlsx.parent.mkdir(parents=True, exist_ok=True)

    if runtime is None:
        runtime = load_runtime_config()

    print("\n" + "=" * 64)
    print("  MODULE 5 — BAY ALLOCATION PDF EXTRACTION")
    print("=" * 64)
    print(f"  Source dir  : {src}")
    print(f"  Output dir  : {out}")
    print(f"  Excel output: {xlsx}")

    # Recursive scan for PDFs
    pdf_files = sorted(src.rglob("*.pdf"))
    if not pdf_files:
        print(f"  [BayAllocation] No PDFs found in: {src}")
        print("=" * 64)
        return pd.DataFrame()

    cached_count = sum(1 for p in pdf_files if _cache_path(p.name, out).exists())
    print(f"  PDFs found  : {len(pdf_files)}")
    print(f"  Cached      : {cached_count}  (will be skipped)")
    print(f"  To extract  : {len(pdf_files) - cached_count}")
    print(f"  Mode        : {runtime.execution_target}")
    print(f"  Max pages   : {max_pages if max_pages != -1 else 'ALL'}")
    print("=" * 64)

    started_at = datetime.now()
    t0 = perf_counter()
    all_results: list[dict] = []
    _ensure_excel_workbook(xlsx)

    for idx, pdf_path in enumerate(pdf_files, 1):
        cache = _cache_path(pdf_path.name, out)

        if cache.exists():
            print(f"\n  [{idx}/{len(pdf_files)}] SKIP    {pdf_path.name}")
            cached = _load_json(cache)
            all_results.append(cached)
            print(f"  -> JSON cache loaded: {cache.name}")
            print(f"  -> Dumping cached JSON rows into Excel: {xlsx.name}")
            _append_pdf_to_excel(
                cached,
                xlsx,
                started_at,
                datetime.now(),
                perf_counter() - t0,
                _agg_stats(all_results),
            )
            print(f"  -> Excel appended/verified: {xlsx.name}")
            continue

        print(f"\n  [{idx}/{len(pdf_files)}] EXTRACT {pdf_path.name}")
        print("-" * 48)

        try:
            pages = extract_bayallocation_pdf(
                str(pdf_path),
                max_pages=max_pages,
                runtime=runtime,
            )
        except Exception as exc:
            logger.error("[BayAllocation] Failed %s: %s", pdf_path.name, exc)
            print(f"  ERROR   {pdf_path.name}: {exc}")
            continue

        result = {
            "source":             pdf_path.name,
            "total_pages":        len(pages),
            "total_substations":  sum(len(p.get("substations", [])) for p in pages),
            "pages":              pages,
        }

        _save_json(result, cache)
        print(f"  -> {len(pages)} pages, "
              f"{result['total_substations']} substations saved -> {cache.name}")
        all_results.append(result)

        print(f"  -> Dumping extracted JSON rows into Excel: {xlsx.name}")
        _append_pdf_to_excel(
            result,
            xlsx,
            started_at,
            datetime.now(),
            perf_counter() - t0,
            _agg_stats(all_results),
        )
        print(f"  -> Excel appended: {xlsx.name}")

    # -- Aggregate ─────────────────────────────────────────────────────────────
    stats              = _agg_stats(all_results)
    total_pdfs         = stats["pdfs_processed"]
    total_pages        = stats["pages_matched"]
    total_substations  = stats["substations"]
    flat_rows          = _flatten(all_results)

    print("\n" + "=" * 64)
    print("  BAY ALLOCATION SUMMARY")
    print(f"    PDFs processed  : {total_pdfs}")
    print(f"    Pages matched   : {total_pages}")
    print(f"    Substations     : {total_substations}")
    print(f"    Bay entries     : {len(flat_rows)}")
    print("=" * 64)

    print(f"\n[BayAllocation] Excel → {xlsx}")

    if not flat_rows:
        return pd.DataFrame()

    return pd.DataFrame(flat_rows)


def run_bayallocation_image_extraction(
    image_dir: Path | str,
    output_dir: Path | str | None = None,
    excel_path: Path | str | None = None,
    runtime: Optional[RuntimeConfig] = None,
    max_pages: int = -1,
) -> pd.DataFrame:
    """Extract Bay Allocation rows from page images using LLM vision."""
    src = Path(image_dir).resolve()
    out = Path(output_dir).resolve() if output_dir else _START_DIR / "output" / "bayallocation_image_cache"
    xlsx = Path(excel_path).resolve() if excel_path else BAY_EXCEL

    out.mkdir(parents=True, exist_ok=True)
    xlsx.parent.mkdir(parents=True, exist_ok=True)

    if runtime is None:
        runtime = load_runtime_config()

    image_files = sorted(
        p for p in src.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if max_pages != -1:
        image_files = image_files[:max_pages]

    print("\n" + "=" * 64)
    print("  MODULE 5 — BAY ALLOCATION IMAGE EXTRACTION")
    print("=" * 64)
    print(f"  Image dir   : {src}")
    print(f"  Output dir  : {out}")
    print(f"  Excel output: {xlsx}")
    print(f"  Images      : {len(image_files)}")
    print(f"  Mode        : {runtime.execution_target}")
    print("=" * 64)

    if not image_files:
        print(f"  [BayAllocation] No images found in: {src}")
        print("=" * 64)
        return pd.DataFrame()

    all_results: list[dict] = []
    for page_number, image_path in enumerate(image_files, 1):
        cache = _cache_path(image_path.name, out)

        if cache.exists():
            print(f"\n  [{page_number}/{len(image_files)}] SKIP    {image_path.name}")
            all_results.append(_load_json(cache))
            continue

        print(f"\n  [{page_number}/{len(image_files)}] EXTRACT {image_path.name}")
        print("-" * 48)

        page = extract_bayallocation_image(
            image_path,
            page_number=page_number,
            runtime=runtime,
        )
        if page is None:
            page = {
                "page_number": page_number,
                "raw_text": "",
                "columns": [],
                "table_rows": [],
                "substations": [],
                "extraction_method": "llm_image_failed",
            }

        result = {
            "source": image_path.name,
            "total_pages": 1,
            "total_substations": len(page.get("substations", [])),
            "pages": [page],
        }
        _save_json(result, cache)
        all_results.append(result)

    total_pages = sum(r.get("total_pages", 0) for r in all_results)
    total_substations = sum(r.get("total_substations", 0) for r in all_results)
    flat_rows = _flatten(all_results)

    print("\n" + "=" * 64)
    print("  BAY ALLOCATION IMAGE SUMMARY")
    print(f"    Images processed: {len(all_results)}")
    print(f"    Pages matched   : {total_pages}")
    print(f"    Substations     : {total_substations}")
    print(f"    Bay entries     : {len(flat_rows)}")
    print("=" * 64)

    if not flat_rows:
        return pd.DataFrame()

    df = pd.DataFrame(flat_rows)
    col_order = [c for c in EXCEL_COLUMNS if c in df.columns]
    export_to_excel(
        rows=flat_rows,
        output_path=xlsx,
        sheet_name="Bay Allocation Data",
        column_order=col_order,
        summary_rows=[
            ("Images processed", len(all_results)),
            ("Pages matched", total_pages),
            ("Substations", total_substations),
            ("Bay entries", len(flat_rows)),
        ],
    )
    print(f"\n[BayAllocation] Excel → {xlsx}")

    return df
