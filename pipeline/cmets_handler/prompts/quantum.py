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
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Application Quantum: the MW capacity APPLIED for.
(Granted Quantum is computed in post-processing — do NOT extract it.)"""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Application Quantum (MW)(ST II)": "300"'

