"""
Mode / Criteria for applying — prompt section
================================================
LOA or PPA, Land BG, etc.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Mode(Criteria for applying)"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Criterion for applying",
    "Criteria for applying",
    "Mode",
    "Mode(Criteria for applying)",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Normalise any LOA/PPA keyword match to "LOA or PPA".
If any other non-empty mode/criteria value is present, output only "Land BG".
Known values: Land BG Route, Land Route, LOA or PPA, NHPC LOA, NTPC LOA,
SJVN LOA, REMCL LOA, SECI LOA."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Mode(Criteria for applying)": "SECI LOA"'
