"""
Project Location + State — prompt section
==========================================
State is derived from Project Location, so they share a file.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = ["Project Location", "State"]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Project Location",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
- Project Location: extract as-is from the table.
- State: derive from Project Location — output the Indian state/UT name only."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Project Location": "bulandshahr distt, uttar pradesh"',
    '"State": "uttar pradesh"',
]
