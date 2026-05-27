"""
bayallocation_handler/runner.py — Bay Allocation Orchestration (Module 5)
==========================================================================
Discovers all Bay Allocation PDFs in source/bayallocation/, checks a JSON
cache, extracts un-cached PDFs, and writes per-PDF and combined JSON output.

Each page of a PDF is treated as one independent extraction unit.

Usage (standalone):
    from pipeline.bayallocation_handler import run_bayallocation_extraction
    df = run_bayallocation_extraction()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

from config import RuntimeConfig, load_runtime_config
from pipeline.excel_utils import export_to_excel
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


# ─── Public API ───────────────────────────────────────────────────────────────

def run_bayallocation_extraction(
    source_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    excel_path: Path | str | None = None,
    runtime:    Optional[RuntimeConfig] = None,
    max_pages:  int = -1,
) -> pd.DataFrame:
    """Discover Bay Allocation PDFs → extract (skip cached) → dump JSON → write Excel.

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

    all_results: list[dict] = []

    for idx, pdf_path in enumerate(pdf_files, 1):
        cache = _cache_path(pdf_path.name, out)

        if cache.exists():
            print(f"\n  [{idx}/{len(pdf_files)}] SKIP    {pdf_path.name}")
            all_results.append(_load_json(cache))
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

    # -- Aggregate ─────────────────────────────────────────────────────────────
    total_pdfs         = len(all_results)
    total_pages        = sum(r.get("total_pages", 0) for r in all_results)
    total_substations  = sum(r.get("total_substations", 0) for r in all_results)
    flat_rows          = _flatten(all_results)

    print("\n" + "=" * 64)
    print("  BAY ALLOCATION SUMMARY")
    print(f"    PDFs processed  : {total_pdfs}")
    print(f"    Pages matched   : {total_pages}")
    print(f"    Substations     : {total_substations}")
    print(f"    Bay entries     : {len(flat_rows)}")
    print("=" * 64)

    if not flat_rows:
        return pd.DataFrame()

    df = pd.DataFrame(flat_rows)

    # ── Excel export ─────────────────────────────────────────────────────────
    col_order = [c for c in EXCEL_COLUMNS if c in df.columns]
    export_to_excel(
        rows         = flat_rows,
        output_path  = xlsx,
        sheet_name   = "Bay Allocation Data",
        column_order = col_order,
        summary_rows = [
            ("PDFs processed",  total_pdfs),
            ("Pages matched",   total_pages),
            ("Substations",     total_substations),
            ("Bay entries",     len(flat_rows)),
        ],
    )
    print(f"\n[BayAllocation] Excel → {xlsx}")

    return df


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
