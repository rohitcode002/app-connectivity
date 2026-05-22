"""
Status of application — prompt section
=========================================
Withdrawn / granted / Revoked.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Status of application(Withdrawn / granted. Revoked.)"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Status of Application",
    "Status",
    "Withdrawn / granted / Revoked",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Map the status wording to one of: Withdrawn, granted, Revoked."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Status of application(Withdrawn / granted. Revoked.)": "granted"'
