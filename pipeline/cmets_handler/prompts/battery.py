"""
Battery (BESS) — prompt section
==================================
Battery MWh, Battery Injection (MW), Battery Drawl (MW).

These three columns overlap due to shared BESS extraction rules,
so they share a single prompt file.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "Battery MWh",
    "Battery Injection (MW)",
    "Battery Drawl (MW)",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "Battery MWh": [
        "Battery MWh",
        "BESS MWh",
        "Battery (MWh)",
        "Battery Energy Storage (MWh)",
    ],
    "Battery Injection (MW)": [
        "Battery Injection (MW)",
        "BESS Injection (MW)",
        "Injection (MW)",
        "Planned additional capacity (MW)",
        "Additional Generation Capacity",
        "Additional capacity (MW)",
    ],
    "Battery Drawl (MW)": [
        "Battery Drawl (MW)",
        "BESS Drawl (MW)",
        "Drawl (MW)",
        "Drawal (MW)",
        "Additional Drawl Requested (MW)",
        "Additional Drawl Requested",
        "Additional Drawl (MW)",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Fill ONLY when BESS / Battery context is present in the row.

CRITICAL — Battery Injection (MW):
The Battery Injection (MW) value comes from the "Additional Generation Capacity"
or "Planned additional capacity (MW)" columns. These columns contain BESS
capacity in patterns like:
  • "6.88 MW (BESS)"    → Battery Injection (MW) = 6.88
  • "16 (BESS)"         → Battery Injection (MW) = 16
  • "28.75 (BESS)"      → Battery Injection (MW) = 28.75
  • "11.25 (BESS)"      → Battery Injection (MW) = 11.25
  • "BESS (45)"         → Battery Injection (MW) = 45
  • "150 MW BESS"       → Battery Injection (MW) = 150
  • "300 MW (BESS 4hr)" → Battery Injection (MW) = 300, duration = 4 hours
  • "300 MW (BESS 2 Hr)" → Battery Injection (MW) = 300, duration = 2 hours
  • "590 MW BESS"       → Battery Injection (MW) = 590
  • "300 (BESS) 240 (Solar)" → Battery Injection (MW) = 300 (only BESS part)
  • "100 MW Solar & 125 MW BESS" → Battery Injection (MW) = 125 (only BESS part)
Extract ONLY the numeric MW value associated with BESS, not the Solar/Wind part.
IMPORTANT: Do NOT confuse the existing connectivity quantum (from "App. No. &
Quantum" column) with Battery Injection. Battery Injection comes only from the
additional/planned capacity column's BESS component.

CRITICAL — Battery Drawl (MW):
If the table has an "Additional Drawl Requested (MW)" or "Additional Drawl (MW)"
column, extract the numeric MW value as Battery Drawl (MW).
Example: "3 MW" → Battery Drawl (MW) = 3

CRITICAL — Battery MWh:
  DURATION FORMULA: If a duration is found in the text such as "4 hours",
  "four (4) hours", "2 hrs", "BESS 4hr", "BESS 4 Hr", "BESS 2 Hr", etc.,
  and Battery Injection (MW) is known but Battery MWh is NOT explicitly stated,
  then compute:
      Battery MWh = Battery Injection (MW) × duration_hours
  For example: 300 MW injection with "4 Hr" → Battery MWh = 1200.
  For example: 300 MW injection with "2 Hr" → Battery MWh = 600.
  For example: 50 MW injection with "4 hours" → Battery MWh = 200.
  IMPORTANT: If NO duration is mentioned anywhere and MWh is NOT explicitly
  stated, set Battery MWh to null, NOT 0.
  Only set Battery MWh to a value when you can compute it from duration × injection.

- If both BESS and PSP are present in same row, use Battery* for BESS
  values and PSP* for pump storage values."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Battery MWh": null',
    '"Battery Injection (MW)": null',
    '"Battery Drawl (MW)": null',
]
