"""
GNA Operationalization — prompt section
=========================================
GNA Operationalization Date + GNA Operationalization (Yes/No).

Yes/No is derived from the Date, so they share a file.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "GNA Operationalization Date",
    "GNA Operationalization (Yes/No)",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "GNA Operationalization Date": [
        "GNA Operationalization Date",
        "SCoD",
        "SCOD",
    ],
    "GNA Operationalization (Yes/No)": [],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
GNA Operationalization Date: look near SCoD / SCOD terms in the description text.
GNA Operationalization (Yes/No): return null — this is computed in post-processing."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"GNA Operationalization Date": "31.03.2030"',
    '"GNA Operationalization (Yes/No)": null',
]
