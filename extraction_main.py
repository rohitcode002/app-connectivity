"""
extraction_main.py — DTBC (Data To Be Captured) Full Pipeline
================================================================
Sequential pipeline that extracts, maps, and formats all data sources.

Phase 1 — Sequential Extractions (order matters):
    1. CMETS           → excels/01_cmets_extracted.xlsx
    2. JCC             → excels/04_jcc_extracted.xlsx
    3. Effectiveness   → excels/02_effectiveness_extracted.xlsx
    4. Bay Allocation  → excels/05_bayallocation_extracted.xlsx
    If an Excel is missing but JSON cache exists, rebuild from cache.

Phase 2 — Sequential Mappings (each uses CMETS as base):
    1. CMETS × JCC             → excels/06_cmets_jcc_mapped.xlsx
    2. CMETS × Effectiveness   → excels/03_cmets_effectiveness_mapped.xlsx
    3. CMETS × Bay Allocation  → excels/07_cmets_bayallocation_mapped.xlsx

Phase 3 — Final Combined Output:
    → excels/final_dtbc.xlsx   (all mapped data combined)

Phase 4 — Formatting:
    → excels/dtbc_formatted.xlsx  (final formatted output)
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# ── Neutralise broken xlrd (Python-2-era version) before pandas imports it ──
# pandas internally does `import_optional_dependency("xlrd")` even when
# engine="openpyxl" is specified.  If an incompatible xlrd is installed the
# import itself raises SyntaxError.  We catch that and tell Python to
# treat xlrd as unavailable so pandas falls back gracefully.
try:
    import xlrd  # noqa: F401 — test-import only
except (SyntaxError, ImportError):
    sys.modules["xlrd"] = None  # type: ignore[assignment]

import pandas as pd

from config import load_runtime_config

_START_DIR = Path(__file__).resolve().parent


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _banner(title: str, char: str = "█") -> None:
    print(f"\n{char * 64}")
    print(f"  {title}")
    print(f"{char * 64}")


def _sub_banner(title: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {title}")
    print(f"{'─' * 64}")


def _rebuild_excel_from_cache(cache_dir: Path, excel_path: Path, source_name: str) -> bool:
    """Rebuild an Excel from JSON cache files when the Excel is missing.

    Returns True if JSON cache was found and Excel was generated.
    """
    if not cache_dir.exists():
        print(f"  [!] No cache directory found for {source_name}: {cache_dir}")
        return False

    json_files = sorted(cache_dir.glob("*.json"))
    if not json_files:
        print(f"  [!] No JSON cache files found for {source_name} in {cache_dir}")
        return False

    print(f"  [*] Found {len(json_files)} cached JSON files for {source_name}")
    print(f"  [*] Rebuilding Excel from cache → {excel_path.name}")

    all_data = []
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as fh:
                all_data.append(json.load(fh))
        except Exception as exc:
            print(f"  [!] Could not read {jf.name}: {exc}")

    if not all_data:
        print(f"  [!] No valid JSON data found for {source_name}")
        return False

    # Flatten all records into a single list of dicts
    flat_rows = []
    for data in all_data:
        # Handle different JSON structures per source
        if "results" in data:
            # CMETS structure
            for page in data.get("results", []):
                for row in page.get("rows", []):
                    if isinstance(row, dict):
                        flat_rows.append(row)
        elif "pages" in data:
            # JCC / BayAllocation structure
            source = data.get("source", "")
            for page in data.get("pages", []):
                pnum = page.get("page_number")
                for row in page.get("rows", page.get("substations", [])):
                    if isinstance(row, dict):
                        rec = {"source_pdf": source, "page_number": pnum}
                        rec.update(row)
                        flat_rows.append(rec)
        elif isinstance(data, list):
            # Effectiveness structure (list of records)
            flat_rows.extend(data)

    if flat_rows:
        df = pd.DataFrame(flat_rows)
        excel_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(str(excel_path), index=False, sheet_name="Extracted Data")
        print(f"  [✓] Rebuilt {excel_path.name} with {len(df)} rows")
        return True

    print(f"  [!] No rows extracted from cache for {source_name}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Individual Extractions
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_cmets(runtime, excel_root: Path, output_root: Path) -> Path:
    """Step 1: Extract CMETS data."""
    from pipeline.cmets_handler.runner import run_cmets_extraction

    excel_path = excel_root / "01_cmets_extracted.xlsx"
    cache_dir = output_root / "cmets_cache"

    _sub_banner("STEP 1 — CMETS EXTRACTION")
    print(f"  Excel  : {excel_path}")
    print(f"  Cache  : {cache_dir}")

    # Skip if output Excel already exists (resume-safe)
    if excel_path.exists():
        print(f"  [✓] SKIP — Excel already exists: {excel_path.name}")
        return excel_path

    try:
        result_path = run_cmets_extraction(
            output_dir=str(cache_dir),
            excel_path=str(excel_path),
            runtime=runtime,
            max_pages=runtime.max_pages,
        )
        print(f"  [✓] CMETS extraction complete → {result_path}")
        return Path(result_path)
    except Exception as exc:
        print(f"  [!] CMETS extraction failed: {exc}")
        traceback.print_exc()
        # Try rebuilding from cache
        if not excel_path.exists():
            _rebuild_excel_from_cache(cache_dir, excel_path, "CMETS")
        return excel_path


def _extract_jcc(runtime, excel_root: Path, output_root: Path) -> Path:
    """Step 2: Extract JCC data."""
    from pipeline.jcc_handler.runner import run_jcc_extraction

    excel_path = excel_root / "04_jcc_extracted.xlsx"
    cache_dir = output_root / "jcc_cache"

    _sub_banner("STEP 2 — JCC EXTRACTION")
    print(f"  Excel  : {excel_path}")
    print(f"  Cache  : {cache_dir}")

    # Skip if output Excel already exists (resume-safe)
    if excel_path.exists():
        print(f"  [✓] SKIP — Excel already exists: {excel_path.name}")
        return excel_path

    try:
        run_jcc_extraction(
            output_dir=str(cache_dir),
            excel_path=str(excel_path),
            runtime=runtime,
            max_pages=runtime.max_pages,
        )
        print(f"  [✓] JCC extraction complete → {excel_path.name}")
    except Exception as exc:
        print(f"  [!] JCC extraction failed: {exc}")
        traceback.print_exc()
        if not excel_path.exists():
            _rebuild_excel_from_cache(cache_dir, excel_path, "JCC")

    return excel_path


def _extract_effectiveness(runtime, excel_root: Path, output_root: Path) -> Path:
    """Step 3: Extract Effectiveness data."""
    from pipeline.effectiveness_handler.runner import run_effectiveness_extraction

    excel_path = excel_root / "02_effectiveness_extracted.xlsx"
    cache_dir = output_root / "effectiveness_cache"

    _sub_banner("STEP 3 — EFFECTIVENESS EXTRACTION")
    print(f"  Excel  : {excel_path}")
    print(f"  Cache  : {cache_dir}")

    # Skip if output Excel already exists (resume-safe)
    if excel_path.exists():
        print(f"  [✓] SKIP — Excel already exists: {excel_path.name}")
        return excel_path

    try:
        run_effectiveness_extraction(
            output_dir=str(cache_dir),
            excel_path=str(excel_path),
            runtime=runtime,
            max_pages=runtime.max_pages,
        )
        print(f"  [✓] Effectiveness extraction complete → {excel_path.name}")
    except Exception as exc:
        print(f"  [!] Effectiveness extraction failed: {exc}")
        traceback.print_exc()
        if not excel_path.exists():
            _rebuild_excel_from_cache(cache_dir, excel_path, "Effectiveness")

    return excel_path


def _extract_bayallocation(runtime, excel_root: Path, output_root: Path) -> Path:
    """Step 4: Extract Bay Allocation data."""
    from pipeline.bayallocation_handler.runner import run_bayallocation_extraction

    excel_path = excel_root / "05_bayallocation_extracted.xlsx"
    cache_dir = output_root / "bayallocation_cache"

    _sub_banner("STEP 4 — BAY ALLOCATION EXTRACTION")
    print(f"  Excel  : {excel_path}")
    print(f"  Cache  : {cache_dir}")

    # Skip if output Excel already exists (resume-safe)
    if excel_path.exists():
        print(f"  [✓] SKIP — Excel already exists: {excel_path.name}")
        return excel_path

    try:
        run_bayallocation_extraction(
            output_dir=str(cache_dir),
            excel_path=str(excel_path),
            runtime=runtime,
            max_pages=runtime.max_pages,
        )
        print(f"  [✓] Bay Allocation extraction complete → {excel_path.name}")
    except Exception as exc:
        print(f"  [!] Bay Allocation extraction failed: {exc}")
        traceback.print_exc()
        if not excel_path.exists():
            _rebuild_excel_from_cache(cache_dir, excel_path, "BayAllocation")

    return excel_path


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Sequential Mappings (each uses CMETS as base)
# ═══════════════════════════════════════════════════════════════════════════════

def _map_jcc(cmets_df: pd.DataFrame, output_root: Path, excel_root: Path) -> tuple[pd.DataFrame, Path]:
    """Map CMETS × JCC → 06_cmets_jcc_mapped.xlsx"""
    from pipeline.final_mapping_handler.runner import _step2_jcc_mapping

    output_excel = excel_root / "06_cmets_jcc_mapped.xlsx"
    jcc_cache = output_root / "jcc_cache"

    _sub_banner("MAPPING 1 — CMETS × JCC")
    mapped_df = _step2_jcc_mapping(
        df=cmets_df.copy(),
        jcc_cache_dir=jcc_cache,
        output_excel=output_excel,
    )
    print(f"  [✓] JCC mapping complete → {output_excel.name}")
    return mapped_df, output_excel


def _map_effectiveness(
    cmets_df: pd.DataFrame, output_root: Path, excel_root: Path
) -> tuple[pd.DataFrame, Path]:
    """Map CMETS × Effectiveness → 03_cmets_effectiveness_mapped.xlsx"""
    from pipeline.final_mapping_handler.runner import _step1_effectiveness_mapping

    output_excel = excel_root / "03_cmets_effectiveness_mapped.xlsx"
    eff_cache = output_root / "effectiveness_cache"

    _sub_banner("MAPPING 2 — CMETS × EFFECTIVENESS")
    mapped_df, _ = _step1_effectiveness_mapping(
        cmets_df=cmets_df.copy(),
        effectiveness_output_dir=eff_cache,
        output_excel=output_excel,
    )
    print(f"  [✓] Effectiveness mapping complete → {output_excel.name}")
    return mapped_df, output_excel


def _map_bayallocation(
    df: pd.DataFrame, output_root: Path, excel_root: Path
) -> tuple[pd.DataFrame, Path]:
    """Map (enriched CMETS) × Bay Allocation → 07_cmets_bayallocation_mapped.xlsx"""
    from pipeline.final_mapping_handler.runner import _step3_bay_mapping

    output_excel = excel_root / "07_cmets_bayallocation_mapped.xlsx"
    bay_cache = output_root / "bayallocation_cache"

    _sub_banner("MAPPING 3 — CMETS × BAY ALLOCATION")
    mapped_df = _step3_bay_mapping(
        df=df.copy(),
        bay_cache_dir=bay_cache,
        output_excel=output_excel,
    )
    print(f"  [✓] Bay Allocation mapping complete → {output_excel.name}")
    return mapped_df, output_excel


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Generate final_dtbc.xlsx
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_final_dtbc(final_mapped_excel: Path, excel_root: Path) -> Path:
    """Generate final_dtbc.xlsx from the fully mapped data."""
    from pipeline.final_mapping_handler.data_capture import generate_data_to_be_captured

    output_excel = excel_root / "final_dtbc.xlsx"

    _sub_banner("PHASE 3 — GENERATE final_dtbc.xlsx")
    print(f"  Source  : {final_mapped_excel}")
    print(f"  Output  : {output_excel}")

    result_path = generate_data_to_be_captured(
        final_mapped_excel=final_mapped_excel,
        output_excel=output_excel,
    )
    print(f"  [✓] final_dtbc.xlsx generated → {result_path}")
    return Path(result_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4 — Formatting → dtbc_formatted.xlsx
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_dtbc_formatting(source_excel: Path, excel_root: Path) -> Path:
    """Apply column formatting to final_dtbc.xlsx → dtbc_formatted.xlsx."""
    from pipeline.mapping_handler.formatting import format_mapped_excel
    import shutil

    output_excel = excel_root / "dtbc_formatted.xlsx"

    _sub_banner("PHASE 4 — FORMATTING → dtbc_formatted.xlsx")
    print(f"  Source  : {source_excel}")
    print(f"  Output  : {output_excel}")

    # Copy final_dtbc.xlsx to dtbc_formatted.xlsx, then apply formatting
    shutil.copy2(str(source_excel), str(output_excel))

    # Apply the standard professional formatting
    format_mapped_excel(str(output_excel))
    print(f"  [✓] Formatting applied → {output_excel.name}")

    return output_excel


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DTBC Pipeline — Extract, Map, Format"
    )
    parser.add_argument("--mode", choices=["vm", "laptop"], default=None,
                        help="Override execution mode (default: vm)")
    parser.add_argument("--api-key", default=None,
                        help="OpenAI API key (laptop mode override)")
    parser.add_argument("--llm-script", default=None,
                        help="Path to llm_client.bat (vm mode override)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max pages to process per PDF (-1 = all)")
    parser.add_argument("--extract-only", action="store_true",
                        help="Run only extraction phase (skip mapping + formatting)")
    parser.add_argument("--map-only", action="store_true",
                        help="Run only mapping + formatting (skip extraction)")
    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = _build_args()
    runtime = load_runtime_config(
        mode_override=args.mode,
        api_key_override=args.api_key,
        llm_script_override=args.llm_script,
        max_pages_override=args.max_pages,
    )

    excel_root = _START_DIR / "excels"
    output_root = _START_DIR / "output"
    excel_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    cmets_excel = excel_root / "01_cmets_extracted.xlsx"

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1 — SEQUENTIAL EXTRACTIONS
    # ══════════════════════════════════════════════════════════════════════
    if not args.map_only:
        _banner("PHASE 1 — SEQUENTIAL EXTRACTIONS")
        print(f"  Mode          : {runtime.execution_target}")
        print(f"  Max pages/PDF : {runtime.max_pages if runtime.max_pages != -1 else 'ALL'}")

        # 1. CMETS (must be first — base for all mappings)
        _extract_cmets(runtime, excel_root, output_root)

        # 2. JCC
        _extract_jcc(runtime, excel_root, output_root)

        # 3. Effectiveness
        _extract_effectiveness(runtime, excel_root, output_root)

        # 4. Bay Allocation
        _extract_bayallocation(runtime, excel_root, output_root)

        print(f"\n{'═' * 64}")
        print("  PHASE 1 COMPLETE — All extractions finished")
        print(f"{'═' * 64}")
    else:
        print("\n[Pipeline] Skipping extraction phase (--map-only)")

    if args.extract_only:
        print("\n[Pipeline] Skipping mapping phase (--extract-only)")
        _banner("PIPELINE COMPLETE (extract-only)")
        return

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2 — SEQUENTIAL MAPPINGS
    # ══════════════════════════════════════════════════════════════════════
    _banner("PHASE 2 — SEQUENTIAL MAPPINGS")

    # Load CMETS base data
    if not cmets_excel.exists():
        print(f"  [!] CMETS Excel not found: {cmets_excel}")
        print("  [!] Cannot proceed with mapping. Run extraction first.")
        sys.exit(1)

    cmets_df = pd.read_excel(cmets_excel, sheet_name=0, engine="openpyxl")
    print(f"  Base CMETS rows loaded: {len(cmets_df)}")

    if cmets_df.empty:
        print("  [!] CMETS DataFrame is empty — nothing to map.")
        _banner("PIPELINE COMPLETE (no data)")
        return

    # Mapping 1: CMETS × Effectiveness → enriched_df
    try:
        eff_df, eff_excel = _map_effectiveness(cmets_df, output_root, excel_root)
    except Exception as exc:
        print(f"  [!] Effectiveness mapping failed: {exc}")
        traceback.print_exc()
        eff_df = cmets_df.copy()

    # Mapping 2: enriched_df × JCC → jcc_df
    try:
        jcc_df, jcc_excel = _map_jcc(eff_df, output_root, excel_root)
    except Exception as exc:
        print(f"  [!] JCC mapping failed: {exc}")
        traceback.print_exc()
        jcc_df = eff_df.copy()

    # Mapping 3: jcc_df × Bay Allocation → final_df
    try:
        final_df, final_mapped_excel = _map_bayallocation(jcc_df, output_root, excel_root)
    except Exception as exc:
        print(f"  [!] Bay Allocation mapping failed: {exc}")
        traceback.print_exc()
        final_mapped_excel = jcc_excel if 'jcc_excel' in dir() else excel_root / "06_cmets_jcc_mapped.xlsx"

    print(f"\n{'═' * 64}")
    print("  PHASE 2 COMPLETE — All mappings finished")
    print(f"{'═' * 64}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3 — GENERATE final_dtbc.xlsx
    # ══════════════════════════════════════════════════════════════════════
    try:
        dtbc_excel = _generate_final_dtbc(final_mapped_excel, excel_root)
    except Exception as exc:
        print(f"  [!] final_dtbc.xlsx generation failed: {exc}")
        traceback.print_exc()
        _banner("PIPELINE FAILED at Phase 3")
        return

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4 — FORMATTING → dtbc_formatted.xlsx
    # ══════════════════════════════════════════════════════════════════════
    try:
        formatted_excel = _apply_dtbc_formatting(dtbc_excel, excel_root)
    except Exception as exc:
        print(f"  [!] Formatting failed: {exc}")
        traceback.print_exc()
        _banner("PIPELINE FAILED at Phase 4")
        return

    # ══════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    _banner("DTBC PIPELINE COMPLETE")
    print("  Generated files:")
    print(f"    Phase 1 — Extractions:")
    print(f"      01_cmets_extracted.xlsx")
    print(f"      04_jcc_extracted.xlsx")
    print(f"      02_effectiveness_extracted.xlsx")
    print(f"      05_bayallocation_extracted.xlsx")
    print(f"    Phase 2 — Mappings:")
    print(f"      03_cmets_effectiveness_mapped.xlsx")
    print(f"      06_cmets_jcc_mapped.xlsx")
    print(f"      07_cmets_bayallocation_mapped.xlsx")
    print(f"    Phase 3 — Final Data:")
    print(f"      final_dtbc.xlsx")
    print(f"    Phase 4 — Formatted:")
    print(f"      dtbc_formatted.xlsx")
    print(f"\n  Final output → {formatted_excel}")
    print(f"{'█' * 64}\n")


if __name__ == "__main__":
    main()
