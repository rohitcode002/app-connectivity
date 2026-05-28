"""
Status of application — prompt section
=========================================
Withdrawn / granted / Revoked / Applied.
"""

# ── Output key(s) for the LLM JSON response ─────────────────────────────────
OUTPUT_KEY = "Status of application(Withdrawn / granted. Revoked.)"

# ── Header name variants the PDF might use ───────────────────────────────────
VARIANTS = [
    "Status of Application",
    "Status",
    "Withdrawn / granted / Revoked",
]

# ── Extraction rule ──────────────────────────────────────────────────────────
RULE = """\
Map the status wording to one of: Withdrawn, granted, Revoked, Applied.

IMPORTANT — Determining status from TABLE DESCRIPTION TEXT:
The status is often NOT in a table column. Instead, it is found in the
DESCRIPTION / NARRATIVE paragraph that accompanies each table row. In the
PDF this paragraph appears BELOW the table, but in the extracted text it
may appear to the LEFT or BESIDE the table data. You MUST read this
description text carefully for each row to determine its status.

Rules for inferring status from description text:
  • If the description mentions the word "granted" in the context of
    connectivity or capacity (e.g. "connectivity quantum earlier granted",
    "agreed to grant", "it was agreed to grant", "grant of connectivity",
    "granted connectivity"), then the status is "granted".
  • If the description mentions "withdrawn" or "withdrawal" in the context
    of the application (e.g. "application has been withdrawn",
    "requested for withdrawal", "withdrawal of connectivity"),
    then the status is "Withdrawn".
  • If the description mentions "revoked", "cancelled", or "rejected"
    in the context of the application (e.g. "connectivity has been revoked",
    "application stands revoked", "connectivity cancelled"),
    then the status is "Revoked".
  • If NONE of the above keywords appear in the description, or no
    description is present, or the status is pending/submitted/under
    process/applied, then the status is "Applied".

Use "granted" when the application is granted/approved — look in BOTH table columns AND description text.
Use "Withdrawn" when wording says withdrawn — look in BOTH table columns AND description text.
Use "Revoked" when wording says revoked/cancelled/rejected — look in BOTH table columns AND description text.
Use "Applied" ONLY when no status keyword (granted/withdrawn/revoked) is found anywhere on the page for that row."""

# ── JSON example fragment ────────────────────────────────────────────────────
JSON_EXAMPLE = '"Status of application(Withdrawn / granted. Revoked.)": "Applied"'
