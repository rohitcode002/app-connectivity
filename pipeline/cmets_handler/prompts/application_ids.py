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
  Extract the ID preceded by "St-II:" / "ST-II:" / "GNA:" when that keyword is
  present. The number immediately after that keyword is the GNA/ST-II ID.
  CRITICAL: extract only ONE single ID per row — the primary/first one seen.
  Do NOT put multiple comma-separated IDs in this field.
  If a cell contains multiple IDs, pick the FIRST GNA/ST-II ID.
  Example: "St-II:1200001603" → "1200001603"
  Example: "St-II: 1200002847(400MW)" → "1200002847"
  If no St-II/GNA keyword is present, use the ID from "Application No. & Date"
  as the default GNA/ST-II ID, except when that ID is explicitly routed to
  "Application ID under Enhancement 5.2 or revision" by the 5.2 rules below.
  Keep the numeric ID EXACTLY as printed (preserve leading zeros).

LTA Application ID:
  Extract IDs associated with the "LTA:" keyword. The number after "LTA:" is an
  LTA ID. If the same LTA cell continues with more 6+ digit numbers before the
  next label/column, extract those numbers too.
  CRITICAL: Any application ID starting with "04" is LTA even if the "LTA:"
  label is not printed.
  Extract ALL LTA IDs found in the row — if there are multiple, return them
  comma-separated.
  Example: "LTA: 0412100007(200MW), 0412100020(200MW)" → "0412100007, 0412100020"
  Example: "LTA: 1200001669 (300MW)" → "1200001669"
  Often found in "App. No. & Conn. Quantum (MW) of already granted Connectivity"
  or "App. No. & Quantum (MW)" columns — extract only the application numbers
  (e.g. from "0412100008(100 MW)" extract "0412100008"), NOT the MW values.
  CRITICAL: When a cell has BOTH "St-II:" and "LTA:" prefixed IDs in the same
  multi-line cell, split them — St-II IDs go to GNA/ST II, LTA IDs go here.

Application ID under Enhancement 5.2 or revision:
  Extract only ONE single ID.
  Fill this column when the page/section mentions wording such as
  "Applications under 5.2 received", "under regulation 5.2", Enhancement, or
  revision.
  CRITICAL: "Applications under 5.2 received" is context, not an automatic
  column swap. In existing-connectivity grantee tables, the ID from
  "Application No. & Date" remains the default GNA/ST-II ID unless the existing
  connectivity cell explicitly contains a "St-II:" or "GNA:" labelled ID.
  If the existing-connectivity cell has a bare non-LTA numeric ID, put that
  bare existing-connectivity ID here.
  If the 5.2 wording says the existing connectivity applications are "under
  process", the existing connectivity application number belongs here, while the
  ID from "Application No. & Date" remains the default GNA/ST-II ID.
  If a row has "Application No. & Date" plus "Existing Connectivity App. No. &
  Quantum" and the existing-connectivity cell has a bare numeric ID that does
  NOT start with "04", put the "Application No. & Date" ID in GNA/ST-II, put
  the bare existing-connectivity ID here, and leave LTA empty. If that bare ID
  starts with "04", put it in LTA instead.
  Example: Application No. "2200002621 (25-11-2025)" +
  Existing Connectivity "0212100033(300MW)" →
  GNA/ST-II = "2200002621", Enhancement 5.2 = "0212100033".
  Example: Existing Connectivity "St-II: 1200002847(400MW)" with Application
  No. "2200002563 (06-11-2025)" → GNA/ST-II = "1200002847",
  Enhancement 5.2 = "2200002563".
  Example: "2200002127 (05.06.2025)" → Enhancement 5.2 = "2200002127"
  Special non-5.2 case: when there is no 5.2 wording but the row has three
  application IDs (Application No. & Date + St-II + LTA), put the
  "Application No. & Date" ID here, put the St-II number in GNA/ST-II, and put
  the LTA number(s) in LTA.
  Keep the numeric ID EXACTLY as printed (preserve leading zeros).

CRITICAL — NO DUPLICATE IDs:
  The SAME application ID must NEVER appear in more than one column.
  If an ID appears in GNA/ST II Application ID, do NOT repeat it in
  LTA Application ID or Enhancement 5.2. Each column must hold a UNIQUE ID.

FORMAT RULE:
  Return all IDs as strings. Never convert them to numbers.
  Preserve leading zeros exactly as they appear in the PDF."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = [
    '"GNA/ST II Application ID": "1200003683"',
    '"LTA Application ID": "0412100008, 0412100020"',
    '"Application ID under Enhancement 5.2 or revision": null',
]
