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
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from pipeline.mapping_handler.formatting import format_mapped_excel
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
# Helpers for Step 1 — Effectiveness mapping
# ─────────────────────────────────────────────────────────────────────────────

def _extract_ids(cell_value) -> list[str]:
    """Extract all numeric IDs (6+ digits) from a cell value."""
    text = safe_str(cell_value)
    if not text:
        return []
    return re.findall(r"\b\d{6,}\b", text)


def _is_valid(val) -> bool:
    """Check if a value is non-empty and not a placeholder."""
    if val is None:
        return False
    if isinstance(val, float) and pd.isna(val):
        return False
    return str(val).strip().lower() not in (
        "", "none", "null", "na", "n/a", "-", "--", "nan",
    )


def _safe_float(val) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return 0.0 if pd.isna(val) else float(val)
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


# Matches patterns like: "Solar(40)", "Wind (12)", "BESS(34)", "Hydro (150)"
_TYPE_MW_RE = re.compile(
    r"(solar|wind|bess|ess|hydro|hybrid|psp|pump\s*storage)"
    r"\s*\(\s*([\d,.]+)\s*\)",
    re.IGNORECASE,
)


def _parse_type_mw(type_str: str) -> dict[str, float]:
    """Parse CMETS Type column into keyword → MW mapping.

    Examples:
        "Solar(40)+BESS(34)"  → {"solar": 40.0, "bess": 34.0}
        "Wind (300)"          → {"wind": 300.0}
        "Solar"               → {}  (no MW value)
    """
    result: dict[str, float] = {}
    if not type_str:
        return result
    for match in _TYPE_MW_RE.finditer(type_str):
        keyword = match.group(1).lower().strip()
        mw = _safe_float(match.group(2))
        result[keyword] = result.get(keyword, 0.0) + mw
    return result


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — CMETS + Effectiveness Mapping
# ─────────────────────────────────────────────────────────────────────────────

def _step1_effectiveness_mapping(
    cmets_df: pd.DataFrame,
    effectiveness_excel: Path,
    output_excel: Path,
) -> tuple[pd.DataFrame, dict]:
    """Merge CMETS with effectiveness data (Excel-to-Excel).

    Logic
    -----
    1. Read 02_effectiveness_extracted.xlsx → build application_id lookup.
    2. For each CMETS row, search GNA/LTA/5.2 IDs in the lookup.
    3. When matched, update these columns directly:
         effectiveness.name_of_applicant  → CMETS.Name of Developers
         effectiveness.substation         → CMETS.Substation
         effectiveness.state              → CMETS.State
         effectiveness.expected_date      → CMETS.GNA Operationalization Date
         effectiveness.installed_capacity_mw → CMETS.Application Quantum (MW)(ST II)
    4. Then compute Installed/Break-up Capacity (MW) sub-columns:
         - Parse CMETS Type for MW values (e.g. "Solar(40)+BESS(34)")
         - Match effectiveness type_of_project keyword → choose target column
         - Target column = effectiveness MW + CMETS Type parsed MW

    Output has the EXACT same columns as CMETS — nothing added or removed.

    Returns (enriched_df, stats).
    """
    print("\n" + "=" * 64)
    print("  STEP 1 — CMETS × EFFECTIVENESS MAPPING")
    print("=" * 64)
    print(f"  CMETS rows             : {len(cmets_df)}")
    print(f"  Effectiveness Excel    : {effectiveness_excel}")
    print(f"  Output Excel           : {output_excel}")
    print("=" * 64)

    # ── Load effectiveness data from Excel ────────────────────────────────
    if not effectiveness_excel.exists():
        logger.warning("[Step 1] Effectiveness Excel not found — output mirrors CMETS.")
        print("[Step 1] WARNING: Effectiveness Excel not found.")
        cmets_df.to_excel(str(output_excel), index=False,
                          sheet_name="CMETS+Effectiveness")
        return cmets_df, {"matched_gna": 0, "matched_lta": 0,
                          "matched_52": 0, "unmatched": len(cmets_df),
                          "total_rows": len(cmets_df)}

    eff_df = pd.read_excel(effectiveness_excel, sheet_name=0, engine="openpyxl")
    print(f"[Step 1] Effectiveness rows loaded: {len(eff_df)}")

    # Build application_id → row dict lookup
    eff_lookup: dict[str, dict] = {}
    for _, eff_row in eff_df.iterrows():
        app_id = safe_str(eff_row.get("application_id")).strip()
        if app_id and app_id.lower() not in ("", "none", "nan", "null"):
            eff_lookup[app_id] = eff_row.to_dict()

    print(f"[Step 1] Effectiveness lookup: {len(eff_lookup)} unique application IDs")

    if not eff_lookup:
        print("[Step 1] WARNING: No effectiveness records with application_id.")
        cmets_df.to_excel(str(output_excel), index=False,
                          sheet_name="CMETS+Effectiveness")
        return cmets_df, {"matched_gna": 0, "matched_lta": 0,
                          "matched_52": 0, "unmatched": len(cmets_df),
                          "total_rows": len(cmets_df)}

    # ── Match and update each CMETS row ───────────────────────────────────
    matched_gna = matched_lta = matched_52 = unmatched = 0
    capacity_computed = 0

    for idx, row in cmets_df.iterrows():
        # Extract all IDs from the 3 CMETS ID columns
        gna_ids = _extract_ids(row.get("GNA/ST II Application ID"))
        lta_ids = _extract_ids(row.get("LTA Application ID"))
        enh_ids = _extract_ids(row.get(
            "Application ID under Enhancement 5.2 or revision"))

        # Search effectiveness lookup: GNA → LTA → 5.2 cascade
        eff_rec = None
        match_via = None

        for aid in gna_ids:
            if aid in eff_lookup:
                eff_rec = eff_lookup[aid]
                match_via = "GNA"
                break

        if eff_rec is None:
            for aid in lta_ids:
                if aid in eff_lookup:
                    eff_rec = eff_lookup[aid]
                    match_via = "LTA"
                    break

        if eff_rec is None:
            for aid in enh_ids:
                if aid in eff_lookup:
                    eff_rec = eff_lookup[aid]
                    match_via = "5.2"
                    break

        if eff_rec is None:
            unmatched += 1
            continue

        if match_via == "GNA":
            matched_gna += 1
        elif match_via == "LTA":
            matched_lta += 1
        else:
            matched_52 += 1

        # ── Direct column updates ─────────────────────────────────────
        if _is_valid(eff_rec.get("name_of_applicant")):
            cmets_df.at[idx, "Name of Developers"] = eff_rec["name_of_applicant"]
        if _is_valid(eff_rec.get("substation")):
            cmets_df.at[idx, "Substation"] = eff_rec["substation"]
        if _is_valid(eff_rec.get("state")):
            cmets_df.at[idx, "State"] = eff_rec["state"]
        if _is_valid(eff_rec.get("expected_date")):
            cmets_df.at[idx, "GNA Operationalization Date"] = eff_rec["expected_date"]
        if _is_valid(eff_rec.get("installed_capacity_mw")):
            cmets_df.at[idx, "Application Quantum (MW)(ST II)"] = eff_rec["installed_capacity_mw"]

        # ── Installed/Break-up Capacity computation ───────────────────
        # Parse CMETS Type for MW values: "Solar(40)+BESS(34)" → {"solar": 40, "bess": 34}
        cmets_type_str = safe_str(row.get("Type"))
        cmets_type_mw = _parse_type_mw(cmets_type_str)

        # Check effectiveness type_of_project keyword
        eff_type = safe_str(eff_rec.get("type_of_project")).lower()

        # Effectiveness MW columns
        eff_mw = {
            "solar": _safe_float(eff_rec.get("solar_mw")),
            "wind":  _safe_float(eff_rec.get("wind_mw")),
            "hydro": _safe_float(eff_rec.get("hydro_mw")),
            "ess":   _safe_float(eff_rec.get("ess_mw")),
        }

        # Map: effectiveness type keyword → CMETS capacity column
        _TYPE_TO_CAPACITY_COL = {
            "solar":  "Installed/Break-up Capacity (MW) Solar",
            "wind":   "Installed/Break-up Capacity (MW) Wind",
            "hybrid": "Installed/Break-up Capacity (MW) Hybrid",
            "hydro":  "Installed/Break-up Capacity (MW) Hydro",
        }

        # Map: effectiveness type keyword → which eff_mw key to use
        _TYPE_TO_EFF_KEY = {
            "solar": "solar",
            "wind":  "wind",
            "hydro": "hydro",
            "hybrid": None,  # hybrid sums all
        }

        row_has_capacity = False

        for type_keyword, capacity_col in _TYPE_TO_CAPACITY_COL.items():
            if type_keyword not in eff_type:
                continue

            # Get effectiveness MW for this type
            if type_keyword == "hybrid":
                # Hybrid = sum of all effectiveness MW
                eff_val = sum(v for v in eff_mw.values() if v > 0)
            else:
                eff_key = _TYPE_TO_EFF_KEY[type_keyword]
                eff_val = eff_mw.get(eff_key, 0.0)

            # Get CMETS Type parsed MW for matching keyword
            # Map type keywords to what appears in CMETS Type text
            _CMETS_TYPE_KEYS = {
                "solar": ["solar"],
                "wind":  ["wind"],
                "hydro": ["hydro", "psp", "pump storage"],
                "hybrid": ["solar", "wind", "hydro"],
            }
            cmets_val = 0.0
            for tk in _CMETS_TYPE_KEYS.get(type_keyword, []):
                cmets_val += cmets_type_mw.get(tk, 0.0)

            total = eff_val + cmets_val
            if total > 0:
                cmets_df.at[idx, capacity_col] = total
                row_has_capacity = True

        # Also handle ESS/BESS — ess in effectiveness maps to Battery
        # ESS is NOT an Installed/Break-up Capacity column but we check
        # if BESS is in Type and ess_mw > 0, we still need to note it.
        # (BESS goes to Battery columns which are already extracted)

        if row_has_capacity:
            capacity_computed += 1

    # ── Write output Excel (same columns as CMETS, no add/remove) ─────
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    cmets_df.to_excel(str(output_excel), index=False,
                      sheet_name="CMETS+Effectiveness")
    print(
        f"\n[Step 1] Merge: "
        f"GNA={matched_gna} | "
        f"LTA={matched_lta} | "
        f"5.2={matched_52} | "
        f"Unmatched={unmatched} | "
        f"Capacity computed={capacity_computed} | "
        f"Total={len(cmets_df)}"
    )
    print(f"[Step 1] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    stats = {
        "matched_gna": matched_gna,
        "matched_lta": matched_lta,
        "matched_52":  matched_52,
        "unmatched":   unmatched,
        "total_rows":  len(cmets_df),
    }
    return cmets_df, stats


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
        - 02_effectiveness_extracted.xlsx
        - 04_jcc_extracted.xlsx (or JCC JSON cache)
        - 05_bayallocation_extracted.xlsx (or bay allocation JSON cache)

    Pipeline:
        Step 1: CMETS + Effectiveness  → 03_cmets_effectiveness_mapped.xlsx
                (reads 02_effectiveness_extracted.xlsx directly, updates
                 columns in-place, computes Installed/Break-up Capacity)
        Step 2: Step1  + JCC           → 06_cmets_jcc_mapped.xlsx
        Step 3: Step2  + Bay Allocation → 07_final_mapped.xlsx

    Returns the path to the final output Excel.
    """
    root = start_dir or _START_DIR
    excel_root = root / "excels"
    output_root = root / "output"

    # Input paths
    cmets_excel = excel_root / "01_cmets_extracted.xlsx"
    effectiveness_excel = excel_root / "02_effectiveness_extracted.xlsx"
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
    print(f"  Effectiveness Excel   : {effectiveness_excel}")
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
    cmets_df = pd.read_excel(cmets_excel, sheet_name=0, engine="openpyxl")
    print(f"\n[Pipeline] Base CMETS rows loaded: {len(cmets_df)}")

    if cmets_df.empty:
        print("[Pipeline] WARNING: CMETS DataFrame is empty — nothing to map.")
        return step3_excel

    # ── Step 1: CMETS + Effectiveness ─────────────────────────────────────
    step1_df, _ = _step1_effectiveness_mapping(
        cmets_df=cmets_df.copy(),
        effectiveness_excel=effectiveness_excel,
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

    # ── Step 4: Generate data_to_be_captured.xlsx ─────────────────────────
    from pipeline.final_mapping_handler.data_capture import generate_data_to_be_captured
    data_capture_excel = excel_root / "data_to_be_captured.xlsx"
    try:
        generate_data_to_be_captured(
            final_mapped_excel=step3_excel,
            output_excel=data_capture_excel,
        )
    except Exception as exc:
        print(f"\n  ⚠ data_to_be_captured generation failed: {exc}")
        import traceback
        traceback.print_exc()

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
    print(f"  Step 4 → {data_capture_excel} (FILTERED)")
    print("█" * 64)

    return step3_excel
