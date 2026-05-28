"""
Energy type — prompt section
==============================
Type of energy source (Solar, Wind, BESS, etc.).
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "type"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Type of Source",
    "Generation Type",
    "Energy Source",
    "Additional Generation Capacity",
    "Planned additional capacity (MW)",
    "Additional capacity (MW)",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract only component type/capacity breakups from the source cell/table.
Use only these component labels: Solar, Wind, Hydro, BESS, PSP.
Include the associated MW value in parentheses when present.
Do NOT include any other words, sentences, or descriptions.
Examples: "Solar (300)", "Wind (12) + BESS (19)", "Solar (250) + Wind (250)",
"Solar (100) + BESS (50)", "Hydro (100) + BESS (25)", "PSP".

IMPORTANT — Also look at "Planned additional capacity (MW)" or
"Additional Generation Capacity" columns for component breakdowns:
If such a column says "100 MW Solar & 125 MW BESS" or "Solar(30)+(56)BESS"
or "16 (BESS)" or "240 MW Solar & 300 MW BESS", include ALL components and
their MW values in the Type output. For example:
  "100 MW Solar & 125 MW BESS" → "Solar (100) + BESS (125)"
  "Solar(30)+(56)BESS" → "Solar (30) + BESS (56)"
  "16 (BESS)" → "BESS (16)"
  "52 MW (Solar)" → "Solar (52)"
  "6.88 MW (BESS)" → "BESS (6.88)"
  "300 MW (BESS 4 Hr)" → "BESS (300)"
  "300 (BESS) 240 (Solar)" → "Solar (240) + BESS (300)"
  "240 MW Solar & 300 MW BESS" → "Solar (240) + BESS (300)"
  "170 MW Wind & 150 MW BESS" → "Wind (170) + BESS (150)"
  "11.25 (BESS)" → "BESS (11.25)"
If the table has a dedicated "Type of Source" column, prefer it; otherwise
derive the type from the "Planned additional capacity" or "Additional
Generation Capacity" column values."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"type": "Solar (300)"'
