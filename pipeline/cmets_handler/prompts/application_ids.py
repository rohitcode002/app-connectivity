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
        "App. No. & Quantum (MW)",
    ],
    "LTA Application ID": [
        "App. No. & Conn. Quantum (MW) of already granted Connectivity",
        "App. No. & Quantum (MW)",
        "Existing connectivity App. no. & Quantum",
        "LTA Application ID",
        "LTA App ID",
    ],
    "Application ID under Enhancement 5.2 or revision": [
        "Application ID under Enhancement 5.2",
        "Existing Connectivity application No. & Date",
        "Enhancement 5.2",
        "Revision Application ID",
    ],
}

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
GNA/ST II Application ID:
  10-digit IDs starting with 12/11, or preceded by "St-II:" / "GNA:" prefix.
  CRITICAL: extract only ONE single ID per row — the primary/first one seen.
  Do NOT put multiple comma-separated IDs in this field.
  If a cell contains multiple IDs, pick the FIRST GNA/ST-II ID.
  Example: "St-II:1200001603" → "1200001603"
  Example: "St-II: 1200002847(400MW)" → "1200002847"

LTA Application ID:
  IDs prefixed with 04/41, or preceded by "LTA:" keyword.
  Extract ALL LTA IDs found — if there are multiple, return them comma-separated.
  Example: "LTA: 0412100007(200MW), 0412100020(200MW)" → "0412100007, 0412100020"
  Example: "LTA: 1200001669 (300MW)" → "1200001669"
  Often found in "App. No. & Conn. Quantum (MW) of already granted Connectivity"
  or "App. No. & Quantum (MW)" columns — extract only the application numbers
  (e.g. from "0412100008(100 MW)" extract "0412100008"), NOT the MW values.
  CRITICAL: When a cell has BOTH "St-II:" and "LTA:" prefixed IDs in the same
  multi-line cell, split them — St-II IDs go to GNA/ST II, LTA IDs go here.

Application ID under Enhancement 5.2 or revision:
  Use ONLY when table/row context mentions Enhancement 5.2 / regulation 5.2 / revision.
  This includes section headings like "Applications under 5.2 received" AND
  description text like "under regulation 5.2 of GNA Regulations".
  Extract only ONE single ID — usually the 22-prefix ID from "Application No. & Date"
  or "Existing Connectivity application No. & Date" column.
  CRITICAL: if the page/section title contains "Applications under 5.2 received"
  or the description mentions "under regulation 5.2", the 22-prefix ID from
  "Application No. & Date" column belongs here, NOT in "GNA/ST II Application ID".
  Example: "2200002563 (06-11-2025)" → Enhancement 5.2 = "2200002563"
  Example: "2200002637 (02-12-2025)" → Enhancement 5.2 = "2200002637"
  Example: "2200002127 (05.06.2025)" → Enhancement 5.2 = "2200002127"

CRITICAL — NO DUPLICATE IDs:
  The SAME application ID must NEVER appear in more than one column.
  If an ID appears in GNA/ST II Application ID, do NOT repeat it in
  LTA Application ID or Enhancement 5.2. Each column must hold a UNIQUE ID."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"GNA/ST II Application ID": "1200003683"',
    '"LTA Application ID": "0412100008, 0412100020"',
    '"Application ID under Enhancement 5.2 or revision": null',
]

