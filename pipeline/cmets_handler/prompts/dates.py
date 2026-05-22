"""
Date columns — prompt section
================================
Application/Submission Date, Applied Start of Connectivity date,
Date from which additional capacity is to be added.

Grouped together as they follow the same date extraction pattern.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "Applied Start of Connectivity sought by developer date",
    "Date from which additional capacity is to be added",
    "Application/Submission Date",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "Applied Start of Connectivity sought by developer date": [
        "Start Date of Connectivity (As per Application)",
        "Applied Start of Connectivity sought by developer date",
        "Start Date of Connectivity",
    ],
    "Date from which additional capacity is to be added": [
        "Date from which additional capacity is to be added",
        "Additional Capacity Date",
    ],
    "Application/Submission Date": [
        "Application No. & Date",
        "Submission Date",
        "Application Date",
        "Application/Submission Date",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Extract date values only. For "Application No. & Date" extract only the date part.
"Date from which additional capacity is to be added" — only fill if explicitly present."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"Applied Start of Connectivity sought by developer date": "16.04.2026"',
    '"Date from which additional capacity is to be added": null',
    '"Application/Submission Date": "15.02.2024"',
]
