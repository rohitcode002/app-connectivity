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
CONDITIONAL: extract this field ONLY if "Nature of Applicant" appears
in the detected column labels for this page.  If it is NOT listed in
the detected columns, return null for every row — do NOT guess or infer.
When present, extract the value EXACTLY as it appears in the table cell.
Do NOT shorten, paraphrase, or normalise — copy verbatim.

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
