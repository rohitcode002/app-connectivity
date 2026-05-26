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
    ],
    "Battery Drawl (MW)": [
        "Battery Drawl (MW)",
        "BESS Drawl (MW)",
        "Drawl (MW)",
        "Drawal (MW)",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Fill ONLY when BESS / Battery context is present in the row.
- Battery MWh: MWh capacity of the battery.
  DURATION FORMULA: If a duration is found in the text such as "4 hours",
  "four (4) hours", "2 hrs", etc., and Battery Injection (MW) is known but
  Battery MWh is NOT explicitly stated, then compute:
      Battery MWh = Battery Injection (MW) × duration_hours
  For example: 50 MW injection with "4 hours" → Battery MWh = 200.
- Battery Injection (MW): injection is typically SMALLER than drawl for BESS.
  Look for "Injection" or "Inj" in BESS tables.
- Battery Drawl (MW): drawl is typically LARGER than injection for BESS.
  Look for "Drawl" or "Drawal" in BESS tables.
- If both BESS and PSP are present in same row, use Battery* for BESS
  values and PSP* for pump storage values."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Battery MWh": null',
    '"Battery Injection (MW)": null',
    '"Battery Drawl (MW)": null',
]
