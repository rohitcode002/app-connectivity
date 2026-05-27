"""
final_mapping_handler/data_capture.py — Generate data_to_be_captured.xlsx
==========================================================================
Takes the 07_final_mapped.xlsx (output of the full mapping pipeline) and
produces a filtered Excel with ONLY the columns the user needs, in the
exact order specified.

Column Resolution Rules
-----------------------
1. Always prefer CMETS-sourced columns.
2. For columns that exist in BOTH CMETS and Effectiveness:
     - Start with the CMETS value.
     - If the row's GNA/LTA/5.2 ID matches an effectiveness record,
       OVERWRITE with the effectiveness value (better extraction quality).
3. Bay No: If JCC bay number is present → use it.  Else → use Bay Allocation.
4. Commissioned [TGNA, GNA] maps to the TGNA and GNA columns from JCC mapping.
5. Coordinates → Substation Coordinates (Bay Allocation).

Column Mapping (user label → source column in final_mapped)
-----------------------------------------------------------
See FINAL_COLUMN_MAP below for the full mapping.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from pipeline.shared_utils import (
    components_from_type_keywords,
    components_to_type,
    find_col,
    safe_float,
    safe_str,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# FINAL COLUMN ORDER — exactly as the user specified
# ═══════════════════════════════════════════════════════════════════════════════
#
# Each tuple: (final_column_name, [candidate_source_columns...])
# The first matching candidate found in the final_mapped DataFrame is used.

FINAL_COLUMN_MAP: list[tuple[str, list[str]]] = [
    ("Region",
     ["Region"]),

    ("State",
     ["State"]),

    ("Substation",
     ["Substation"]),

    ("Coordinates",
     ["Substation Coordinates (Bay Allocation)",
      "Coordinates"]),

    ("Name of Developers",
     ["Name of Developers", "Name of the developers"]),

    ("GNA/ST II Application ID",
     ["GNA/ST II Application ID"]),

    ("LTA Application ID",
     ["LTA Application ID"]),

    ("Application ID under Enhancement 5.2 or revision",
     ["Application ID under Enhancement 5.2 or revision"]),

    ("CMETS GNA Approved",
     ["CMETS GNA Approved"]),

    ("CMETS LTA Approved",
     ["CMETS LTA Approved"]),

    ("CMETS GNA Meeting Date",
     ["CMETS GNA Meeting Date"]),

    ("CMETS LTA Meeting Date",
     ["CMETS LTA Meeting Date"]),

    ("Type",
     ["Type"]),

    ("Application Quantum (MW)(ST II)",
     ["Application Quantum (MW)(ST II)"]),

    ("Granted  Quantum GNA/LTA(MW)",
     []),

    # Installed/Break-up Capacity (MW) sub-columns
    ("Installed/Break-up Capacity (MW) Solar",
     ["Installed/Break-up Capacity (MW) Solar",
      "Installed capacity (MW) solar"]),

    ("Installed/Break-up Capacity (MW) Wind",
     ["Installed/Break-up Capacity (MW) Wind",
      "Installed capacity (MW) wind"]),

    ("Installed/Break-up Capacity (MW) Hybrid",
     ["Installed/Break-up Capacity (MW) Hybrid",
      "Installed capacity (MW) hybrid"]),

    ("Installed/Break-up Capacity (MW) Hydro",
     ["Installed/Break-up Capacity (MW) Hydro",
      "Installed capacity (MW) hydro"]),

    # Battery columns
    ("Battery MWh",
     ["Battery MWh"]),

    ("Battery Injection (MW)",
     ["Battery Injection (MW)"]),

    ("Battery Drawl (MW)",
     ["Battery Drawl (MW)"]),

    # PSP columns
    ("PSP MWh",
     ["PSP MWh"]),

    ("PSP Injection (MW)",
     ["PSP Injection (MW)"]),

    ("PSP Drawl (MW)",
     ["PSP Drawl (MW)"]),

    # Commissioned — maps to TGNA and GNA from JCC
    ("Commissioned TGNA",
     ["Commissioned TGNA", "TGNA"]),

    ("Commissioned GNA",
     ["Commissioned GNA", "GNA"]),

    ("Application/Submission Date",
     ["Application/Submission Date"]),

    ("Mode(Criteria for applying)",
     ["Mode(Criteria for applying)"]),

    ("Applied Start of Connectivity sought by developer date"
     "( start date of connectivity as per the application)",
     ["Applied Start of Connectivity sought by developer date"
      "( start date of connectivity as per the application)",
      "Applied Start of Connectivity sought by developer date"]),

    ("GNA Operationalization Date",
     ["GNA Operationalization Date"]),

    ("GNA Operationalization (Yes/No)",
     ["GNA Operationalization (Yes/No)"]),

    ("Date from which additional capacity is to be added",
     ["Date from which additional capacity is to be added"]),

    ("Nature of Applicant",
     ["Nature of Applicant"]),

    ("Status of application(Withdrawn / granted. Revoked.)",
     ["Status of application(Withdrawn / granted. Revoked.)"]),

    ("Voltage level",
     ["Voltage level"]),

    # Bay No: JCC preferred, then Bay Allocation
    ("Bay No",
     ["Bay No"]),   # resolved separately in _resolve_bay_no()
]


# ═══════════════════════════════════════════════════════════════════════════════
# Effectiveness-updatable columns
# ═══════════════════════════════════════════════════════════════════════════════
# These columns already exist from CMETS but should be updated from
# Effectiveness when an ID match is found (effectiveness has better extraction).
# The update already happened during Step 1 (mapping_handler/merge.py),
# so we simply read the latest values from the final_mapped DataFrame.
# No additional cross-referencing is needed here — the enrichment pipeline
# in runner.py already did the ID-based overwrite.
#
# This list is kept for documentation/auditing purposes.
EFF_UPDATED_COLUMNS = [
    "Name of Developers",
    "Substation",
    "State",
    "Application Quantum (MW)(ST II)",
    "Region",
    "GNA Operationalization Date",
    "GNA Operationalization (Yes/No)",
    "Date from which additional capacity is to be added",
    "Installed/Break-up Capacity (MW) Solar",
    "Installed/Break-up Capacity (MW) Wind",
    "Installed/Break-up Capacity (MW) Hybrid",
    "Installed/Break-up Capacity (MW) Hydro",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _find_source_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find the first matching column name (case-insensitive) from candidates."""
    for candidate in candidates:
        col = find_col(df, candidate)
        if col is not None:
            return col
    return None


def _resolve_bay_no(df: pd.DataFrame) -> pd.Series:
    """Resolve Bay No: prefer JCC bay number, fallback to Bay Allocation.

    Priority:
        1. Bay No (JCC) — extracted from JCC under ISTS scope
        2. Bay No (Bay Allocation) — matched from Bay Allocation PDF
    """
    jcc_col = find_col(df, "Bay No (JCC)")
    bay_col = find_col(df, "Bay No (Bay Allocation)") or find_col(df, "Bay No")

    result = pd.Series([None] * len(df), index=df.index)

    for idx, row in df.iterrows():
        jcc_val = safe_str(row.get(jcc_col)) if jcc_col else ""
        bay_val = safe_str(row.get(bay_col)) if bay_col else ""

        if jcc_val and jcc_val.lower() not in ("none", "nan", "null", "n/a", "-"):
            result.at[idx] = jcc_val
        elif bay_val and bay_val.lower() not in ("none", "nan", "null", "n/a", "-"):
            result.at[idx] = bay_val

    return result


def _resolve_granted_quantum(df: pd.DataFrame) -> pd.Series:
    """Calculate granted quantum from status and application quantum.

    The final Data to be Captured sheet must not copy this value from CMETS or
    any upstream extracted column.  It is filled only when the application status
    is exactly granted after trimming/case-folding.
    """
    status_col = find_col(df, "Status of application(Withdrawn / granted. Revoked.)")
    quantum_col = find_col(df, "Application Quantum (MW)(ST II)")

    result = pd.Series([None] * len(df), index=df.index)
    if status_col is None or quantum_col is None:
        return result

    for idx, row in df.iterrows():
        status = safe_str(row.get(status_col)).lower()
        if status == "granted":
            result.at[idx] = row.get(quantum_col)

    return result


def _resolve_type(df: pd.DataFrame) -> pd.Series:
    """Recalculate final Type from CMETS + RE-effectiveness capacity evidence."""
    type_col = find_col(df, "Type")
    nature_col = find_col(df, "Nature of Applicant")

    capacity_cols = {
        "Solar": [
            "Installed/Break-up Capacity (MW) Solar",
            "Installed capacity (MW) solar",
        ],
        "Wind": [
            "Installed/Break-up Capacity (MW) Wind",
            "Installed capacity (MW) wind",
        ],
        "Hydro": [
            "Installed/Break-up Capacity (MW) Hydro",
            "Installed capacity (MW) hydro",
        ],
        "BESS": [
            "Battery MWh",
            "Battery Injection (MW)",
            "Battery Drawl (MW)",
            "Installed capacity (MW) ess",
        ],
        "PSP": [
            "PSP MWh",
            "PSP Injection (MW)",
            "PSP Drawl (MW)",
        ],
    }

    id_cols = [
        find_col(df, "GNA/ST II Application ID"),
        find_col(df, "LTA Application ID"),
        find_col(df, "Application ID under Enhancement 5.2 or revision"),
    ]

    result = pd.Series([None] * len(df), index=df.index)
    for idx, row in df.iterrows():
        components = components_from_type_keywords(row.get(type_col)) if type_col else set()
        capacity_components: set[str] = set()

        for component, candidates in capacity_cols.items():
            for candidate in candidates:
                col = find_col(df, candidate)
                if col and safe_float(row.get(col)) > 0:
                    components.add(component)
                    capacity_components.add(component)
                    break

        nature = safe_str(row.get(nature_col)).lower() if nature_col else ""
        has_solar_wind = {"Solar", "Wind"}.issubset(capacity_components)
        if "hybrid" in nature and has_solar_wind:
            result.at[idx] = "Hybrid"
            continue

        ids = " ".join(safe_str(row.get(col)) for col in id_cols if col)
        if any(app_id in ids for app_id in ("2200000305", "2200000319")):
            if {"Solar", "BESS"}.issubset(capacity_components):
                result.at[idx] = "Solar+BESS"
                continue

        result.at[idx] = components_to_type(components)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_data_to_be_captured(
    final_mapped_excel: Path,
    output_excel: Path | None = None,
) -> Path:
    """Generate data_to_be_captured.xlsx from the final mapped Excel.

    Parameters
    ----------
    final_mapped_excel : Path
        Path to 07_final_mapped.xlsx (or any final mapped DataFrame source).
    output_excel : Path, optional
        Path for the output file.  Defaults to ``data_to_be_captured.xlsx``
        in the same directory as the input.

    Returns
    -------
    Path to the generated Excel file.
    """
    if output_excel is None:
        output_excel = final_mapped_excel.parent / "data_to_be_captured.xlsx"

    print("\n" + "█" * 64)
    print("  STEP 4 — GENERATE data_to_be_captured.xlsx")
    print("  ─────────────────────────────────────────────────────────")
    print(f"  Source         : {final_mapped_excel}")
    print(f"  Output         : {output_excel}")
    print("█" * 64)

    # ── Load the final mapped data ────────────────────────────────────────
    if not final_mapped_excel.exists():
        raise FileNotFoundError(
            f"Final mapped Excel not found: {final_mapped_excel}. "
            "Run the full mapping pipeline first."
        )

    df = pd.read_excel(final_mapped_excel, sheet_name=0, engine="openpyxl")
    print(f"\n[Step 4] Source rows loaded: {len(df)}")
    print(f"[Step 4] Source columns: {len(df.columns)}")

    # ── Build the output DataFrame ────────────────────────────────────────
    output_data: dict[str, pd.Series] = {}
    mapped_cols: list[str] = []
    missing_cols: list[str] = []

    for final_name, candidates in FINAL_COLUMN_MAP:
        # Special handling for Bay No
        if final_name == "Bay No":
            output_data[final_name] = _resolve_bay_no(df)
            mapped_cols.append(final_name)
            continue

        # Recalculate from final CMETS + RE-effectiveness component evidence.
        if final_name == "Type":
            output_data[final_name] = _resolve_type(df)
            mapped_cols.append(final_name)
            continue

        # Calculated from status + application quantum, never extracted/copied.
        if final_name == "Granted  Quantum GNA/LTA(MW)":
            output_data[final_name] = _resolve_granted_quantum(df)
            mapped_cols.append(final_name)
            continue

        source_col = _find_source_col(df, candidates)
        if source_col is not None:
            output_data[final_name] = df[source_col].copy()
            mapped_cols.append(final_name)
        else:
            # Column not found — create empty column
            output_data[final_name] = pd.Series([None] * len(df), index=df.index)
            missing_cols.append(final_name)

    output_df = pd.DataFrame(output_data)

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n[Step 4] Output columns: {len(output_df.columns)}")
    print(f"[Step 4] Mapped columns: {len(mapped_cols)}")

    if missing_cols:
        print(f"[Step 4] ⚠ Missing columns (will be empty): {len(missing_cols)}")
        for mc in missing_cols:
            print(f"    ⚠ {mc}")

    print("\n[Step 4] Column fill rates:")
    for col in output_df.columns:
        non_null = output_df[col].notna().sum()
        # Also exclude blank strings
        non_blank = sum(
            1 for v in output_df[col]
            if v is not None and str(v).strip() not in ("", "None", "nan", "NaN")
        )
        print(f"    {col:<65} {non_blank}/{len(output_df)} filled")

    # ── Write output Excel ────────────────────────────────────────────────
    output_excel.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(str(output_excel), index=False, sheet_name="Data to be Captured")

    # Apply basic formatting
    try:
        from pipeline.mapping_handler.formatting import format_mapped_excel
        format_mapped_excel(str(output_excel))
    except Exception:
        pass  # Formatting is optional

    print(f"\n[Step 4] ✓ Excel saved → {output_excel}")
    print(f"[Step 4] ✓ Total rows: {len(output_df)} | Total columns: {len(output_df.columns)}")
    print("█" * 64)

    return output_excel
