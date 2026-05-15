"""
cmets_handler/runner.py — CMETS Orchestration (discovery, cache, Excel)
=========================================================================
Discovers all PDFs in source/cmets_pdfs/, checks JSON cache in
output/cmets_cache/, extracts only un-cached PDFs, flattens results,
writes excels/cmets.xlsx.

This is the only file that performs I/O orchestration for Module 1.
Edit extraction.py or normalization.py to change how data is extracted
or cleaned — this file only handles discovery, caching, and output.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Optional

from config import RuntimeConfig, load_runtime_config
from pipeline.excel_utils import export_to_excel
from pipeline.cmets_handler.models import PipelineResult, CMETS_COLUMNS
from pipeline.cmets_handler.extraction import run_single_pdf
from pipeline.cmets_handler.meeting_classifier import classify_meeting

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


# ─── Serialisation ────────────────────────────────────────────────────────────

def _serialize(result: PipelineResult, meeting_meta: dict | None = None) -> dict:
    out = result.model_dump()
    for i, pr in enumerate(out["results"]):
        pr["rows"] = [r.model_dump(by_alias=True) for r in result.results[i].rows]
    if meeting_meta:
        out["meeting_meta"] = meeting_meta
    return out


def _save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# Meeting-level columns that are injected from meeting_meta (same for all rows per PDF)
_MEETING_COLS = [
    "CMETS GNA Approved", "CMETS LTA Approved",
    "CMETS GNA Meeting Date", "CMETS LTA Meeting Date",
]


def _flatten(all_serialized: list[dict]) -> list[dict]:
    """Flatten nested per-PDF results into flat rows for Excel."""
    records = []
    for pr in all_serialized:
        pdf_path = pr.get("pdf_path", "")
        # Meeting-level metadata (same for every row from this PDF)
        meeting = pr.get("meeting_meta") or {}
        for page in pr.get("results", []):
            pnum = page.get("page_number")
            for row in page.get("rows", []):
                rec = {"PDF": pdf_path, "Page Number": pnum}
                # Inject meeting columns
                for mcol in _MEETING_COLS:
                    rec[mcol] = meeting.get(mcol)
                # Row-level columns
                for col in CMETS_COLUMNS:
                    if col not in rec:  # skip PDF, Page, meeting cols already set
                        rec[col] = row.get(col)
                rec["Applied Start of Connectivity sought by developer date"
                    "( start date of connectivity as per the application)"] = (
                    row.get("Applied Start of Connectivity sought by developer date")
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

    for idx, pdf_path in enumerate(pdf_paths, 1):
        cache = out / f"{pdf_path.stem}.json"

        if cache.exists():
            print(f"\n[{idx}/{len(pdf_paths)}] SKIP    {pdf_path.name}")
            all_data.append(_load_json(cache))
            continue

        print(f"\n[{idx}/{len(pdf_paths)}] EXTRACT {pdf_path.name}")
        print("-" * 64)

        # ── Pre-extraction: classify meeting type (GNA / LTA) ─────────
        print(f"  [M] Classifying meeting type …", end=" ", flush=True)
        meeting_meta = classify_meeting(str(pdf_path))
        print(
            f"#{meeting_meta.meeting_number or '?'} "
            f"({meeting_meta.meeting_date or 'no date'}) → "
            f"{meeting_meta.classification} "
            f"(GNA:{meeting_meta.gna_count} LTA:{meeting_meta.lta_count})"
        )

        result = run_single_pdf(
            pdf_path=str(pdf_path),
            api_key=runtime.api_key or None,
            vm_mode=runtime.vm_mode,
            llm_script_path=runtime.llm_script_path,
            max_pages=max_pages,
        )
        data = _serialize(result, meeting_meta=meeting_meta.as_row_dict())
        _save_json(data, cache)
        print(f"  → JSON: {cache.name}")
        all_data.append(data)

    runtime_s   = perf_counter() - t0
    finished_at = datetime.now()
    stats       = _agg_stats(all_data)

    print("\n" + "=" * 64)
    print("  CMETS SUMMARY")
    print(f"    PDFs          : {len(pdf_paths)}  (skipped {cached})")
    print(f"    Pages ext.    : {stats['total_pages_extracted']}")
    print(f"    Pages passed  : {stats['total_pages_passed_gate']}")
    print(f"    Total rows    : {stats['total_rows']}")
    print(f"    Runtime (s)   : {runtime_s:.1f}")
    print("=" * 64)

    flat_rows = _flatten(all_data)
    out_path  = export_to_excel(
        rows         = flat_rows,
        output_path  = xlsx,
        sheet_name   = "Extracted Data",
        column_order = CMETS_COLUMNS,
        summary_rows = [
            ("Run started at",           started_at.isoformat(timespec="seconds")),
            ("Run finished at",          finished_at.isoformat(timespec="seconds")),
            ("Total runtime (seconds)",  round(runtime_s, 2)),
            ("PDFs processed",           stats["pdfs_processed"]),
            ("Total pages extracted",    stats["total_pages_extracted"]),
            ("Total pages passed gate",  stats["total_pages_passed_gate"]),
            ("Total pages skipped",      stats["total_pages_skipped"]),
            ("Total rows",               stats["total_rows"]),
        ],
    )
    print(f"\n[CMETS] cmets.xlsx → {out_path}")
    return out_path
