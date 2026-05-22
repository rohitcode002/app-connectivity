"""
Application IDs — prompt section
==================================
GNA/ST II Application ID + LTA Application ID +
Application ID under Enhancement 5.2 or revision.

These three columns overlap due to the 5.2 routing rule,
so they share a single prompt file.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = [
    "GNA/ST II Application ID",
    "LTA Application ID",
    "Application ID under Enhancement 5.2 or revision",
]

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = {
    "GNA/ST II Application ID": [
        "Application No. & Date",
        "Application ID",
        "GNA Application ID",
        "ST-II Application ID",
        "GNA/ST II Application ID",
    ],
    "LTA Application ID": [
        "App. No. & Conn. Quantum (MW) of already granted Connectivity",
        "LTA Application ID",
        "LTA App ID",
    ],
    "Application ID under Enhancement 5.2 or revision": [
        "Application ID under Enhancement 5.2",
        "Enhancement 5.2",
        "Revision Application ID",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
GNA/ST II Application ID:
  10-digit IDs starting with 12/22/11. Extract from any column with ST-II / GNA prefix.

LTA Application ID:
  IDs prefixed with 04/41, or preceded by "LTA:" keyword.

Application ID under Enhancement 5.2 or revision:
  Use ONLY when table/row context mentions Enhancement 5.2 / regulation 5.2 / revision.
  CRITICAL: if the page/section title contains "Applications under 5.2 received",
  every numeric ID from "Application No. & Date" / "Application ID" column
  belongs here, NOT in "GNA/ST II Application ID"."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"GNA/ST II Application ID": "1200003683"',
    '"LTA Application ID": "0412100008"',
    '"Application ID under Enhancement 5.2 or revision": null',
]
