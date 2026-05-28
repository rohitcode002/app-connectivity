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
    "App. No. & Quantum (MW)",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Application Quantum: the MW capacity APPLIED for — this is the EXISTING
connectivity quantum, NOT the additional/planned capacity.
(Granted Quantum is computed in post-processing — do NOT extract it.)

CRITICAL — Where to find Application Quantum:
Application Quantum comes from the MW values embedded inside the
"App. No. & Quantum (MW)" or "App. No. & Conn. Quantum (MW) of already
granted Connectivity" columns. These cells contain BOTH an application
number AND a MW value together, for example:
  "0412100008(100 MW)" → Application Quantum = 100
  "0412100010 (150 MW)" → Application Quantum = 150
  "1200003502 (250 MW)" → Application Quantum = 250
  "0212100033(300MW)" → Application Quantum = 300
In such cases, extract ONLY the MW numeric value.

CRITICAL — Multiple App IDs with MW values:
When a row has MULTIPLE application IDs with MW values across columns
(e.g. "St-II: 1200002847(400MW)" in one column and "LTA: 0412100007(200MW),
0412100020(200MW)" in another), the Application Quantum should be the TOTAL
of ALL MW values: 400 + 200 + 200 = 800.
Post-processing will handle this summation.

CRITICAL — "Additional Generation Capacity" and "Planned additional capacity"
columns do NOT map to Application Quantum. These columns contain the
additional capacity breakdown (Solar/BESS/Wind MW) which maps to other
CMETS columns like Battery Injection, Installed/Break-up Capacity Solar, etc.
Do NOT use values from these columns for Application Quantum.

Examples:
  Row: "0212100033(300MW) | 52 MW (Solar)" → Application Quantum = 300
  Row: "0412100008 (100MW) | 6.88 MW (BESS)" → Application Quantum = 100
  Row: "1200001603 | LTA: 1200001669 (300MW) | 300 MW (BESS 4 Hr)"
       → Application Quantum = 300 (from LTA column, NOT from BESS)"""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Application Quantum (MW)(ST II)": "300"'
