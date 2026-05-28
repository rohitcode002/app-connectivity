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

IMPORTANT — Battery Injection (MW) from "Additional Generation Capacity" /
"Planned additional capacity (MW)" columns:
The Battery Injection (MW) value is often found in columns like
"Planned additional capacity (MW)" or "Additional Generation Capacity".
These columns contain BESS capacity in patterns like:
  • "16 (BESS)"         → Battery Injection (MW) = 16
  • "28.75 (BESS)"      → Battery Injection (MW) = 28.75
  • "BESS (45)"          → Battery Injection (MW) = 45
  • "150 MW BESS"        → Battery Injection (MW) = 150
  • "300 MW (BESS 4hr)"  → Battery Injection (MW) = 300, duration = 4 hours
  • "590 MW BESS"        → Battery Injection (MW) = 590
  • "100 MW Solar & 125 MW BESS" → Battery Injection (MW) = 125 (only BESS part)
Extract ONLY the numeric MW value associated with BESS, not the Solar/Wind part.

IMPORTANT — Battery Drawl (MW) from "Additional Drawl Requested" columns:
If the table has an "Additional Drawl Requested (MW)" or "Additional Drawl (MW)"
column, extract the numeric MW value as Battery Drawl (MW).
Example: "3 MW" → Battery Drawl (MW) = 3

- Battery MWh: MWh capacity of the battery.
  DURATION FORMULA: If a duration is found in the text such as "4 hours",
  "four (4) hours", "2 hrs", "BESS 4hr", "BESS 4 Hr", etc., and Battery
  Injection (MW) is known but Battery MWh is NOT explicitly stated, then compute:
      Battery MWh = Battery Injection (MW) × duration_hours
  For example: 300 MW injection with "4hr" → Battery MWh = 1200.
  For example: 50 MW injection with "4 hours" → Battery MWh = 200.
  IMPORTANT: If NO duration is mentioned anywhere and MWh is NOT explicitly stated,
  set Battery MWh to 0 (zero), NOT null.
- Battery Injection (MW): injection is typically SMALLER than drawl for BESS.
  Look for "Injection" or "Inj" in BESS tables, AND also look in
  "Planned additional capacity" or "Additional Generation Capacity" columns
  for BESS MW values as described above.
- Battery Drawl (MW): drawl is typically LARGER than injection for BESS.
  Look for "Drawl" or "Drawal" in BESS tables, AND also look in
  "Additional Drawl Requested" columns.
- If both BESS and PSP are present in same row, use Battery* for BESS
  values and PSP* for pump storage values."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Battery MWh": null',
    '"Battery Injection (MW)": null',
    '"Battery Drawl (MW)": null',
]
