"""
PSP (Pump Storage) — prompt section
=====================================
PSP MWh, PSP Injection (MW), PSP Drawl (MW).

These three columns overlap due to shared PSP extraction rules,
so they share a single prompt file.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "PSP MWh",
    "PSP Injection (MW)",
    "PSP Drawl (MW)",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "PSP MWh": ["PSP MWh", "Pump Storage MWh"],
    "PSP Injection (MW)": ["PSP Injection (MW)", "Pump Storage Injection (MW)"],
    "PSP Drawl (MW)": ["PSP Drawl (MW)", "Pump Storage Drawl (MW)", "PSP Drawal (MW)"],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Fill ONLY when pump storage / PSP wording is explicitly present in the row.
Detection: check both "Nature of Applicant" and "Type" fields for
  keywords like "Pumped Storage", "PSP", "pump storage".
- PSP Injection (MW): look for "Max Injection", "Injection" values
  in PSP-context rows. Extract the numeric MW value.
- PSP Drawl (MW): look for "Max Drawl", "Drawal", "Drawl" values
  in PSP-context rows. Extract the numeric MW value.
- PSP MWh: MWh capacity if explicitly stated.
IMPORTANT: if PSP values are populated for a row, Battery Injection (MW)
  must be cleared (set to null) — PSP injection takes precedence."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"PSP MWh": null',
    '"PSP Injection (MW)": null',
    '"PSP Drawl (MW)": null',
]
