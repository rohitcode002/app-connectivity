"""
Substation — prompt section
============================
Connectivity location / pooling station / injection point.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "substaion"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Connectivity Location (As per Application)",
    "Nearest Pooling Station (As per Application)",
    "Connectivity Granted at",
    "Location requested for Grant of Stage-II Connectivity",
    "Connectivity Injection Point",
    "Sub-station",
    "Substation",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract the substation / connectivity location name.
Value patterns: CityName-RomanNumeral (e.g. Bhadla-V, Fatehgarh-IV)
or CityName (PG) (e.g. Aligarh (PG))."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"substaion": "Aligarh (PG)"'
