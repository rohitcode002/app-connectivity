"""
jcc_handler/models.py — Schema & constants for JCC table extraction
=====================================================================
Edit this file to change the target columns, keyword gate, or
canonical column names for JCC meeting PDFs.
"""

from __future__ import annotations

# Keywords that must ALL appear on a page for it to be considered a target page
REQUIRED_KEYWORDS = ["Pooling", "Quantum", "Connectivity"]

# Fragments (lower-cased) that identify the connectivity table header row
TARGET_COLUMN_FRAGMENTS = [
    "pooling",
    "applicant",
    "quantum",
    "gen comm",
    "schedule as per",
    "connectivity start",
]

# Raw table columns exposed in JSON output. Keep these narrow: JCC extraction
# should copy the PDF cells, then add derived COD/GNA/TGNA columns.
COLUMN_NAMES = [
    "pooling_station",
    "connectivity_applicant",
    "connectivity_quantum_mw",
    "schedule_as_per_current_jcc",
    "connectivity_start_date_under_gna",
]

# Values computed after extraction from the schedule columns.
COMPUTED_COLUMN_NAMES = [
    "total_COD",
    "COD_Found",
    "effective_date",
    "TGNA",
    "GNA",
    # Compatibility fields used by the matching layers.
    "substation",
    "gna_lta_id",
]

EXCEL_COLUMN_NAMES = COLUMN_NAMES + COMPUTED_COLUMN_NAMES
