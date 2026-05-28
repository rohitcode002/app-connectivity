"""
Quantum (MW) — prompt section
================================
Application Quantum (applied MW capacity).
Granted Quantum is NOT extracted — it is calculated in post-processing
from Application Quantum + Status.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Application Quantum (MW)(ST II)"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Installed Capacity (MW)",
    "Connectivity Quantum (MW)",
    "Application Quantum (MW)(ST II)",
    "Capacity (MW)",
    "Existing connectivity App. no. & Quantum",
    "App. No. & Conn. Quantum (MW) of already granted Connectivity",
    "App. No. & Conn. Quantum (MW)",
    "Planned additional capacity (MW)",
    "App. No. & Quantum (MW)",
    "Additional Generation Capacity",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Application Quantum: the MW capacity APPLIED for.
(Granted Quantum is computed in post-processing — do NOT extract it.)

IMPORTANT — Extracting MW from combined App No. & Quantum columns:
Some tables have columns like "App. No. & Conn. Quantum (MW) of already granted
Connectivity" or "Existing connectivity App. no. & Quantum" where the cell
contains BOTH an application number AND a MW value together, for example:
  "0412100008(100 MW)" or "0412100010 (150 MW)" or "1200003502 (250 MW)"
In such cases, extract ONLY the MW numeric value (e.g. "100", "150", "250")
as the Application Quantum, NOT the full application number.

IMPORTANT — Multiple App IDs with MW values:
When a row has MULTIPLE application IDs with MW values across columns
(e.g. "St-II: 1200002847(400MW)" in one column and "LTA: 0412100007(200MW),
0412100020(200MW)" in another), the Application Quantum should be the TOTAL
of ALL MW values: 400 + 200 + 200 = 800.
Post-processing will handle this summation, so extract the MW from whichever
column you see it in — preferably from the "App. No. & Quantum" column.

Also check for "Planned additional capacity (MW)" or "Additional Generation
Capacity" columns — these contain the additional capacity being applied for
(e.g. "16 (BESS)", "28.75 (BESS)", "590 MW BESS", "52 MW (Solar)").
Extract the numeric MW value from these as well."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Application Quantum (MW)(ST II)": "300"'
