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
STRICTLY use ONLY these keywords: Solar, BESS, Wind, Solar+Wind, Solar+BESS.
Include the associated MW value in parentheses if present.
Do NOT include any other words, sentences, or descriptions.
Examples: "Solar (300)", "Wind (12) + BESS (19)", "Solar+Wind (500)",
"Solar+BESS (100)", "BESS (50)"."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"type": "Solar (300)"'
