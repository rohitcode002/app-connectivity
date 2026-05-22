"""
Name of the developers — prompt section
=========================================
Developer / applicant company name.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Name of the developers"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Applicant",
    "Name of Applicant",
    "Developer",
    "Name of Developers",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract the company/applicant name — NOT criterion values like "SECI LOA".
Value patterns: company names ending in Private Limited / Ltd / LLP.
Examples: ACME Greentech Urja Private Limited, Adani Renewable Energy Holding
Nine Limited, THDC India Limited, AM Green Energy Private Limited."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Name of the developers": "THDC India Limited"'
