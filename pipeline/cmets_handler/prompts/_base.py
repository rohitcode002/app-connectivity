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

PRIMARY KEY RULE (CRITICAL):
  A row MUST have at least ONE of these three IDs to be valid:
    • "GNA/ST II Application ID"
    • "LTA Application ID"
    • "Application ID under Enhancement 5.2 or revision"
  If a row has NONE of these three IDs, DO NOT output it.
  If one table row/cell contains multiple GNA/ST-II IDs, keep them together in
  "GNA/ST II Application ID" as a comma-separated string. Do NOT split one
  applicant/table row into multiple rows only because multiple GNA IDs are shown.

5.2 PAGE ROUTING RULE (CRITICAL):
  If the FULL PAGE TEXT contains the heading/phrase "Applications under 5.2 received",
  treat the whole page as a 5.2 Enhancement page. For every extracted row on that
  page, put all numeric application numbers from "Application No. & Date" or
  "Application ID" into "Application ID under Enhancement 5.2 or revision" and
  leave "GNA/ST II Application ID" null. If that exact 5.2 received phrase is
  absent, extract all normal GNA/ST-II application numbers into "GNA/ST II Application ID",
  comma-separated when multiple IDs appear in the same row/cell.

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
  Treat revoked/cancelled/rejected as Revoked; treat missing status, pending/submitted/under process as Applied.
- PSP values: fill only when pump storage / PSP wording is explicitly present.
  Detect PSP from "Nature of Applicant" (e.g. "Pumped Storage") or "Type" fields.
  Look for "Max Injection" and "Max Drawl" / "Drawal" values in PSP-context rows.
  IMPORTANT: if PSP Injection/Drawl are populated, set Battery Injection (MW) to null.
- Battery values: fill only when BESS / Battery wording is explicitly present.
  If a duration is found in the text (e.g. "4 hours", "four (4) hours") and
  Battery MWh is NOT explicitly stated, compute: Battery MWh = Battery Injection (MW) × duration_hours.
- "type" MUST be strictly one of these keywords: Solar, BESS, Wind, Solar+Wind, Solar+BESS
  with associated MW values in parentheses if present.
  Examples: "Solar (300)", "Wind (12) + BESS (19)", "Solar+Wind (500)", "BESS (50)", or null.
  Do NOT include any other words, sentences, or descriptions in the type field."""


PAGE_EMPTY_FOOTER = """\
If the page contains NO extractable data rows: {{"rows": []}}"""
