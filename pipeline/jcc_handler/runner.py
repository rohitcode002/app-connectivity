"""
jcc_handler/runner.py — JCC Orchestration (Module 4)
======================================================
Discovers all JCC Meeting PDFs in the source folder (recursive scan),
checks JSON cache, extracts un-cached PDFs, writes per-PDF JSON cache
and **incrementally appends** rows to the Excel workbook after each PDF.

Extraction is the ONLY responsibility of ``run_jcc_extraction()``.
Mapping layers (JCC Output Layer, Layer 4) live in ``run_jcc_mapping()``
and are called separately by the full pipeline orchestrator.

This is the only file that performs I/O orchestration for Module 4.
Edit extraction.py to change how tables are detected or parsed.
Edit models.py to change column names or keyword filters.
Edit jcc_output_layer.py to change the GNA/TGNA computation logic.
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
)
from pipeline.jcc_handler.models import EXCEL_COLUMN_NAMES
from pipeline.jcc_handler.extraction import extract_jcc_pdf

logger = logging.getLogger(__name__)

# ─── Default I/O paths ────────────────────────────────────────────────────────
_START_DIR = Path(__file__).resolve().parent.parent.parent   # …/start/

# Primary: where the downloader saves JCC PDFs
_DOWNLOAD_SOURCE : Path = _START_DIR / "output" / "source_output" / "CTUIL-ISTS-JCC"
# Fallback: legacy manual-drop folder
_LEGACY_SOURCE   : Path = _START_DIR / "source" / "jcc_pdfs"


def _default_source_dir() -> Path:
    """Prefer downloaded PDFs; fall back to the legacy source folder."""
    if _DOWNLOAD_SOURCE.exists() and any(_DOWNLOAD_SOURCE.rglob("*.pdf")):
        return _DOWNLOAD_SOURCE
    return _LEGACY_SOURCE


JCC_SOURCE_DIR  : Path = _default_source_dir()
JCC_OUTPUT_DIR  : Path = _START_DIR / "output" / "jcc_cache"
JCC_EXCEL       : Path = _START_DIR / "excels" / "04_jcc_extracted.xlsx"

# Full column order for the Excel workbook
_JCC_EXCEL_COLUMNS = ["source_pdf", "page_number"] + EXCEL_COLUMN_NAMES


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


# Increment this version when postprocessing logic changes materially.
# Stale caches without a matching version will be re-extracted.
_POSTPROCESS_VERSION = 4


def _has_current_jcc_schema(data: dict) -> bool:
    """Return True when cached JCC rows already contain the new derived fields
    AND were produced with the current postprocessing version."""
    if data.get("postprocess_version", 0) < _POSTPROCESS_VERSION:
        return False
    required = {"total_COD", "COD_Found", "effective_date", "TGNA", "GNA"}
    saw_row = False
    for page in data.get("pages", []):
        for row in page.get("rows", []):
            saw_row = True
            if not required.issubset(row.keys()):
                return False
    return saw_row or data.get("total_matching_pages", 0) == 0


def _flatten(all_results: list[dict]) -> list[dict]:
    """Flatten per-PDF results into flat rows for Excel."""
    flat: list[dict] = []
    for pdf_result in all_results:
        source = pdf_result.get("source", "")
        for page in pdf_result.get("pages", []):
            pnum = page.get("page_number")
            for row in page.get("rows", []):
                rec = {"source_pdf": source, "page_number": pnum}
                rec.update(row)
                flat.append(rec)
    return flat


def _agg_stats(all_results: list[dict]) -> dict:
    """Compute aggregate stats from all processed PDFs."""
    flat = _flatten(all_results)
    return {
        "pdfs_processed":  len(all_results),
        "pages_matched":   sum(r.get("total_matching_pages", 0) for r in all_results),
        "total_data_rows": len(flat),
    }


# ─── Incremental Excel helpers (same pattern as CMETS) ───────────────────────

def _ensure_excel_workbook(xlsx: Path) -> Path:
    """Create the JCC workbook with headers if it does not already exist."""
    if xlsx.exists():
        return xlsx.resolve()

    opx = _get_openpyxl()
    wb = opx.Workbook()
    ws = wb.active
    ws.title = "JCC Extracted Data"
    ws.append(_JCC_EXCEL_COLUMNS)
    ws.freeze_panes = "A2"
    _apply_header_style(ws, opx)
    _autosize_columns(ws)

    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return xlsx


def _remove_existing_pdf_rows(ws, source_pdf: str) -> int:
    """Delete stale rows for this PDF name before appending current rows."""
    if ws.max_row < 2:
        return 0

    headers = [cell.value for cell in ws[1]]
    if "source_pdf" not in headers:
        return 0

    pdf_col = headers.index("source_pdf") + 1
    target_name = Path(source_pdf).name
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
    """Append one PDF's flattened rows into the existing JCC workbook."""
    opx = _get_openpyxl()
    if not xlsx.exists():
        _ensure_excel_workbook(xlsx)

    wb = opx.load_workbook(xlsx)
    ws = wb["JCC Extracted Data"] if "JCC Extracted Data" in wb.sheetnames else wb.active
    if ws.max_row == 0:
        ws.append(_JCC_EXCEL_COLUMNS)
        _apply_header_style(ws, opx)

    # Remove stale rows for this PDF, then append fresh ones
    _remove_existing_pdf_rows(ws, data.get("source", ""))

    for record in _flatten([data]):
        ws.append([record.get(col) for col in _JCC_EXCEL_COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _apply_header_style(ws, opx)
    _apply_data_style(ws, opx)
    _autosize_columns(ws)

    # Update Run Summary sheet
    if "Run Summary" in wb.sheetnames:
        del wb["Run Summary"]
    ws_summary = wb.create_sheet("Run Summary")
    for row in [
        ("Run started at",           started_at.isoformat(timespec="seconds")),
        ("Last updated at",          updated_at.isoformat(timespec="seconds")),
        ("Runtime so far (seconds)", round(runtime_s, 2)),
        ("Last appended PDF",        Path(data.get("source", "")).name),
        ("PDFs processed",           stats["pdfs_processed"]),
        ("Pages matched",            stats["pages_matched"]),
        ("Total data rows",          stats["total_data_rows"]),
    ]:
        ws_summary.append(list(row))
    _autosize_columns(ws_summary)

    wb.save(xlsx)
    return xlsx


# ─── Public API: Extraction only ─────────────────────────────────────────────

def run_jcc_extraction(
    source_dir:  Path | str | None = None,
    output_dir:  Path | str | None = None,
    excel_path:  Path | str | None = None,
    runtime:     Optional[RuntimeConfig] = None,
    max_pages:   int = -1,
) -> pd.DataFrame:
    """Discover JCC Meeting PDFs → extract → save JSON → append Excel.

    This function is ONLY responsible for extraction. It does NOT run
    any mapping layers (JCC Output Layer, Layer 4). Use
    ``run_jcc_mapping()`` for that — called separately by the full
    pipeline orchestrator.

    Excel is updated **incrementally after each PDF** — rows are appended
    as soon as a PDF is extracted (or loaded from cache), so partial
    results are available even if the run is interrupted.

    Returns pd.DataFrame with all extracted JCC rows.
    """
    src  = Path(source_dir).resolve()  if source_dir else JCC_SOURCE_DIR
    out  = Path(output_dir).resolve()  if output_dir else JCC_OUTPUT_DIR
    xlsx = Path(excel_path).resolve()  if excel_path else JCC_EXCEL

    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    xlsx.parent.mkdir(parents=True, exist_ok=True)

    if runtime is None:
        runtime = load_runtime_config()

    print("\n" + "=" * 64)
    print("  MODULE 4 — JCC MEETING PDF EXTRACTION")
    print("=" * 64)
    print(f"  Source dir  : {src}")
    print(f"  Output dir  : {out}")
    print(f"  Excel output: {xlsx}")

    logger.info(
        "[JCC STEP] run_start source_dir=%s output_dir=%s excel=%s max_pages=%s mode=%s",
        src, out, xlsx, max_pages if max_pages != -1 else "ALL", runtime.execution_target,
    )
    print("  Step output : console only")

    # Recursive scan for PDFs — only inside "Minutes" folders
    pdf_files = sorted(
        p for p in src.rglob("*.pdf")
        if any(part.lower() == "minutes" for part in p.parts)
    )
    if not pdf_files:
        print(f"  [JCC] No PDFs found in: {src}")
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
    t0         = perf_counter()
    all_results: list[dict] = []
    _ensure_excel_workbook(xlsx)

    for idx, pdf_path in enumerate(pdf_files, 1):
        cache = _cache_path(pdf_path.name, out)

        if cache.exists():
            cached = _load_json(cache)
            if _has_current_jcc_schema(cached):
                print(f"\n  [{idx}/{len(pdf_files)}] SKIP    {pdf_path.name}")
                print(f"  → JSON cache loaded: {cache.name}")
                logger.info("[JCC STEP] cache_loaded pdf=%s json=%s", pdf_path.name, cache)
                all_results.append(cached)
                # Still append to Excel (upsert) so Excel stays in sync
                print(f"  → Dumping cached JSON rows into Excel: {xlsx.name}")
                logger.info("[JCC STEP] excel_dump_start pdf=%s source_json=%s excel=%s", pdf_path.name, cache, xlsx)
                _append_pdf_to_excel(
                    cached, xlsx, started_at, datetime.now(),
                    perf_counter() - t0, _agg_stats(all_results),
                )
                logger.info("[JCC STEP] excel_dump_done pdf=%s excel=%s", pdf_path.name, xlsx)
                print(f"  → Excel appended/verified: {xlsx.name}")
                continue
            print(f"\n  [{idx}/{len(pdf_files)}] REFRESH {pdf_path.name} (stale JCC schema)")
        else:
            print(f"\n  [{idx}/{len(pdf_files)}] EXTRACT {pdf_path.name}")

        print("-" * 48)

        try:
            logger.info("[JCC STEP] pdf_extraction_start pdf=%s path=%s", pdf_path.name, pdf_path)
            pages = extract_jcc_pdf(str(pdf_path), runtime=runtime, max_pages=max_pages)
        except Exception as exc:
            logger.error("[JCC] Failed %s: %s", pdf_path.name, exc)
            print(f"  ERROR   {pdf_path.name}: {exc}")
            continue

        result = {
            "source": pdf_path.name,
            "total_matching_pages": len(pages),
            "pages": pages,
            "postprocess_version": _POSTPROCESS_VERSION,
        }

        _save_json(result, cache)
        total_rows = sum(len(p.get("rows", [])) for p in pages)
        logger.info(
            "[JCC STEP] json_saved pdf=%s json=%s matched_pages=%d rows=%d",
            pdf_path.name, cache, len(pages), total_rows,
        )
        print(f"  → {len(pages)} pages, {total_rows} rows saved → {cache.name}")
        all_results.append(result)

        # Immediately append this PDF's rows to Excel
        print(f"  → Dumping extracted JSON rows into Excel: {xlsx.name}")
        logger.info("[JCC STEP] excel_dump_start pdf=%s source_json=%s excel=%s", pdf_path.name, cache, xlsx)
        _append_pdf_to_excel(
            result, xlsx, started_at, datetime.now(),
            perf_counter() - t0, _agg_stats(all_results),
        )
        logger.info("[JCC STEP] excel_dump_done pdf=%s rows=%d excel=%s", pdf_path.name, total_rows, xlsx)
        print(f"  → Excel appended: {xlsx.name}")

    # Final aggregate stats
    stats = _agg_stats(all_results)

    print("\n" + "=" * 64)
    print("  JCC SUMMARY")
    print(f"    PDFs processed  : {stats['pdfs_processed']}")
    print(f"    Pages matched   : {stats['pages_matched']}")
    print(f"    Total data rows : {stats['total_data_rows']}")
    print("=" * 64)
    print(f"\n[JCC] Excel → {xlsx}")
    logger.info(
        "[JCC STEP] run_complete pdfs=%d pages_matched=%d rows=%d excel=%s",
        stats["pdfs_processed"], stats["pages_matched"], stats["total_data_rows"], xlsx,
    )

    flat_rows = _flatten(all_results)
    if not flat_rows:
        return pd.DataFrame()

    return pd.DataFrame(flat_rows)


# ─── Public API: Mapping layers (called by full pipeline only) ────────────────

def run_jcc_mapping(
    *,
    jcc_excel_path: Path | str | None = None,
    effectiveness_df: pd.DataFrame | None = None,
    effectiveness_excel_path: Path | str | None = None,
    effectiveness_output_dir: Path | str | None = None,
    jcc_output_excel_path: Path | str | None = None,
    jcc_mapped_excel_path: Path | str | None = None,
    mapped_excel_path: Path | str | None = None,
    mapped_df: pd.DataFrame | None = None,
    layer4_excel_path: Path | str | None = None,
    cmets_excel_path: Path | str | None = None,
    jcc_cache_dir: Path | str | None = None,
) -> None:
    """Run JCC mapping layers (Output Layer + Layer 4).

    This is separate from extraction and should only be called by the
    full pipeline orchestrator (extract_main.py) after ALL source
    extractions have completed.
    """
    from pipeline.jcc_handler.jcc_output_layer import run_jcc_output_layer, run_layer4_excel

    # Load JCC results from cache
    cache_dir = Path(jcc_cache_dir).resolve() if jcc_cache_dir else JCC_OUTPUT_DIR
    all_results: list[dict] = []
    if cache_dir.exists():
        for json_file in sorted(cache_dir.glob("*.json")):
            try:
                all_results.append(_load_json(json_file))
            except Exception:
                continue

    if not all_results:
        print("\n[JCC Mapping] No JCC extraction results found — skipping mapping.")
        return

    print(f"\n[JCC Mapping] Loaded {len(all_results)} cached PDFs for mapping")

    # ── JCC Output Layer — GNA / TGNA cross-reference ─────────────────────
    try:
        jcc_output_df = run_jcc_output_layer(
            jcc_results              = all_results,
            effectiveness_excel_path = effectiveness_excel_path,
            effectiveness_df         = effectiveness_df,
            effectiveness_output_dir = effectiveness_output_dir,
            output_excel_path        = jcc_output_excel_path,
            mapped_output_excel_path = jcc_mapped_excel_path,
        )
        print(f"\n[JCC] ✓ Output Layer complete — {len(jcc_output_df)} rows")
    except Exception as exc:
        logger.error("[JCC] Output Layer failed: %s", exc)
        print(f"\n[JCC] ⚠ Output Layer failed: {exc}")

    # ── Layer 4 — CMETS-first JCC mapping + GNA / TGNA ─────────────────
    try:
        layer4_df = run_layer4_excel(
            jcc_results       = all_results,
            cmets_excel_path  = cmets_excel_path,
            mapped_excel_path = mapped_excel_path,
            mapped_df         = mapped_df,
            output_excel_path = layer4_excel_path,
        )
        print(f"\n[JCC] ✓ Layer 4 complete — {len(layer4_df)} rows")
    except Exception as exc:
        logger.error("[JCC] Layer 4 failed: %s", exc)
        print(f"\n[JCC] ⚠ Layer 4 failed: {exc}")
