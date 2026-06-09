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

    Step 3: Step2 + Bay Allocation → 07_cmets_effective_jcc_bayallocation.xlsx
            - Reads 05_bayallocation_extracted.xlsx directly
            - Matches via developer name + substation name + voltage level
            - Populates: Coordinates, Bay No

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


def _make_columns_assignable(df: pd.DataFrame, columns: list[str]) -> None:
    """Allow mixed Excel values to be assigned into pandas string columns."""
    for col in columns:
        if col in df.columns:
            df[col] = df[col].astype("object")


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


_TYPE_TO_CAPACITY_COL = {
    "solar": "Installed/Break-up Capacity (MW) Solar",
    "wind": "Installed/Break-up Capacity (MW) Wind",
    "hybrid": "Installed/Break-up Capacity (MW) Hybrid",
    "hydro": "Installed/Break-up Capacity (MW) Hydro",
}

_TYPE_TO_EFF_KEY = {
    "solar": "solar",
    "wind": "wind",
    "hydro": "hydro",
    "hybrid": None,
}

_CMETS_TYPE_KEYS = {
    "solar": ["solar"],
    "wind": ["wind"],
    "hydro": ["hydro"],
    "hybrid": ["hybrid"],
}


def _set_installed_breakdown_from_type(
    df: pd.DataFrame,
    idx: int,
    row: pd.Series,
    eff_rec: dict | None = None,
) -> bool:
    """Set install breakdown columns from effectiveness first, then Type MW."""
    cmets_type_mw = _parse_type_mw(safe_str(row.get("Type")))
    eff_type = safe_str(eff_rec.get("type_of_project")).lower() if eff_rec else ""
    eff_mw = {
        "solar": _safe_float(eff_rec.get("solar_mw")) if eff_rec else 0.0,
        "wind": _safe_float(eff_rec.get("wind_mw")) if eff_rec else 0.0,
        "hydro": _safe_float(eff_rec.get("hydro_mw")) if eff_rec else 0.0,
        "ess": _safe_float(eff_rec.get("ess_mw")) if eff_rec else 0.0,
    }

    row_has_capacity = False
    for type_keyword, capacity_col in _TYPE_TO_CAPACITY_COL.items():
        if capacity_col not in df.columns:
            continue

        existing_val = _safe_float(df.at[idx, capacity_col])
        if existing_val > 0:
            row_has_capacity = True
            continue

        if type_keyword == "hybrid":
            eff_val = sum(v for v in eff_mw.values() if v > 0) if eff_rec else 0.0
        else:
            eff_key = _TYPE_TO_EFF_KEY[type_keyword]
            eff_val = eff_mw.get(eff_key, 0.0) if eff_key else 0.0

        cmets_val = 0.0
        for tk in _CMETS_TYPE_KEYS.get(type_keyword, []):
            cmets_val += cmets_type_mw.get(tk, 0.0)

        has_eff_type = type_keyword in eff_type
        if type_keyword == "hybrid":
            has_eff_type = "hybrid" in eff_type
        if not has_eff_type and cmets_val <= 0:
            continue

        value = eff_val if eff_val > 0 else cmets_val
        if value > 0:
            df.at[idx, capacity_col] = value
            row_has_capacity = True

    return row_has_capacity


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

    # Build effectiveness list (each row may have multiple IDs in application_id cell)
    eff_records: list[dict] = []
    for _, eff_row in eff_df.iterrows():
        app_id_cell = safe_str(eff_row.get("application_id")).strip()
        if app_id_cell and app_id_cell.lower() not in ("", "none", "nan", "null"):
            eff_records.append(eff_row.to_dict())

    print(f"[Step 1] Effectiveness records loaded: {len(eff_records)}")

    if not eff_records:
        print("[Step 1] WARNING: No effectiveness records with application_id.")
        cmets_df.to_excel(str(output_excel), index=False,
                          sheet_name="CMETS+Effectiveness")
        return cmets_df, {"matched_gna": 0, "matched_lta": 0,
                          "matched_52": 0, "unmatched": len(cmets_df),
                          "total_rows": len(cmets_df)}

    # ── Match and update each CMETS row ───────────────────────────────────
    matched_gna = matched_lta = matched_52 = unmatched = 0
    capacity_computed = 0
    _make_columns_assignable(cmets_df, [
        "Name of Developers",
        "Substation",
        "State",
        "GNA Operationalization Date",
        "Application Quantum (MW)(ST II)",
        "Installed/Break-up Capacity (MW) Solar",
        "Installed/Break-up Capacity (MW) Wind",
        "Installed/Break-up Capacity (MW) Hybrid",
        "Installed/Break-up Capacity (MW) Hydro",
    ])

    for idx, row in cmets_df.iterrows():
        # Extract all IDs from the 3 CMETS ID columns
        gna_ids = _extract_ids(row.get("GNA/ST II Application ID"))
        lta_ids = _extract_ids(row.get("LTA Application ID"))
        enh_ids = _extract_ids(row.get(
            "Application ID under Enhancement 5.2 or revision"))

        # Search effectiveness records: GNA → LTA → 5.2 cascade
        # Use substring "in" matching since effectiveness application_id can contain multiple IDs
        eff_rec = None
        match_via = None

        for aid in gna_ids:
            for eff_row in eff_records:
                eff_app_id_cell = safe_str(eff_row.get("application_id"))
                if aid in eff_app_id_cell:
                    eff_rec = eff_row
                    match_via = "GNA"
                    break
            if eff_rec:
                break

        if eff_rec is None:
            for aid in lta_ids:
                for eff_row in eff_records:
                    eff_app_id_cell = safe_str(eff_row.get("application_id"))
                    if aid in eff_app_id_cell:
                        eff_rec = eff_row
                        match_via = "LTA"
                        break
                if eff_rec:
                    break

        if eff_rec is None:
            for aid in enh_ids:
                for eff_row in eff_records:
                    eff_app_id_cell = safe_str(eff_row.get("application_id"))
                    if aid in eff_app_id_cell:
                        eff_rec = eff_row
                        match_via = "5.2"
                        break
                if eff_rec:
                    break

        if eff_rec is None:
            if _set_installed_breakdown_from_type(cmets_df, idx, row):
                capacity_computed += 1
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
        # Effectiveness values win when present; otherwise use CMETS Type MW.
        if _set_installed_breakdown_from_type(cmets_df, idx, row, eff_rec):
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
# STEP 2 — + JCC Mapping (Commissioned TGNA / GNA)
# ─────────────────────────────────────────────────────────────────────────────

def _step2_jcc_mapping(
    df: pd.DataFrame,
    jcc_excel: Path,
    output_excel: Path,
) -> pd.DataFrame:
    """Map CMETS (enriched with effectiveness) to JCC data (Excel-to-Excel).

    Logic
    -----
    1. Read 04_jcc_extracted.xlsx → build gna_lta_id lookup.
    2. For each CMETS row, search GNA/LTA/5.2 IDs in the JCC gna_lta_id column.
    3. When matched, pick TGNA and GNA values from the JCC row.
    4. Update Commissioned TGNA and Commissioned GNA columns in CMETS.

    Output has the EXACT same columns as input — nothing added or removed.
    Same column order preserved.
    """
    print("\n" + "=" * 64)
    print("  STEP 2 — CMETS × JCC MAPPING (Commissioned TGNA / GNA)")
    print("=" * 64)
    print(f"  Input rows      : {len(df)}")
    print(f"  JCC Excel       : {jcc_excel}")
    print(f"  Output Excel    : {output_excel}")
    print("=" * 64)

    # ── Load JCC data from Excel ──────────────────────────────────────────
    if not jcc_excel.exists():
        logger.warning("[Step 2] JCC Excel not found — Commissioned columns stay empty.")
        print("[Step 2] WARNING: JCC Excel not found.")
        df.to_excel(str(output_excel), index=False,
                    sheet_name="CMETS+Effectiveness+JCC")
        return df

    jcc_df = pd.read_excel(jcc_excel, sheet_name=0, engine="openpyxl")
    print(f"[Step 2] JCC rows loaded: {len(jcc_df)}")

    # Build gna_lta_id → row dict lookup
    # A single gna_lta_id cell can contain multiple IDs (comma/semicolon separated)
    jcc_lookup: dict[str, dict] = {}
    for _, jcc_row in jcc_df.iterrows():
        gna_lta_raw = safe_str(jcc_row.get("gna_lta_id"))
        if not gna_lta_raw:
            continue
        row_dict = jcc_row.to_dict()
        # Split gna_lta_id into individual IDs and map each to this row
        for aid in re.findall(r"\b\d{6,}\b", gna_lta_raw):
            if aid not in jcc_lookup:
                jcc_lookup[aid] = row_dict

    print(f"[Step 2] JCC lookup: {len(jcc_lookup)} unique application IDs")

    if not jcc_lookup:
        print("[Step 2] WARNING: No JCC records with gna_lta_id.")
        df.to_excel(str(output_excel), index=False,
                    sheet_name="CMETS+Effectiveness+JCC")
        return df

    # ── Match and update each CMETS row ───────────────────────────────────
    matched_count = 0
    tgna_count = 0
    gna_count = 0
    _make_columns_assignable(df, ["Commissioned TGNA", "Commissioned GNA"])

    for idx, row in df.iterrows():
        # Extract all IDs from the 3 CMETS ID columns
        gna_ids = _extract_ids(row.get("GNA/ST II Application ID"))
        lta_ids = _extract_ids(row.get("LTA Application ID"))
        enh_ids = _extract_ids(row.get(
            "Application ID under Enhancement 5.2 or revision"))

        # Search JCC lookup: GNA → LTA → 5.2 cascade
        jcc_rec = None

        for aid in gna_ids:
            if aid in jcc_lookup:
                jcc_rec = jcc_lookup[aid]
                break

        if jcc_rec is None:
            for aid in lta_ids:
                if aid in jcc_lookup:
                    jcc_rec = jcc_lookup[aid]
                    break

        if jcc_rec is None:
            for aid in enh_ids:
                if aid in jcc_lookup:
                    jcc_rec = jcc_lookup[aid]
                    break

        if jcc_rec is None:
            continue

        matched_count += 1

        # ── Pick TGNA and GNA from JCC row ────────────────────────────
        tgna_val = jcc_rec.get("TGNA")
        gna_val = jcc_rec.get("GNA")

        if _is_valid(tgna_val):
            df.at[idx, "Commissioned TGNA"] = tgna_val
            tgna_count += 1
        if _is_valid(gna_val):
            df.at[idx, "Commissioned GNA"] = gna_val
            gna_count += 1

    # ── Write output Excel (same columns, same order) ─────────────────
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_excel), index=False,
                sheet_name="CMETS+Effectiveness+JCC")
    print(
        f"\n[Step 2] Results: "
        f"Matched={matched_count} | "
        f"TGNA populated={tgna_count} | "
        f"GNA populated={gna_count} | "
        f"Unmatched={len(df) - matched_count} | "
        f"Total={len(df)}"
    )
    print(f"[Step 2] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — + Bay Allocation Mapping (Excel-to-Excel)
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_voltage(v: str) -> str:
    """Normalise voltage strings for cross-sheet comparison.

    '220 kV' → '220kv', '400kV' → '400kv', '220kv' → '220kv'
    """
    return re.sub(r"\s+", "", safe_str(v)).lower().strip()


def _tokenise(text: str) -> set[str]:
    """Break a name into lowercase alphanumeric tokens for fuzzy matching."""
    return set(re.findall(r"[a-z0-9]+", safe_str(text).lower()))


def _token_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Jaccard-like token overlap ratio (0.0 – 1.0)."""
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _step3_bay_mapping(
    df: pd.DataFrame,
    bay_excel: Path,
    output_excel: Path,
) -> pd.DataFrame:
    """Map CMETS (enriched with effectiveness + JCC) to Bay Allocation data.

    Reads 05_bayallocation_extracted.xlsx directly and matches each CMETS row
    by (Substation + Name of Developers + Voltage level) against
    (Name of Substation + Name of Entity + Voltage Level) in bay allocation.

    On match: populates 'Coordinates' and 'Bay No' columns.

    Output has the EXACT same columns as input plus 'Coordinates' and 'Bay No'
    if they were not already present. No columns are removed; order is preserved.
    """
    print("\n" + "=" * 64)
    print("  STEP 3 — CMETS × BAY ALLOCATION MAPPING")
    print("=" * 64)
    print(f"  Input rows                   : {len(df)}")
    print(f"  Bay Allocation Excel         : {bay_excel}")
    print(f"  Output Excel                 : {output_excel}")
    print("=" * 64)

    # ── Ensure target columns exist ──────────────────────────────────────
    if "Coordinates" not in df.columns:
        df["Coordinates"] = ""
    if "Bay No" not in df.columns:
        df["Bay No"] = ""
    _make_columns_assignable(df, ["Coordinates", "Bay No"])

    # ── Load Bay Allocation data ─────────────────────────────────────────
    if not bay_excel.exists():
        logger.warning("[Step 3] Bay Allocation Excel not found — Coordinates/Bay No stay empty.")
        print("[Step 3] WARNING: Bay Allocation Excel not found.")
        df.to_excel(str(output_excel), index=False,
                    sheet_name="CMETS+Eff+JCC+Bay")
        return df

    bay_df = pd.read_excel(bay_excel, sheet_name=0, engine="openpyxl")
    print(f"[Step 3] Bay Allocation rows loaded: {len(bay_df)}")

    # ── Pre-compute bay allocation index by normalised voltage ───────────
    # Structure: {"220kv": [(substation_tokens, entity_tokens, bay_row_dict), ...]}
    bay_index: dict[str, list[tuple[set[str], set[str], dict]]] = {}
    for _, brow in bay_df.iterrows():
        voltage = _normalise_voltage(brow.get("Voltage Level", ""))
        if not voltage:
            continue
        sub_tokens = _tokenise(brow.get("Name of Substation", ""))
        entity_tokens = _tokenise(brow.get("Name of Entity", ""))
        entry = (sub_tokens, entity_tokens, brow.to_dict())
        bay_index.setdefault(voltage, []).append(entry)

    total_entries = sum(len(v) for v in bay_index.values())
    print(f"[Step 3] Bay index built: {total_entries} entries "
          f"(220kV={len(bay_index.get('220kv', []))}, "
          f"400kV={len(bay_index.get('400kv', []))})")

    if total_entries == 0:
        print("[Step 3] WARNING: No valid bay allocation entries found.")
        df.to_excel(str(output_excel), index=False,
                    sheet_name="CMETS+Eff+JCC+Bay")
        return df

    # ── Match each CMETS row ─────────────────────────────────────────────
    matched_count = 0
    coords_count = 0
    bay_count = 0
    no_voltage_count = 0
    no_developer_count = 0
    unmatched_count = 0

    for idx, row in df.iterrows():
        # Get CMETS substation, developer, and voltage
        cmets_substation = safe_str(row.get("Substation", ""))
        cmets_developer = safe_str(row.get("Name of Developers", ""))
        cmets_voltage = _normalise_voltage(row.get("Voltage level", ""))

        if not cmets_voltage:
            no_voltage_count += 1
            continue

        if not cmets_developer:
            no_developer_count += 1
            continue

        # Get bay entries for this voltage level
        candidates = bay_index.get(cmets_voltage, [])
        if not candidates:
            unmatched_count += 1
            continue

        # Tokenise CMETS values
        cmets_sub_tokens = _tokenise(cmets_substation)
        cmets_dev_tokens = _tokenise(cmets_developer)

        # Find best match — score = substation_similarity + entity_similarity
        best_score = 0.0
        best_entry: dict | None = None

        for bay_sub_tokens, bay_entity_tokens, bay_row in candidates:
            # Entity/developer match is the primary signal
            entity_score = _token_similarity(cmets_dev_tokens, bay_entity_tokens)
            # Substation match is secondary but helps disambiguate
            sub_score = _token_similarity(cmets_sub_tokens, bay_sub_tokens)

            # Weighted: entity match is 70%, substation match is 30%
            combined = 0.7 * entity_score + 0.3 * sub_score

            if combined > best_score:
                best_score = combined
                best_entry = bay_row

        # Require minimum threshold to avoid false matches
        if best_score < 0.15 or best_entry is None:
            unmatched_count += 1
            continue

        matched_count += 1

        # ── Populate Coordinates from bay allocation ──────────────────
        bay_coords = safe_str(best_entry.get("Substation Coordinates", ""))
        if bay_coords:
            df.at[idx, "Coordinates"] = bay_coords
            coords_count += 1

        # ── Populate Bay No from bay allocation ───────────────────────
        bay_no = safe_str(best_entry.get("Bay No", ""))
        if bay_no:
            df.at[idx, "Bay No"] = bay_no
            bay_count += 1

    # ── Write output Excel (same columns, same order) ─────────────────
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_excel), index=False,
                sheet_name="CMETS+Eff+JCC+Bay")
    print(
        f"\n[Step 3] Results: "
        f"Matched={matched_count} | "
        f"Coords populated={coords_count} | "
        f"Bay No populated={bay_count} | "
        f"No voltage={no_voltage_count} | "
        f"No developer={no_developer_count} | "
        f"Unmatched={unmatched_count} | "
        f"Total={len(df)}"
    )
    print(f"[Step 3] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Format & produce dtbc.xlsx
# ─────────────────────────────────────────────────────────────────────────────

def _step4_format_dtbc(
    df: pd.DataFrame,
    output_excel: Path,
) -> pd.DataFrame:
    """Apply column-value formatting to produce dtbc.xlsx.

    Same columns, same order as the input — NO column added or removed.

    Formatting rules applied IN-PLACE on the DataFrame:
      1. Bay No       → merge JCC bay (preferred) + Bay Allocation bay
      2. Granted Quantum GNA/LTA(MW)
                       → populated only when Status = "Granted"
      3. Type          → recalculated from capacity evidence columns
      4. Excel styling → headers, borders, alternating row fills
    """
    print("\n" + "=" * 64)
    print("  STEP 4 — FORMAT dtbc.xlsx")
    print("=" * 64)
    print(f"  Input rows      : {len(df)}")
    print(f"  Input columns   : {len(df.columns)}")
    print(f"  Output Excel    : {output_excel}")
    print("=" * 64)

    original_columns = list(df.columns)

    # ── 1. Bay No: prefer JCC, fallback to Bay Allocation ────────────────
    bay_merged = 0
    if "Bay No (JCC)" in df.columns and "Bay No" in df.columns:
        for idx, row in df.iterrows():
            jcc_val = safe_str(row.get("Bay No (JCC)"))
            bay_val = safe_str(row.get("Bay No"))
            # JCC bay preferred
            if jcc_val and jcc_val.lower() not in (
                "none", "nan", "null", "n/a", "-", ""
            ):
                df.at[idx, "Bay No"] = jcc_val
                bay_merged += 1
            # else keep existing Bay No from bay allocation (already set)
    print(f"[Step 4] Bay No: {bay_merged} rows updated from JCC bay")

    # ── 2. Granted Quantum GNA/LTA(MW): only when Status = "Granted" ────
    granted_col = None
    for c in df.columns:
        if "granted" in c.lower() and "quantum" in c.lower():
            granted_col = c
            break

    status_col = None
    for c in df.columns:
        if "status of application" in c.lower():
            status_col = c
            break

    quantum_col = None
    for c in df.columns:
        if "application quantum" in c.lower():
            quantum_col = c
            break

    granted_count = 0
    if granted_col and status_col and quantum_col:
        for idx, row in df.iterrows():
            status = safe_str(row.get(status_col)).strip().lower()
            if status == "granted":
                df.at[idx, granted_col] = row.get(quantum_col)
                granted_count += 1
            else:
                # Clear if status is not "granted"
                df.at[idx, granted_col] = None
    print(f"[Step 4] Granted Quantum: {granted_count} rows populated")

    # ── 3. Type: strip MW numbers, keep only keywords ─────────────────
    #    e.g. "Solar (52) + BESS (6.88)" → "Solar + BESS"
    #    The MW values were used in Step 1 for capacity breakdown;
    #    in this formatting layer we keep only the component keywords.
    type_col = None
    for c in df.columns:
        if c.strip() == "Type":
            type_col = c
            break

    # Pattern to remove parenthesised numbers: "(52)", "(6.88)", "( 300 )"
    _MW_PARENS_RE = re.compile(r"\s*\(\s*[\d,.]+\s*\)")
    # Clean up double-spaces and leading/trailing whitespace after removal
    _MULTI_SPACE_RE = re.compile(r"\s{2,}")

    type_stripped = 0
    if type_col:
        for idx, row in df.iterrows():
            raw_type = safe_str(row.get(type_col))
            if not raw_type:
                continue
            stripped = _MW_PARENS_RE.sub("", raw_type)
            stripped = _MULTI_SPACE_RE.sub(" ", stripped).strip()
            # Clean up leftover separators: " + " at start/end, "++", etc.
            stripped = re.sub(r"(?:^[+\s]+|[+\s]+$)", "", stripped)
            stripped = re.sub(r"\s*\+\s*\+\s*", " + ", stripped)
            if stripped and stripped != raw_type:
                df.at[idx, type_col] = stripped
                type_stripped += 1
    print(f"[Step 4] Type: {type_stripped} rows — MW numbers stripped to keywords only")

    # ── Ensure column order is unchanged ─────────────────────────────────
    df = df[original_columns]

    # ── Write output Excel ───────────────────────────────────────────────
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_excel), index=False, sheet_name="DTBC")

    # Apply professional styling
    try:
        format_mapped_excel(str(output_excel))
    except Exception:
        pass  # Formatting is optional

    print(
        f"\n[Step 4] Results: "
        f"Bay No merged={bay_merged} | "
        f"Granted Quantum={granted_count} | "
        f"Type stripped={type_stripped} | "
        f"Total rows={len(df)} | "
        f"Total columns={len(df.columns)}"
    )
    print(f"[Step 4] ✓ Excel saved → {output_excel}")
    print("=" * 64)

    return df




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
        - 04_jcc_extracted.xlsx
        - 05_bayallocation_extracted.xlsx

    Pipeline:
        Step 1: CMETS + Effectiveness  → 03_cmets_effectiveness_mapped.xlsx
                (reads 02_effectiveness_extracted.xlsx, updates columns
                 in-place, computes Installed/Break-up Capacity)
        Step 2: Step1 + JCC            → 06_cmets_jcc_mapped.xlsx
                (reads 04_jcc_extracted.xlsx, updates Commissioned
                 TGNA/GNA via gna_lta_id matching)
        Step 3: Step2 + Bay Allocation → 07_cmets_effective_jcc_bayallocation.xlsx
                (reads 05_bayallocation_extracted.xlsx, populates
                 Coordinates and Bay No via fuzzy name matching)
        Step 4: Formatting             → dtbc.xlsx
                (applies column-value formatting: Bay No merge,
                 Granted Quantum, Type recalculation)

    Returns the path to the final output Excel (dtbc.xlsx).
    """
    root = start_dir or _START_DIR
    excel_root = root / "excels"

    # Input paths
    cmets_excel = excel_root / "01_cmets_extracted.xlsx"
    effectiveness_excel = excel_root / "02_effectiveness_extracted.xlsx"
    jcc_excel = excel_root / "04_jcc_extracted.xlsx"
    bay_excel = excel_root / "05_bayallocation_extracted.xlsx"

    # Output paths
    step1_excel = excel_root / "03_cmets_effectiveness_mapped.xlsx"
    step2_excel = excel_root / "06_cmets_jcc_mapped.xlsx"
    step3_excel = excel_root / "07_cmets_effective_jcc_bayallocation.xlsx"
    step4_excel = excel_root / "dtbc.xlsx"

    print("\n" + "█" * 64)
    print("  SEQUENTIAL MAPPING PIPELINE")
    print("  ─────────────────────────────────────────────────────────")
    print(f"  Base CMETS Excel      : {cmets_excel}")
    print(f"  Effectiveness Excel   : {effectiveness_excel}")
    print(f"  JCC Excel             : {jcc_excel}")
    print(f"  Bay Allocation Excel  : {bay_excel}")
    print(f"  Step 1 output         : {step1_excel}")
    print(f"  Step 2 output         : {step2_excel}")
    print(f"  Step 3 output         : {step3_excel}")
    print(f"  Step 4 output (FINAL) : {step4_excel}")
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
        return step4_excel

    # ── Step 1: CMETS + Effectiveness ─────────────────────────────────────
    step1_df, _ = _step1_effectiveness_mapping(
        cmets_df=cmets_df.copy(),
        effectiveness_excel=effectiveness_excel,
        output_excel=step1_excel,
    )

    # ── Step 2: Step1 + JCC ───────────────────────────────────────────────
    step2_df = _step2_jcc_mapping(
        df=step1_df.copy(),
        jcc_excel=jcc_excel,
        output_excel=step2_excel,
    )

    # ── Step 3: Step2 + Bay Allocation ────────────────────────────────────
    step3_df = _step3_bay_mapping(
        df=step2_df.copy(),
        bay_excel=bay_excel,
        output_excel=step3_excel,
    )

    # ── Step 4: Format → dtbc.xlsx ────────────────────────────────────────
    dtbc_df = _step4_format_dtbc(
        df=step3_df.copy(),
        output_excel=step4_excel,
    )

    # ── Final summary ─────────────────────────────────────────────────────
    print("\n" + "█" * 64)
    print("  MAPPING PIPELINE COMPLETE")
    print("  ─────────────────────────────────────────────────────────")
    print(f"  Total rows        : {len(dtbc_df)}")
    print(f"  Total columns     : {len(dtbc_df.columns)}")
    print(f"  Columns:")
    for col in dtbc_df.columns:
        non_null = dtbc_df[col].notna().sum()
        print(f"    {col:<55} {non_null}/{len(dtbc_df)} filled")
    print(f"\n  Step 1 → {step1_excel}")
    print(f"  Step 2 → {step2_excel}")
    print(f"  Step 3 → {step3_excel}")
    print(f"  Step 4 → {step4_excel} (FINAL)")
    print("█" * 64)

    return step4_excel
