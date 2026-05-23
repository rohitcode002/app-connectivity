"""
final_mapping_handler/runner.py — Sequential Mapping Pipeline Runner
======================================================================
Orchestrates the full mapping chain after all 4 individual extractions
are complete:

    Step 1: CMETS + Effectiveness  → 03_cmets_effectiveness_mapped.xlsx
            - Matches CMETS rows to effectiveness data via GNA/LTA/5.2 IDs
            - Adds/updates: Updated Date, Updated GNA Operationalization Date,
              GNA Operationalization (Yes/No), enrichment columns

    Step 2: Step1 + JCC            → 06_cmets_jcc_mapped.xlsx
            - Matches via GNA/LTA/5.2 IDs found in JCC connectivity_applicant
            - Adds: TGNA, GNA columns

    Step 3: Step2 + Bay Allocation → 07_final_mapped.xlsx
            - Matches via developer name + voltage level
            - Adds: Bay No (Bay Allocation), Substation Coordinates (Bay Allocation)

All mapping uses the CMETS excel as the base — values are picked from
CMETS rows and searched in the other source data. If a match is found,
the new column values are added; otherwise they remain blank.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from pipeline.mapping_handler.lookup import build_lookup
from pipeline.mapping_handler.merge import merge_rows
from pipeline.mapping_handler.formatting import format_mapped_excel
from pipeline.effectiveness_handler.date_updater import (
    update_gna_dates,
    update_additional_capacity_dates,
)
from pipeline.effectiveness_handler.capacity_calculator import compute_installed_capacity
from pipeline.jcc_handler.jcc_output_layer import (
    flatten_jcc_data,
    compute_gna_tgna,
    _collect_cmets_id_columns,
    _candidate_ids_from_cmets_row,
    _find_jcc_by_any_cmets_id,
    extract_bay_no_from_jcc_ists_scope,
)
from pipeline.bay_mapping_handler.lookup import build_bay_lookup
from pipeline.bay_mapping_handler.merge import merge_bay_allocation
from pipeline.shared_utils import safe_str

logger = logging.getLogger(__name__)

_START_DIR = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — CMETS + Effectiveness Mapping
# ─────────────────────────────────────────────────────────────────────────────

def _step1_effectiveness_mapping(
    cmets_df: pd.DataFrame,
    effectiveness_output_dir: Path,
    output_excel: Path,
) -> tuple[pd.DataFrame, dict]:
    """Merge CMETS with effectiveness data.

    Adds/updates:
        - Name of Developers, Substation, State, Application Quantum
          (overwritten from effectiveness when matched)
        - Region, Type of Project, Installed capacity MW breakdowns
        - GNA Operationalization Date (updated to later date from effectiveness)
        - GNA Operationalization (Yes/No) (recomputed)
        - Date from which additional capacity is to be added (updated)
        - Installed/Break-up Capacity (MW) columns

    Returns (enriched_df, combined_stats).
    """
    print("\n" + "=" * 64)
    print("  STEP 1 — CMETS × EFFECTIVENESS MAPPING")
    print("=" * 64)
    print(f"  CMETS rows             : {len(cmets_df)}")
    print(f"  Effectiveness cache    : {effectiveness_output_dir}")
    print(f"  Output Excel           : {output_excel}")
    print("=" * 64)

    # Build effectiveness lookup from on-disk JSON cache
    lookup = build_lookup(pd.DataFrame(), effectiveness_output_dir)
    if not lookup:
        logger.warning("[Step 1] No effectiveness data — output mirrors CMETS.")
        print("[Step 1] WARNING: No effectiveness data found.")
    print(f"[Step 1] Effectiveness lookup: {len(lookup)} unique application IDs")

    # Merge rows (update overlapping columns + add enrichment columns)
    enriched_df, merge_stats = merge_rows(cmets_df, lookup)
    print(
        f"[Step 1] Merge: "
        f"GNA={merge_stats['matched_gna']} | "
        f"LTA={merge_stats['matched_lta']} | "
        f"5.2={merge_stats['matched_52']} | "
        f"Unmatched={merge_stats['unmatched']} | "
        f"Total={merge_stats['total_rows']}"
    )

    # GNA Operationalization Date update
    if lookup:
        enriched_df, date_stats = update_gna_dates(enriched_df, lookup)
        print(
            f"[Step 1] GNA Date Update: "
            f"Matched={date_stats['matched']} | "
            f"Updated={date_stats['updated_date']} | "
            f"Kept same={date_stats['kept_same']} | "
            f"No eff date={date_stats['no_eff_date']}"
        )

    # Additional Capacity Date update
    if lookup:
        enriched_df, add_stats = update_additional_capacity_dates(enriched_df, lookup)
        print(
            f"[Step 1] Additional Capacity Date: "
            f"Matched={add_stats['matched']} | "
            f"Updated={add_stats['updated_date']} | "
            f"Kept same={add_stats['kept_same']} | "
            f"No eff date={add_stats['no_eff_date']}"
        )

    # Installed/Break-up Capacity computation
    if lookup:
        enriched_df, cap_stats = compute_installed_capacity(enriched_df, lookup)
        print(
            f"[Step 1] Installed Capacity: "
            f"Matched={cap_stats['matched']} | "
            f"Computed={cap_stats['computed']} | "
            f"Skipped={cap_stats['skipped']}"
        )

    # Write intermediate Excel
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    enriched_df.to_excel(str(output_excel), index=False, sheet_name="CMETS+Effectiveness")
    format_mapped_excel(str(output_excel))
    print(f"\n[Step 1] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return enriched_df, merge_stats


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — + JCC Mapping (TGNA / GNA)
# ─────────────────────────────────────────────────────────────────────────────

def _load_jcc_results(jcc_cache_dir: Path) -> list[dict]:
    """Load all JCC extraction results from JSON cache."""
    import json
    results: list[dict] = []
    if not jcc_cache_dir.exists():
        return results
    for jf in sorted(jcc_cache_dir.glob("*.json")):
        try:
            with open(jf, "r", encoding="utf-8") as fh:
                results.append(json.load(fh))
        except Exception:
            continue
    return results


def _step2_jcc_mapping(
    df: pd.DataFrame,
    jcc_cache_dir: Path,
    output_excel: Path,
) -> pd.DataFrame:
    """Map CMETS (enriched with effectiveness) to JCC data.

    For each CMETS row, pick GNA/LTA/5.2 IDs and search in JCC
    connectivity_applicant. If matched, compute TGNA and GNA from
    the matched JCC row and add them as new columns.

    Adds: TGNA, GNA, Match Source, Matched JCC ID, Bay No (JCC)
    """
    print("\n" + "=" * 64)
    print("  STEP 2 — CMETS × JCC MAPPING (TGNA / GNA)")
    print("=" * 64)
    print(f"  Input rows      : {len(df)}")
    print(f"  JCC cache dir   : {jcc_cache_dir}")
    print(f"  Output Excel    : {output_excel}")
    print("=" * 64)

    # Load JCC results from cache
    jcc_results = _load_jcc_results(jcc_cache_dir)
    jcc_rows = flatten_jcc_data(jcc_results)
    print(f"[Step 2] JCC cache files loaded: {len(jcc_results)}")
    print(f"[Step 2] JCC rows available: {len(jcc_rows)}")

    if not jcc_rows:
        print("[Step 2] ⚠ No JCC rows — TGNA/GNA columns will be empty.")
        df["TGNA"] = None
        df["GNA"] = None
        df["Match Source"] = None
        df["Matched JCC ID"] = None
        df["Bay No (JCC)"] = None
        df.to_excel(str(output_excel), index=False, sheet_name="CMETS+Effectiveness+JCC")
        format_mapped_excel(str(output_excel))
        print(f"\n[Step 2] ✓ Excel saved → {output_excel}")
        print("=" * 64)
        return df

    # Identify ID columns in CMETS
    id_columns = _collect_cmets_id_columns(df)
    print("[Step 2] CMETS ID cols:")
    for source, col in id_columns:
        print(f"    {source:<3} → {col}")
    print("-" * 64)

    # Match each CMETS row → JCC
    tgna_values: list = []
    gna_values: list = []
    match_sources: list[str] = []
    matched_ids: list[str] = []
    jcc_bay_values: list[str] = []

    matched_count = 0
    gna_count = 0
    tgna_count = 0
    jcc_bay_count = 0
    match_by = {"Application ID": 0, "GNA": 0, "LTA": 0, "5.2": 0}

    for idx, row in df.iterrows():
        id_candidates = _candidate_ids_from_cmets_row(row, id_columns)

        if not id_candidates:
            tgna_values.append(None)
            gna_values.append(None)
            match_sources.append("")
            matched_ids.append("")
            jcc_bay_values.append("")
            continue

        jcc_match, source, matched_id = _find_jcc_by_any_cmets_id(id_candidates, jcc_rows)

        if jcc_match is None:
            tgna_values.append(None)
            gna_values.append(None)
            match_sources.append("")
            matched_ids.append("")
            jcc_bay_values.append("")
            continue

        matched_count += 1
        match_by[source] = match_by.get(source, 0) + 1

        gna_val, tgna_val = compute_gna_tgna(jcc_match)

        if gna_val is not None:
            gna_count += 1
        if tgna_val is not None:
            tgna_count += 1

        tgna_values.append(tgna_val)
        gna_values.append(gna_val)
        match_sources.append(source)
        matched_ids.append(matched_id)

        jcc_bay_no = extract_bay_no_from_jcc_ists_scope(jcc_match)
        if jcc_bay_no:
            jcc_bay_count += 1
        jcc_bay_values.append(jcc_bay_no)

    # Append columns
    df["TGNA"] = tgna_values
    df["GNA"] = gna_values
    df["Match Source"] = match_sources
    df["Matched JCC ID"] = matched_ids
    df["Bay No (JCC)"] = jcc_bay_values

    # Print summary
    print(f"\n[Step 2] Results:")
    print(f"    Total rows              : {len(df)}")
    print(f"    Matched to JCC          : {matched_count}")
    print(f"      via Application ID    : {match_by.get('Application ID', 0)}")
    print(f"      via GNA ID            : {match_by.get('GNA', 0)}")
    print(f"      via LTA ID            : {match_by.get('LTA', 0)}")
    print(f"      via 5.2 Enhancement   : {match_by.get('5.2', 0)}")
    print(f"    GNA values populated    : {gna_count}")
    print(f"    TGNA values populated   : {tgna_count}")
    print(f"    JCC bay numbers found   : {jcc_bay_count}")
    print(f"    Unmatched               : {len(df) - matched_count}")

    # Write intermediate Excel
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_excel), index=False, sheet_name="CMETS+Effectiveness+JCC")
    format_mapped_excel(str(output_excel))
    print(f"\n[Step 2] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — + Bay Allocation Mapping
# ─────────────────────────────────────────────────────────────────────────────

def _step3_bay_mapping(
    df: pd.DataFrame,
    bay_cache_dir: Path,
    output_excel: Path,
) -> pd.DataFrame:
    """Map CMETS (enriched with effectiveness + JCC) to Bay Allocation data.

    Matches developer name + voltage level to bay allocation entries.

    Adds: Bay No (Bay Allocation), Substation Name (Bay Allocation),
          Substation Coordinates (Bay Allocation), Bay No Source
    """
    print("\n" + "=" * 64)
    print("  STEP 3 — CMETS × BAY ALLOCATION MAPPING")
    print("=" * 64)
    print(f"  Input rows             : {len(df)}")
    print(f"  Bay Allocation cache   : {bay_cache_dir}")
    print(f"  Output Excel           : {output_excel}")
    print("=" * 64)

    # Build bay allocation lookup
    bay_index = build_bay_lookup(bay_cache_dir)
    total_220 = len(bay_index.get("220kv", []))
    total_400 = len(bay_index.get("400kv", []))
    print(f"[Step 3] Bay index: 220kV={total_220} entries, 400kV={total_400} entries")

    if total_220 + total_400 == 0:
        print("[Step 3] WARNING: No bay allocation data found.")
        print("[Step 3]  → Run Module 5 (Bay Allocation Extraction) first.")

    # Merge
    enriched_df, stats = merge_bay_allocation(df, bay_index)
    print(
        f"[Step 3] Results: "
        f"JCC bay used={stats['jcc_bay_used']} | "
        f"Matched={stats['matched']} | "
        f"Multi-match={stats['multi_match']} | "
        f"No voltage={stats['no_voltage']} | "
        f"No developer={stats['no_developer']} | "
        f"Unmatched={stats['unmatched']} | "
        f"Skipped existing coords={stats['skipped_existing_coordinates']} | "
        f"Skipped fixed bay no={stats['skipped_fixed_bay_no']} | "
        f"Total={stats['total_rows']}"
    )

    # Write final Excel
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    enriched_df.to_excel(str(output_excel), index=False, sheet_name="Final Mapped Data")
    format_mapped_excel(str(output_excel))
    print(f"\n[Step 3] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return enriched_df


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API — Full Mapping Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_full_mapping_pipeline(
    start_dir: Path | None = None,
) -> Path:
    """Run the complete sequential mapping pipeline.

    Prerequisites: All 4 individual extractions must have completed:
        - 01_cmets_extracted.xlsx
        - 02_effectiveness_extracted.xlsx (or effectiveness JSON cache)
        - 04_jcc_extracted.xlsx (or JCC JSON cache)
        - 05_bayallocation_extracted.xlsx (or bay allocation JSON cache)

    Pipeline:
        Step 1: CMETS + Effectiveness  → 03_cmets_effectiveness_mapped.xlsx
        Step 2: Step1  + JCC           → 06_cmets_jcc_mapped.xlsx
        Step 3: Step2  + Bay Allocation → 07_final_mapped.xlsx

    Returns the path to the final output Excel.
    """
    root = start_dir or _START_DIR
    excel_root = root / "excels"
    output_root = root / "output"

    # Input paths
    cmets_excel = excel_root / "01_cmets_extracted.xlsx"
    effectiveness_cache = output_root / "effectiveness_cache"
    jcc_cache = output_root / "jcc_cache"
    bay_cache = output_root / "bayallocation_cache"

    # Output paths
    step1_excel = excel_root / "03_cmets_effectiveness_mapped.xlsx"
    step2_excel = excel_root / "06_cmets_jcc_mapped.xlsx"
    step3_excel = excel_root / "07_final_mapped.xlsx"

    print("\n" + "█" * 64)
    print("  SEQUENTIAL MAPPING PIPELINE")
    print("  ─────────────────────────────────────────────────────────")
    print(f"  Base CMETS Excel      : {cmets_excel}")
    print(f"  Step 1 output         : {step1_excel}")
    print(f"  Step 2 output         : {step2_excel}")
    print(f"  Step 3 output (FINAL) : {step3_excel}")
    print("█" * 64)

    # ── Load CMETS base data ──────────────────────────────────────────────
    if not cmets_excel.exists():
        raise FileNotFoundError(
            f"CMETS Excel not found: {cmets_excel}. "
            "Run Module 1 (CMETS extraction) first."
        )
    cmets_df = pd.read_excel(cmets_excel, sheet_name=0)
    print(f"\n[Pipeline] Base CMETS rows loaded: {len(cmets_df)}")

    if cmets_df.empty:
        print("[Pipeline] WARNING: CMETS DataFrame is empty — nothing to map.")
        return step3_excel

    # ── Step 1: CMETS + Effectiveness ─────────────────────────────────────
    step1_df, _ = _step1_effectiveness_mapping(
        cmets_df=cmets_df.copy(),
        effectiveness_output_dir=effectiveness_cache,
        output_excel=step1_excel,
    )

    # ── Step 2: Step1 + JCC ───────────────────────────────────────────────
    step2_df = _step2_jcc_mapping(
        df=step1_df.copy(),
        jcc_cache_dir=jcc_cache,
        output_excel=step2_excel,
    )

    # ── Step 3: Step2 + Bay Allocation ────────────────────────────────────
    final_df = _step3_bay_mapping(
        df=step2_df.copy(),
        bay_cache_dir=bay_cache,
        output_excel=step3_excel,
    )

    # ── Final summary ─────────────────────────────────────────────────────
    print("\n" + "█" * 64)
    print("  MAPPING PIPELINE COMPLETE")
    print("  ─────────────────────────────────────────────────────────")
    print(f"  Total rows        : {len(final_df)}")
    print(f"  Total columns     : {len(final_df.columns)}")
    print(f"  Columns:")
    for col in final_df.columns:
        non_null = final_df[col].notna().sum()
        print(f"    {col:<55} {non_null}/{len(final_df)} filled")
    print(f"\n  Step 1 → {step1_excel}")
    print(f"  Step 2 → {step2_excel}")
    print(f"  Step 3 → {step3_excel} (FINAL)")
    print("█" * 64)

    return step3_excel
