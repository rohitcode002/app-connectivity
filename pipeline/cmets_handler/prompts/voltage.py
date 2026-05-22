"""
Voltage — prompt section
==========================
Voltage level of the substation / connectivity point.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Voltage"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Voltage",
    "Voltage Level",
    "kV Level",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract voltage level: "400 kV", "765 kV", "220 kV", "132 kV", "66 kV", "33 kV".
Extract from:
  • the substation column value (e.g. "Aligarh 400kV (PG)")
  • the row's project location or description ("at 400 kV level")
  • the table header/title if it mentions a voltage
Return as "<number> kV" (e.g. "400 kV"). Return null if not found."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Voltage": "400 kV"'
