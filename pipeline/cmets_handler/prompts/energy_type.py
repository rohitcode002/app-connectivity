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
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract only component type/capacity breakups from the source cell/table.
Use only these component labels: Solar, Wind, Hydro, BESS, PSP.
Include the associated MW value in parentheses when present.
Do NOT include any other words, sentences, or descriptions.
Examples: "Solar (300)", "Wind (12) + BESS (19)", "Solar (250) + Wind (250)",
"Solar (100) + BESS (50)", "Hydro (100) + BESS (25)", "PSP"."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"type": "Solar (300)"'
