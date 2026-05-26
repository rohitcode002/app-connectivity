"""
Nature of Applicant — prompt section
======================================
Describes the type of entity applying for connectivity.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Nature of Applicant"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Nature of Applicant",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
This field is REQUIRED for every extracted row.
Extract the value EXACTLY as it appears in the table cell.
Do NOT shorten, paraphrase, or normalise — copy verbatim.
If the PDF table uses a merged/repeated "Nature of Applicant" cell for
multiple rows, copy that same visible value into every row covered by it.
If the page has a "Nature of Applicant" column but an individual row cell
is visually blank because it continues from the row above, use the nearest
preceding visible Nature of Applicant value in the same table.
Only return null when no Nature of Applicant value is visible anywhere for
that row or table block.

Known values:
  "REGS with installed capacity of 5 MW & above applying for Connectivity through electrical system of a generating station already having Connectivity to ISTS"
  "REGS with installed capacity of 5 MW & above applying for Connectivity to ISTS through electrical system of a generating station already having Connectivity to ISTS"
  "Generating station(s), including REGS(s), without ESS"
  "Generating station(s), including REGS(s), with ESS"
  "Generating station, including REGS, without ESS"
  "Generating station, including REGS, with ESS"
  "Generating station, including REGS, without ESS through a lead generator"
  "Generating station, including REGS, with ESS through a lead generator"
  "Standalone ESS"
  "Renewable Power Park developer"
  "Renewable Power Park Developer"
  "Generator"
  "Generator with ESS"
  "Generator (Hybrid)"
  "Generator (Wind)"
  "Generator (Solar)"
  "Captive generating plant"
  "Pumped Storage"
Pattern keywords: Generator, REGS, ESS, Standalone, Renewable Power Park, Captive, Pumped Storage."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Nature of Applicant": "Generating station, including REGS, without ESS"'
