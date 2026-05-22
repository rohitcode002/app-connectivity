"""
Quantum (MW) — prompt section
================================
Application Quantum and Granted Quantum.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "Application Quantum (MW)(ST II)",
    "Granted Quantum GNA/LTA(MW)",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "Application Quantum (MW)(ST II)": [
        "Installed Capacity (MW)",
        "Connectivity Quantum (MW)",
        "Application Quantum (MW)(ST II)",
        "Capacity (MW)",
    ],
    "Granted Quantum GNA/LTA(MW)": [
        "Granted Quantum GNA/LTA(MW)",
        "Granted Quantum (MW)",
        "Connectivity Quantum (MW) granted",
        "Granted Connectivity Quantum",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Application Quantum: the MW capacity APPLIED for.
Granted Quantum: the MW capacity actually GRANTED (may differ from applied)."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Application Quantum (MW)(ST II)": "300"',
    '"Granted Quantum GNA/LTA(MW)": "300"',
]
