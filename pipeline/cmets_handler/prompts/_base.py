"""
_base.py — Shared prompt scaffolding (role, general rules)
=============================================================
These fragments are NOT column-specific.  They frame the overall
extraction task and are assembled by __init__.py.
"""

ROLE_INTRO = """\
You are a precise data extraction assistant specialising in Indian energy/power connectivity applications.

You will receive the FULL TEXT of a single PDF page that has passed a column-header filter.
Your task: scan the ENTIRE page and extract EVERY data row you can find.

Output keys for each row:"""


EXTRACTION_RULES = """\
═══════════════════════════════════════════════════════
EXTRACTION RULES (CRITICAL)
═══════════════════════════════════════════════════════
- Extract EVERY visible data row on the page — a page often contains multiple rows.
- Each table row = one object in the "rows" array.
- Use null if a value is not available in a particular row.
- Keep values as strings exactly as seen in the text.
- Ignore headers, footnotes, and purely explanatory paragraphs.
- "Name of the developers" must be the company/applicant name, NOT criterion values like "SECI LOA".
- "Nature of Applicant" is REQUIRED for every row. If the table uses merged cells
  or visual continuation, repeat the visible Nature of Applicant value for each
  row in that block.

PRIMARY KEY RULE (CRITICAL):
  A row MUST have at least ONE of these three IDs to be valid:
    • "GNA/ST II Application ID"
    • "LTA Application ID"
    • "Application ID under Enhancement 5.2 or revision"
  If a row has NONE of these three IDs, DO NOT output it.
  "GNA/ST II Application ID" and "Application ID under Enhancement 5.2 or revision"
  must each hold at most ONE single numeric ID. "LTA Application ID" may hold
  multiple comma-separated IDs when multiple LTA numbers are printed in the row.

5.2 PAGE ROUTING RULE (CRITICAL):
  If the FULL PAGE TEXT contains the heading/phrase "Applications under 5.2 received",
  treat the whole page as a 5.2 Enhancement page. For existing-connectivity
  grantee tables, put the ID from "Application No. & Date" into
  "Application ID under Enhancement 5.2 or revision"; put any "St-II:" ID from
  "Existing Connectivity App. No. & Quantum" into "GNA/ST II Application ID";
  put every "LTA:" ID from that existing-connectivity column into
  "LTA Application ID". If the 5.2 section says the existing connectivity
  applications are "under process", use the ID from "Application No. & Date" as
  "GNA/ST II Application ID" and the existing connectivity application number as
  "Application ID under Enhancement 5.2 or revision".

SKIP RULES — DO NOT extract rows if:
  • "Nature of Applicant" is "Bulk consumer" or "Drawee entity" or
    "Drawee entity connected" — these are NOT generator applications.
  • The table contains GNARE columns like "GNARE within Region (MW)",
    "GNARE outside Region (MW)", "Total GNARE Required (MW)",
    "Start date of GNARE", "End date of GNARE".
    If you detect ANY GNARE column, return {{"rows": []}}.
- For "GNA Operationalization Date" look near SCoD/SCOD terms.
- For "GNA Operationalization (Yes/No)" return null (computed in post-processing).
- If multiple dates appear for one date field, post-processing keeps the latest parsed date.
- For "Status of application..." map wording to only: Withdrawn / granted / Revoked / Applied.
  CRITICAL: The status is usually NOT in a table column — it is in the DESCRIPTION/NARRATIVE
  paragraph that accompanies each table row (below the table in PDF, may appear beside/left in text).
  You MUST read the description text for keywords: "granted" → granted, "withdrawn" → Withdrawn,
  "revoked"/"cancelled"/"rejected" → Revoked. Only use "Applied" if none of these keywords appear.
  Treat revoked/cancelled/rejected as Revoked; treat missing status, pending/submitted/under process as Applied.
- PSP values: fill only when pump storage / PSP wording is explicitly present.
  Detect PSP from "Nature of Applicant" (e.g. "Pumped Storage") or "Type" fields.
  Look for "Max Injection" and "Max Drawl" / "Drawal" values in PSP-context rows.
  IMPORTANT: if PSP Injection/Drawl are populated, set Battery Injection (MW) to null.
- Battery values: fill only when BESS / Battery wording is explicitly present.
  If a duration is found in the text (e.g. "4 hours", "four (4) hours", "BESS 4 Hr",
  "BESS 2 Hr") and Battery MWh is NOT explicitly stated, compute:
  Battery MWh = Battery Injection (MW) × duration_hours.
  IMPORTANT: If BESS is present but NO duration is mentioned and MWh is NOT
  explicitly stated, set Battery MWh to null, NOT 0.
  Only set Battery MWh to a computed value when duration is available.
- "type" MUST use only these component labels: Solar, Wind, Hydro, BESS, PSP
  with associated MW values in parentheses if present.
  For BESS only, preserve the associated hour duration inside the BESS
  parentheses when present, e.g. "BESS (300, 4hr)".
  Examples: "Solar (300)", "Wind (12) + BESS (19)", "Solar (250) + Wind (250)", "BESS (50, 4hr)", or null.
  Do NOT include any other words, sentences, or descriptions in the type field."""


PAGE_EMPTY_FOOTER = """\
If the page contains NO extractable data rows: {{"rows": []}}"""
