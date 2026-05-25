#!/usr/bin/env python3
"""
DTBC Validation Script
======================
Compares generated_data_to_be_captured.xlsx against OG.csv (ground truth).

Matching Logic (ID Priority):
  1. GNA/ST II Application ID
  2. LTA Application ID  (fallback if GNA not found)
  3. Application ID under Enhancement 5.2 or revision  (fallback if LTA not found)

When multiple generated rows match on an ID, the script picks the BEST
match (highest column-overlap score) to avoid fan-out.

Comparison Logic:
  - For each matched row-pair, checks every common column.
  - Uses "in" containment: generated_value IN og_value  (case-insensitive).
  - Accuracy = matched_correct / total_checked  per column and overall.

Output:
  - Console summary report
  - validation_report.xlsx  with per-row results
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

# ---------------------------------------------------------------------------
# 1.  LOAD FILES
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OG_PATH = os.path.join(SCRIPT_DIR, "OG.csv")
GEN_PATH = os.path.join(SCRIPT_DIR, "generated_data_to_be_captured.xlsx")
REPORT_PATH = os.path.join(SCRIPT_DIR, "validation_report.xlsx")

# ---------------------------------------------------------------------------
# COLUMNS TO EXCLUDE FROM VALIDATION
# Add column names here to skip them.  If empty [], all columns are validated.
# Example:
#   EXCLUDE_COLUMNS = ["Region", "Coordinates", "Bay No"]
# ---------------------------------------------------------------------------
EXCLUDE_COLUMNS = ["Region"]

print("=" * 80)
print("  DTBC VALIDATION REPORT")
print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)


# ---- Load OG.csv with multi-row header ----
raw = pd.read_csv(OG_PATH, header=None)

# Row 1 = main headers, Row 2 = sub-headers (for composite columns)
main_headers = raw.iloc[1].tolist()
sub_headers  = raw.iloc[2].tolist()

# Build composite column names to match generated xlsx naming convention.
# The OG.csv uses merged headers: the parent name (row1) only appears on the
# first sub-column; subsequent sub-columns have an empty row1.  We carry the
# last non-empty row1 value forward so "Wind", "Hybrid" etc. get the correct
# parent prefix like "Installed/Break-up Capacity (MW) Wind".
og_col_names = []
last_main = ""
for i, (main, sub) in enumerate(zip(main_headers, sub_headers)):
    m = str(main).strip() if pd.notna(main) else ""
    s = str(sub).strip()  if pd.notna(sub)  else ""
    if m:
        last_main = m  # remember the parent header
    if m and s:
        # First sub-column under a new parent  e.g. "Installed/Break-up Capacity (MW) Solar"
        og_col_names.append(f"{m} {s}")
    elif m and not s:
        # Standalone column with no sub-header  e.g. "Type"
        og_col_names.append(m)
    elif not m and s:
        # Continuation sub-column under the same parent  e.g. "" + "Wind" → "Installed/Break-up Capacity (MW) Wind"
        og_col_names.append(f"{last_main} {s}")
    else:
        og_col_names.append(f"_col_{i}")

# Data starts from row 4 (0-indexed: rows 0=extra header, 1=headers, 2=sub, 3=empty)
og = raw.iloc[4:].reset_index(drop=True).copy()
og.columns = og_col_names

# Drop rows that are entirely NaN
og = og.dropna(how="all").reset_index(drop=True)

# ---- Load Generated XLSX ----
gen = pd.read_excel(GEN_PATH)


# ---------------------------------------------------------------------------
# 2.  IDENTIFY COMMON COLUMNS  (excluding ID columns themselves)
# ---------------------------------------------------------------------------

ID_COLS = [
    "GNA/ST II Application ID",
    "LTA Application ID",
    "Application ID under Enhancement 5.2 or revision",
]

SKIP_COLS = {"Sr.no.", "Group"}

common_cols = sorted(
    set(og.columns).intersection(set(gen.columns))
    - set(ID_COLS)
    - SKIP_COLS
    - set(EXCLUDE_COLUMNS)
)

print(f"\n📂  OG rows        : {len(og)}")
print(f"📂  Generated rows : {len(gen)}")
if EXCLUDE_COLUMNS:
    print(f"🚫  Excluded cols  : {len(EXCLUDE_COLUMNS)}")
    for c in EXCLUDE_COLUMNS:
        print(f"    ✗ {c}")
print(f"📊  Validated cols : {len(common_cols)}")
for c in common_cols:
    print(f"    • {c}")
print()


# ---------------------------------------------------------------------------
# 3.  NORMALISE ID COLUMNS FOR MATCHING
# ---------------------------------------------------------------------------

def normalise_id(val):
    """Convert an ID value to a clean string for matching."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    try:
        f = float(s)
        if f == int(f):
            s = str(int(f))
    except (ValueError, OverflowError):
        pass
    return s if s else None


for col in ID_COLS:
    if col in og.columns:
        og[col] = og[col].apply(normalise_id)
    if col in gen.columns:
        gen[col] = gen[col].apply(normalise_id)


# ---------------------------------------------------------------------------
# 4.  BUILD INDEX MAPS ON GENERATED DATA   (ID → list of row indices)
# ---------------------------------------------------------------------------

gen_index_gna = {}
gen_index_lta = {}
gen_index_52  = {}

for idx, row in gen.iterrows():
    gna  = row.get("GNA/ST II Application ID")
    lta  = row.get("LTA Application ID")
    five2 = row.get("Application ID under Enhancement 5.2 or revision")
    if gna:
        gen_index_gna.setdefault(gna, []).append(idx)
    if lta:
        gen_index_lta.setdefault(lta, []).append(idx)
    if five2:
        gen_index_52.setdefault(five2, []).append(idx)


# ---------------------------------------------------------------------------
# 5.  HELPERS: normalise values + date-aware comparison
# ---------------------------------------------------------------------------

import re
from dateutil import parser as dateparser

# Columns that contain date values (detected by name keywords)
DATE_KEYWORDS = ["date", "meeting"]


def is_date_column(col_name):
    """Check if a column likely holds date values."""
    cl = col_name.lower()
    return any(kw in cl for kw in DATE_KEYWORDS)


def safe_str(val):
    """Convert to lower-case stripped string; return None if empty/NaN."""
    if pd.isna(val):
        return None
    s = str(val).strip().lower()
    # Remove trailing '.0' from numeric strings
    if s.endswith(".0"):
        try:
            s = str(int(float(s)))
        except (ValueError, OverflowError):
            pass
    return s if s else None


def parse_date(val_str):
    """
    Try to extract a (day, month, year) tuple from a date string.
    Handles formats like: dd.mm.yyyy, dd-mm-yyyy, dd/mm/yyyy, yyyy-mm-dd,
    and mixed formats.  Returns None on failure.
    """
    if val_str is None:
        return None
    s = val_str.strip()
    if not s:
        return None

    # Try common explicit patterns first  (dd.mm.yyyy, dd-mm-yyyy, dd/mm/yyyy)
    m = re.match(r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})', s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (d, mo, y)
        # Maybe it's mm/dd/yyyy — swap if day > 12
        if d > 12 and 1 <= mo <= 31:
            return (mo, d, y)

    # Try yyyy-mm-dd
    m = re.match(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (d, mo, y)

    # Fallback: dateutil parser
    try:
        dt = dateparser.parse(s, dayfirst=True)
        return (dt.day, dt.month, dt.year)
    except (ValueError, OverflowError, TypeError):
        pass

    return None


def values_match(og_val, gen_val, col_name):
    """
    Check if gen_val matches og_val for a given column.
    - Date columns: compare parsed date tuples (format-agnostic).
    - Others: use 'in' containment (either direction).
    """
    if og_val is None and gen_val is None:
        return True  # both empty = match

    if og_val is None or gen_val is None:
        return False

    # Date columns: compare actual date values
    if is_date_column(col_name):
        og_date  = parse_date(og_val)
        gen_date = parse_date(gen_val)
        if og_date and gen_date:
            return og_date == gen_date
        # If parsing fails, fall through to containment check

    # General containment: gen_val in og_val  OR  og_val in gen_val
    if gen_val in og_val or og_val in gen_val:
        return True

    return False


def overlap_score(og_row, gen_row, cols):
    """Return (matches, total_compared) between two rows on given columns."""
    matches = 0
    compared = 0
    for col in cols:
        og_val  = safe_str(og_row.get(col))
        gen_val = safe_str(gen_row.get(col))
        compared += 1
        if values_match(og_val, gen_val, col):
            matches += 1
    return matches, compared


# ---------------------------------------------------------------------------
# 6.  MATCH OG ROWS → BEST GENERATED ROW  (priority: GNA > LTA > 5.2)
# ---------------------------------------------------------------------------

matches = []  # list of (og_idx, gen_idx, matched_via)

matched_og   = 0
unmatched_og = 0
unmatched_og_rows = []

for og_idx, og_row in og.iterrows():
    gna   = og_row.get("GNA/ST II Application ID")
    lta   = og_row.get("LTA Application ID")
    five2 = og_row.get("Application ID under Enhancement 5.2 or revision")

    candidates   = None
    matched_via  = None

    # Priority 1: GNA
    if gna and gna in gen_index_gna:
        candidates  = gen_index_gna[gna]
        matched_via = "GNA/ST II"
    # Priority 2: LTA
    elif lta and lta in gen_index_lta:
        candidates  = gen_index_lta[lta]
        matched_via = "LTA"
    # Priority 3: 5.2
    elif five2 and five2 in gen_index_52:
        candidates  = gen_index_52[five2]
        matched_via = "5.2"

    if candidates:
        matched_og += 1
        # Pick the single best match among candidates
        if len(candidates) == 1:
            best = candidates[0]
        else:
            best = None
            best_score = (-1, -1)
            for gi in candidates:
                score = overlap_score(og_row, gen.iloc[gi], common_cols)
                if score > best_score:
                    best_score = score
                    best = gi
        matches.append((og_idx, best, matched_via))
    else:
        unmatched_og += 1
        unmatched_og_rows.append({
            "OG_Row": og_idx + 1,
            "GNA_ID": gna or "",
            "LTA_ID": lta or "",
            "5.2_ID": five2 or "",
        })

print(f"🔗  Matched OG rows   : {matched_og}  ({matched_og}/{len(og)} = {matched_og/len(og)*100:.1f}%)")
print(f"❌  Unmatched OG rows : {unmatched_og}")
print(f"🔗  Total match pairs : {len(matches)}  (1:1 best-match per OG row)")
print()


# ---------------------------------------------------------------------------
# 7.  COMPARE EACH MATCHED PAIR
# ---------------------------------------------------------------------------

# Per-column counters
col_correct    = {c: 0 for c in common_cols}
col_incorrect  = {c: 0 for c in common_cols}
col_both_empty = {c: 0 for c in common_cols}
col_og_only    = {c: 0 for c in common_cols}
col_gen_only   = {c: 0 for c in common_cols}

detail_rows = []

for og_idx, gen_idx, matched_via in matches:
    og_row  = og.iloc[og_idx]
    gen_row = gen.iloc[gen_idx]

    row_detail = {
        "OG_Row": og_idx + 1,
        "Gen_Row": gen_idx + 1,
        "Matched_Via": matched_via,
        "GNA_ID": og_row.get("GNA/ST II Application ID", ""),
        "LTA_ID": og_row.get("LTA Application ID", ""),
        "5.2_ID": og_row.get("Application ID under Enhancement 5.2 or revision", ""),
    }

    row_correct = 0
    row_total   = 0

    for col in common_cols:
        og_val  = safe_str(og_row.get(col))
        gen_val = safe_str(gen_row.get(col))

        if og_val is None and gen_val is None:
            # Both empty → count as correct match
            col_both_empty[col] += 1
            col_correct[col] += 1
            status = "BOTH_EMPTY"
            row_correct += 1
            row_total += 1
        elif og_val is not None and gen_val is None:
            col_og_only[col] += 1
            status = "MISSING_IN_GEN"
            row_total += 1
        elif og_val is None and gen_val is not None:
            col_gen_only[col] += 1
            status = "EXTRA_IN_GEN"
            row_total += 1
        elif values_match(og_val, gen_val, col):
            col_correct[col] += 1
            status = "MATCH"
            row_correct += 1
            row_total += 1
        else:
            col_incorrect[col] += 1
            status = "MISMATCH"
            row_total += 1

        row_detail[f"{col}__status"] = status
        row_detail[f"{col}__og"]     = og_val if og_val else ""
        row_detail[f"{col}__gen"]    = gen_val if gen_val else ""

    row_detail["row_accuracy"] = (
        f"{row_correct}/{row_total} ({row_correct/row_total*100:.1f}%)"
        if row_total > 0 else "N/A"
    )
    detail_rows.append(row_detail)




# ---------------------------------------------------------------------------
# 8.  PRINT PER-COLUMN ACCURACY REPORT
# ---------------------------------------------------------------------------

print("-" * 90)
print(f"  {'COLUMN':<65} {'ACCURACY':>20}")
print("-" * 90)

total_correct = 0
total_checked = 0

for col in common_cols:
    correct   = col_correct[col]
    incorrect = col_incorrect[col]
    missing   = col_og_only[col]
    checked   = correct + incorrect + missing
    acc = (correct / checked * 100) if checked > 0 else 0.0

    total_correct += correct
    total_checked += checked

    tag = "✅" if acc >= 80 else ("⚠️ " if acc >= 50 else "❌")
    col_display = col[:62] + "..." if len(col) > 65 else col
    print(f"  {tag} {col_display:<63} {correct:>4}/{checked:<4} ({acc:5.1f}%)")

overall_acc = (total_correct / total_checked * 100) if total_checked > 0 else 0.0

print("-" * 90)
print(f"  {'OVERALL':.<65} {total_correct:>4}/{total_checked:<4} ({overall_acc:5.1f}%)")
print("=" * 90)


# ---------------------------------------------------------------------------
# 9.  DETAILED BREAKDOWN
# ---------------------------------------------------------------------------

print("\n📋  DETAILED BREAKDOWN PER COLUMN\n")
for col in common_cols:
    correct    = col_correct[col]
    incorrect  = col_incorrect[col]
    both_empty = col_both_empty[col]
    og_only    = col_og_only[col]
    gen_only   = col_gen_only[col]
    checked    = correct + incorrect + og_only
    acc = (correct / checked * 100) if checked > 0 else 0.0

    print(f"  📌 {col}")
    print(f"     ✅ Match       : {correct}")
    print(f"     ❌ Mismatch    : {incorrect}")
    print(f"     ⬜ Both Empty  : {both_empty}")
    print(f"     🟡 OG Only     : {og_only}  (present in OG, missing in generated)")
    print(f"     🔵 Gen Only    : {gen_only}  (present in generated, missing in OG)")
    print(f"     📊 Accuracy    : {acc:.1f}%  ({correct}/{checked})")
    print()


# ---------------------------------------------------------------------------
# 10. MATCH METHOD SUMMARY
# ---------------------------------------------------------------------------

match_via_counts = {}
for _, _, via in matches:
    match_via_counts[via] = match_via_counts.get(via, 0) + 1

print("-" * 90)
print("  MATCH METHOD DISTRIBUTION")
print("-" * 90)
for method, count in sorted(match_via_counts.items()):
    pct = count / len(matches) * 100 if matches else 0
    print(f"  {method:<30} : {count:>4} rows  ({pct:.1f}%)")
print()


# ---------------------------------------------------------------------------
# 11. SAVE DETAILED REPORT TO EXCEL
# ---------------------------------------------------------------------------

if detail_rows:
    detail_df = pd.DataFrame(detail_rows)

    # Summary sheet
    summary_data = []
    for col in common_cols:
        correct    = col_correct[col]
        incorrect  = col_incorrect[col]
        og_only    = col_og_only[col]
        both_empty = col_both_empty[col]
        gen_only   = col_gen_only[col]
        checked    = correct + incorrect + og_only
        acc = (correct / checked * 100) if checked > 0 else 0.0
        summary_data.append({
            "Column": col,
            "Match (Correct)": correct,
            "Mismatch": incorrect,
            "Missing in Generated": og_only,
            "Extra in Generated": gen_only,
            "Both Empty": both_empty,
            "Total Checked": checked,
            "Accuracy (%)": round(acc, 2),
        })

    # Append an OVERALL TOTAL row
    summary_data.append({
        "Column": "── OVERALL TOTAL ──",
        "Match (Correct)": total_correct,
        "Mismatch": sum(col_incorrect[c] for c in common_cols),
        "Missing in Generated": sum(col_og_only[c] for c in common_cols),
        "Extra in Generated": sum(col_gen_only[c] for c in common_cols),
        "Both Empty": sum(col_both_empty[c] for c in common_cols),
        "Total Checked": total_checked,
        "Accuracy (%)": round(overall_acc, 2),
    })
    summary_df = pd.DataFrame(summary_data)

    # Mismatch details sheet
    mismatch_rows = []
    for detail in detail_rows:
        has_mismatch = any(
            detail.get(f"{col}__status") == "MISMATCH" for col in common_cols
        )
        if has_mismatch:
            mrow = {
                "OG_Row": detail["OG_Row"],
                "Gen_Row": detail["Gen_Row"],
                "Matched_Via": detail["Matched_Via"],
                "GNA_ID": detail["GNA_ID"],
                "LTA_ID": detail["LTA_ID"],
                "5.2_ID": detail["5.2_ID"],
            }
            for col in common_cols:
                if detail.get(f"{col}__status") == "MISMATCH":
                    mrow[f"{col} (OG)"] = detail.get(f"{col}__og", "")
                    mrow[f"{col} (Gen)"] = detail.get(f"{col}__gen", "")
            mismatch_rows.append(mrow)
    mismatch_df = pd.DataFrame(mismatch_rows) if mismatch_rows else pd.DataFrame()

    # Unmatched OG rows sheet
    unmatched_df = pd.DataFrame(unmatched_og_rows) if unmatched_og_rows else pd.DataFrame()

    with pd.ExcelWriter(REPORT_PATH, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Column Accuracy", index=False)
        if not mismatch_df.empty:
            mismatch_df.to_excel(writer, sheet_name="Mismatches", index=False)
        if not unmatched_df.empty:
            unmatched_df.to_excel(writer, sheet_name="Unmatched OG Rows", index=False)
        detail_df.to_excel(writer, sheet_name="Full Detail", index=False)

    print(f"💾  Detailed report saved to: {REPORT_PATH}")
else:
    print("⚠️  No matches found — no report generated.")

print("\n✅  Validation complete.")

# ---------------------------------------------------------------------------
# FINAL OVERALL ACCURACY (printed last for visibility)
# ---------------------------------------------------------------------------
print()
print("=" * 90)
print(f"  📊  OVERALL ACCURACY:  {total_correct} / {total_checked}  =  {overall_acc:.1f}%")
print(f"  🔗  Rows Matched   :  {matched_og} / {len(og)}  (via GNA/ST II → LTA → 5.2 fallback)")
print(f"  📋  Columns Checked :  {len(common_cols)}")
print("=" * 90)
