"""
Status of application — prompt section
=========================================
Withdrawn / granted / Revoked / Applied.
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
Map the status wording to one of: Withdrawn, granted, Revoked, Applied.
Use granted when the application is granted/approved.
Use Withdrawn when wording says withdrawn.
Use Revoked when wording says revoked/cancelled/rejected.
Use Applied when no status is found, or for submitted, applied, pending, under process, or any active/non-final status."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Status of application(Withdrawn / granted. Revoked.)": "Applied"'
