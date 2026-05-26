"""
mapping_handler/merge.py — Row-by-row CMETS × Effectiveness merge
===================================================================
Takes a CMETS DataFrame and an effectiveness lookup dict, enriches
each row by matching Application IDs (GNA primary, LTA fallback, 5.2 GNA).

Edit this file to change:
  • Which CMETS columns are updated from effectiveness data
  • Which new enrichment columns are added
  • The matching / fallback strategy
"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd

from pipeline.shared_utils import (
    find_col,
    ids_from_cell,
    lookup_first,
    safe_float,
    safe_str,
    classify_project_type,
    components_from_type_keywords,
    components_to_type,
    normalize_type,
    parse_type_capacity,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_valid(val) -> bool:
    if val is None:
        return False
    if isinstance(val, float) and pd.isna(val):
        return False
    return str(val).strip().lower() not in ("", "none", "null", "na", "n/a", "-", "--", "nan")




# ── Enrichment column definitions ────────────────────────────────────────────

NEW_COLUMNS = [
    "Region", "Type of Project",
    "Installed capacity (MW) solar",  "Installed capacity (MW) wind",
    "Installed capacity (MW) ess",    "Installed capacity (MW) hydro",
    "Installed capacity (MW) hybrid",
]

_EFF_FIELD_TO_COL: list[tuple[str, str]] = [
    ("region",          "Region"),
    ("type_of_project", "Type of Project"),
    ("solar_mw",        "Installed capacity (MW) solar"),
    ("wind_mw",         "Installed capacity (MW) wind"),
    ("ess_mw",          "Installed capacity (MW) ess"),
    ("hydro_mw",        "Installed capacity (MW) hydro"),
]


# ── Columns to update from effectiveness when matched ────────────────────────
# These are effectiveness columns that overlap with CMETS columns.
# When a match is found via GNA/LTA/5.2, the effectiveness value
# overwrites the CMETS value (if the effectiveness value is valid).

_OVERLAPPING_UPDATES: list[tuple[str, list[str]]] = [
    # (effectiveness_key, [cmets_column_candidates...])
    ("name_of_applicant",       ["Name of Developers", "Name of the developers"]),
    ("substation",              ["Substation", "substaion"]),
    ("state",                   ["State"]),
    ("installed_capacity_mw",   ["Application Quantum (MW)(ST II)"]),
    ("connectivity_mw",         ["Application Quantum (MW)(ST II)"]),
    ("region",                  ["Region"]),
]

_TYPE_TO_COMPONENT: dict[str, str] = {
    "solar": "Solar",
    "wind": "Wind",
    "ess": "BESS",
    "hydro": "Hydro",
}


def _row_components(row: pd.Series, type_col: str | None) -> tuple[set[str], dict[str, float]]:
    """Read CMETS components from Type text and existing capacity columns."""
    type_text = row.get(type_col) if type_col else ""
    capacities = parse_type_capacity(safe_str(type_text))
    components = set(capacities) | components_from_type_keywords(type_text)

    column_to_component = {
        "Installed/Break-up Capacity (MW) Solar": "Solar",
        "Installed capacity (MW) solar": "Solar",
        "Installed/Break-up Capacity (MW) Wind": "Wind",
        "Installed capacity (MW) wind": "Wind",
        "Installed/Break-up Capacity (MW) Hydro": "Hydro",
        "Installed capacity (MW) hydro": "Hydro",
        "Battery MWh": "BESS",
        "Battery Injection (MW)": "BESS",
        "Battery Drawl (MW)": "BESS",
        "Installed capacity (MW) ess": "BESS",
        "PSP MWh": "PSP",
        "PSP Injection (MW)": "PSP",
        "PSP Drawl (MW)": "PSP",
    }
    for col_name, component in column_to_component.items():
        if safe_float(row.get(col_name)) > 0:
            components.add(component)
            capacities[component] = max(capacities.get(component, 0.0), safe_float(row.get(col_name)))

    return components, capacities


def _effectiveness_components(eff: dict) -> tuple[set[str], dict[str, float]]:
    """Map RE-effectiveness project text and MW columns to canonical components."""
    components = components_from_type_keywords(eff.get("type_of_project"))
    capacities: dict[str, float] = {}

    for eff_key, component in (
        ("solar_mw", "Solar"),
        ("wind_mw", "Wind"),
        ("ess_mw", "BESS"),
        ("hydro_mw", "Hydro"),
    ):
        val = safe_float(eff.get(eff_key))
        if val <= 0:
            continue
        if eff_key == "hydro_mw" and any(c == "PSP" for c in components):
            component = "PSP"
        components.add(component)
        capacities[component] = capacities.get(component, 0.0) + val

    cats = classify_project_type(eff.get("type_of_project") or "")
    for cat in cats:
        component = _TYPE_TO_COMPONENT.get(cat)
        if component:
            components.add(component)

    return components, capacities


def _has_detailed_cmets_breakup(components: set[str], capacities: dict[str, float]) -> bool:
    """True when CMETS already provides usable component detail."""
    if any(value > 0 for value in capacities.values()):
        return True
    specific = components - {"Hybrid"}
    return bool(specific)


def merge_re_type_and_capacity(row: pd.Series, eff: dict, type_col: str | None) -> str | None:
    """Merge CMETS and RE-effectiveness component evidence into final Type.

    CMETS component breakups win when present. When CMETS is generic or blank,
    RE-effectiveness project type/capacities fill the missing components. The
    combined component set is then passed through ``components_to_type``.
    """
    cmets_components, cmets_caps = _row_components(row, type_col)
    eff_components, eff_caps = _effectiveness_components(eff)

    if _has_detailed_cmets_breakup(cmets_components, cmets_caps):
        components = set(cmets_components)
        if eff_components - components:
            components |= eff_components
    else:
        components = set(cmets_components) | eff_components

    nature = safe_str(row.get("Nature of Applicant")).lower()
    merged_caps = {**eff_caps, **cmets_caps}
    has_solar_wind = merged_caps.get("Solar", 0.0) > 0 and merged_caps.get("Wind", 0.0) > 0
    if "hybrid" in nature and has_solar_wind:
        return "Hybrid"

    return components_to_type(components) or normalize_type(row.get(type_col) if type_col else None)


def apply_known_re_row_normalizations(
    row: pd.Series,
    type_value: str | None,
    type_col: str | None,
) -> str | None:
    """Hardcoded fixes for RE rows whose original PDF formatting is broken."""
    ids = " ".join(
        safe_str(row.get(col))
        for col in (
            "GNA/ST II Application ID",
            "LTA Application ID",
            "Application ID under Enhancement 5.2 or revision",
        )
    )
    if not re.search(r"\b(?:2200000305|2200000319)\b", ids):
        return type_value

    components, capacities = _row_components(row, type_col)
    has_solar = capacities.get("Solar", 0.0) > 0
    has_bess = capacities.get("BESS", 0.0) > 0
    if has_solar and has_bess:
        return "Solar+BESS"
    return type_value


# ── Main merge function ──────────────────────────────────────────────────────

def merge_rows(df: pd.DataFrame, lookup: dict) -> tuple[pd.DataFrame, dict]:
    """Apply effectiveness data to CMETS DataFrame rows.

    Returns (enriched_df, match_stats).
    """
    for col in NEW_COLUMNS:
        if col not in df.columns:
            df[col] = None

    gna_col      = find_col(df, "GNA/ST II Application ID")
    lta_col      = find_col(df, "LTA Application ID")
    col_52       = find_col(df, "Application ID under Enhancement 5.2 or revision")
    col_dev_name = find_col(df, "Name of the developers", "Name of developers", "Name of Developers")
    col_subst    = find_col(df, "substaion", "Substation")
    col_state    = find_col(df, "State")
    col_quantum  = find_col(df, "Application Quantum (MW)(ST II)")
    col_type     = find_col(df, "Type")

    matched_gna = matched_lta = matched_52 = unmatched = 0

    for idx, row in df.iterrows():
        # ── Find effectiveness record using ID cascade ────────────────
        eff       = None
        match_via = None

        # Try GNA first
        if gna_col:
            gna_ids = ids_from_cell(row.get(gna_col))
            eff     = lookup_first(gna_ids, lookup)
            if eff:
                match_via = "GNA"

        # Fallback to LTA
        if eff is None and lta_col:
            eff = lookup_first(ids_from_cell(row.get(lta_col)), lookup)
            if eff:
                match_via = "LTA"

        # Fallback to 5.2 GNA
        if eff is None and col_52:
            eff = lookup_first(ids_from_cell(row.get(col_52)), lookup)
            if eff:
                match_via = "5.2"

        if eff is None:
            unmatched += 1
            continue

        if match_via == "GNA":
            matched_gna += 1
        elif match_via == "LTA":
            matched_lta += 1
        else:
            matched_52 += 1

        # ── Update overlapping CMETS columns from effectiveness ───────
        if col_dev_name and _is_valid(eff.get("name_of_applicant")):
            df.at[idx, col_dev_name] = eff["name_of_applicant"]
        if col_subst and _is_valid(eff.get("substation")):
            df.at[idx, col_subst] = eff["substation"]
        if col_state and _is_valid(eff.get("state")):
            df.at[idx, col_state] = eff["state"]
        if col_quantum and _is_valid(eff.get("installed_capacity_mw")):
            df.at[idx, col_quantum] = eff["installed_capacity_mw"]

        # ── Populate new enrichment columns ───────────────────────────
        for eff_key, col_name in _EFF_FIELD_TO_COL:
            if _is_valid(eff.get(eff_key)):
                df.at[idx, col_name] = eff[eff_key]

        # ── Type from CMETS component breakup + RE-effectiveness lookup ──
        if col_type:
            current_row = df.loc[idx]
            merged_type = merge_re_type_and_capacity(current_row, eff, col_type)
            merged_type = apply_known_re_row_normalizations(current_row, merged_type, col_type)
            df.at[idx, col_type] = merged_type

        # ── Hybrid total ──────────────────────────────────────────────
        cats = classify_project_type(eff.get("type_of_project") or "")
        if "hybrid" in cats:
            total = sum(
                float(eff.get(k) or 0)
                for k in ("solar_mw", "wind_mw", "ess_mw", "hydro_mw")
                if _is_valid(eff.get(k))
            )
            if total > 0:
                df.at[idx, "Installed capacity (MW) hybrid"] = total

    stats = {
        "matched_gna": matched_gna,
        "matched_lta": matched_lta,
        "matched_52":  matched_52,
        "unmatched":   unmatched,
        "total_rows":  len(df),
    }
    return df, stats
