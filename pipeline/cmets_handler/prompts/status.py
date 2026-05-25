"""
Status of application — prompt section
=========================================
Withdrawn / Granted / Applied.
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
Map the status wording to one of: Withdrawn, Granted, Applied.
Use Granted when the application is granted/approved.
Use Withdrawn when wording says withdrawn/revoked/cancelled/rejected.
Use Applied for submitted, applied, pending, under process, or any active/non-final status."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Status of application(Withdrawn / granted. Revoked.)": "Granted"'
