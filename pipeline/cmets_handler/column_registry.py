"""
cmets_handler/column_registry.py — Single Source of Truth for ALL CMETS Columns
==================================================================================
Every column in the final CMETS output is defined HERE, in one place.

For each column you can see:
  • data_source  : where the data comes from
                   "extraction"   → extracted from PDF page text via LLM
                   "derived"      → computed from another extracted column
                   "calculated"   → calculated from extraction + logic
                   "meeting_meta" → injected from meeting classifier (same for all rows in a PDF)
                   "internal"     → pipeline bookkeeping (PDF path, page number)
  • llm_key      : the key name the LLM should return (None if not LLM-extracted)
  • aliases      : list of header-text variants the PDF might use for this column
  • norm_func    : name of the normalisation function applied post-extraction
  • description  : human-readable explanation

To change extraction logic for ANY column → edit its entry here and its
corresponding norm function in normalization.py.

To add a new column → add an entry here, add a field to MappedRow in models.py,
update the LLM prompt in prompts.py (if extraction-based), and add a norm
function in normalization.py (if needed).

COLUMN ORDER
------------
The CMETS_COLUMNS list below defines the column order for the raw
extraction Excel (01_cmets_extracted.xlsx).  The order matches the
user-specified schema exactly.  No formatting is applied to this
Excel — it holds exact extracted data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable
from collections import OrderedDict


@dataclass
class ColumnDef:
    """Definition of a single CMETS output column."""
    name: str                                    # Final column name in Excel/JSON
    data_source: str                             # "extraction" | "derived" | "calculated" | "meeting_meta" | "internal"
    llm_key: Optional[str] = None                # Key name in LLM JSON response
    aliases: list[str] = field(default_factory=list)   # PDF header variants
    norm_func: Optional[str] = None              # normalization function name (in normalization.py)
    description: str = ""                        # Human-readable description
    derived_from: Optional[str] = None           # Source column if derived/calculated
    default: Optional[str] = None                # Default value if not found


# ═══════════════════════════════════════════════════════════════════════════════
# COLUMN DEFINITIONS — ordered as they appear in 01_cmets_extracted.xlsx
# ═══════════════════════════════════════════════════════════════════════════════
#
# The order below exactly matches the user-specified schema for the raw
# extraction Excel.  Internal bookkeeping columns (PDF, Page Number) are
# appended at the end so they exist in the data but don't disrupt the
# user-facing column order.

COLUMN_DEFS: list[ColumnDef] = [

    # ── 1. Region (derived from PDF path at flatten time) ────────────────
    ColumnDef(
        name="Region",
        data_source="derived",
        derived_from="PDF path",
        description="Region code (NR, SR, WR, ER, NER) derived from PDF folder structure",
    ),

    # ── 2. State ─────────────────────────────────────────────────────────
    ColumnDef(
        name="State",
        data_source="derived",
        derived_from="Project Location",
        norm_func="extract_state",
        description="Indian state / UT derived from Project Location text",
    ),

    # ── 3. Substation ────────────────────────────────────────────────────
    ColumnDef(
        name="Substation",
        data_source="extraction",
        llm_key="substaion",
        aliases=[
            "Connectivity Location (As per Application)",
            "Nearest Pooling Station (As per Application)",
            "Connectivity Granted at",
            "Location requested for Grant of Stage-II Connectivity",
            "Connectivity Injection Point",
            "Sub-station", "Substation",
        ],
        norm_func="norm_substation",
        description="Substation / connectivity location name (e.g. Aligarh (PG), Bhadla-V)",
    ),

    # ── 4. Coordinates (placeholder — populated downstream) ──────────────
    ColumnDef(
        name="Coordinates",
        data_source="internal",
        description="Substation coordinates — populated in downstream mapping, empty at extraction",
    ),

    # ── 5. Project Location ──────────────────────────────────────────────
    ColumnDef(
        name="Project Location",
        data_source="extraction",
        llm_key="Project Location",
        aliases=["Project Location"],
        norm_func="clean",
        description="Project location as stated in the application",
    ),

    # ── 6. Name of Developers ────────────────────────────────────────────
    ColumnDef(
        name="Name of Developers",
        data_source="extraction",
        llm_key="Name of the developers",
        aliases=["Applicant", "Name of Applicant", "Developer", "Name of Developers"],
        norm_func="norm_dev",
        description="Name of the developer / applicant company",
    ),

    # ── 7. GNA/ST II Application ID ──────────────────────────────────────
    ColumnDef(
        name="GNA/ST II Application ID",
        data_source="extraction",
        llm_key="GNA/ST II Application ID",
        aliases=[
            "Application No. & Date",
            "Application ID",
            "GNA Application ID",
            "ST-II Application ID",
            "GNA/ST II Application ID",
        ],
        norm_func="norm_num_ids",
        description="GNA or Stage-II application IDs (10-digit, starts with 12/22/11), comma-separated if multiple",
    ),

    # ── 8. LTA Application ID ───────────────────────────────────────────
    ColumnDef(
        name="LTA Application ID",
        data_source="extraction",
        llm_key="LTA Application ID",
        aliases=[
            "App. No. & Conn. Quantum (MW) of already granted Connectivity",
            "LTA Application ID",
            "LTA App ID",
        ],
        norm_func="norm_num_ids_strip",
        description="LTA application ID (prefixed with 04/41)",
    ),

    # ── 9. Application ID under Enhancement 5.2 or revision ─────────────
    ColumnDef(
        name="Application ID under Enhancement 5.2 or revision",
        data_source="calculated",
        llm_key="Application ID under Enhancement 5.2 or revision",
        aliases=[
            "Application ID under Enhancement 5.2",
            "Enhancement 5.2",
            "Revision Application ID",
        ],
        norm_func="derive_enhancement_id",
        description="Derived: only when context mentions Enhancement 5.2 / regulation 5.2 / revision",
        derived_from="GNA/ST II Application ID, LTA Application ID, Mode(Criteria for applying)",
    ),

    # ── 10. CMETS GNA Approved ───────────────────────────────────────────
    ColumnDef(
        name="CMETS GNA Approved",
        data_source="meeting_meta",
        description="Meeting number if PDF is GNA-classified",
        norm_func=None,
    ),

    # ── 11. CMETS LTA Approved ───────────────────────────────────────────
    ColumnDef(
        name="CMETS LTA Approved",
        data_source="meeting_meta",
        description="Meeting number if PDF is LTA-classified",
    ),

    # ── 12. CMETS GNA Meeting Date ───────────────────────────────────────
    ColumnDef(
        name="CMETS GNA Meeting Date",
        data_source="meeting_meta",
        description="Meeting date (dd.mm.yyyy) if PDF is GNA-classified",
    ),

    # ── 13. CMETS LTA Meeting Date ───────────────────────────────────────
    ColumnDef(
        name="CMETS LTA Meeting Date",
        data_source="meeting_meta",
        description="Meeting date (dd.mm.yyyy) if PDF is LTA-classified",
    ),

    # ── 14. Type (RAW — no formatting applied at extraction) ─────────────
    ColumnDef(
        name="Type",
        data_source="derived",
        llm_key="type",
        derived_from="type",
        norm_func=None,      # Deliberately None — keep raw LLM value
        description="Energy source type — kept as raw extracted text (e.g. 'Solar (24) +BESS (45)')",
    ),

    # ── 15. Application Quantum (MW)(ST II) ──────────────────────────────
    ColumnDef(
        name="Application Quantum (MW)(ST II)",
        data_source="extraction",
        llm_key="Application Quantum (MW)(ST II)",
        aliases=[
            "Installed Capacity (MW)",
            "Connectivity Quantum (MW)",
            "Application Quantum (MW)(ST II)",
            "Capacity (MW)",
            "Existing connectivity App. no. & Quantum",
            "App. No. & Conn. Quantum (MW) of already granted Connectivity",
            "App. No. & Conn. Quantum (MW)",
            "Planned additional capacity (MW)",
        ],
        norm_func="clean",
        description="Applied connectivity quantum in MW",
    ),

    # ── 16. Granted Quantum GNA/LTA(MW) ──────────────────────────────────
    ColumnDef(
        name="Granted Quantum GNA/LTA(MW)",
        data_source="calculated",
        derived_from="Status of application(Withdrawn / granted. Revoked.), Application Quantum (MW)(ST II)",
        norm_func="clean",
        description="Calculated: equals Application Quantum (MW) when status is 'granted', otherwise null",
    ),

    # ── 17–20. Installed/Break-up Capacity (MW) sub-columns ──────────────
    ColumnDef(
        name="Installed/Break-up Capacity (MW) Solar",
        data_source="internal",
        description="Solar installed capacity — populated in formatter, empty at extraction",
    ),
    ColumnDef(
        name="Installed/Break-up Capacity (MW) Wind",
        data_source="internal",
        description="Wind installed capacity — populated in formatter, empty at extraction",
    ),
    ColumnDef(
        name="Installed/Break-up Capacity (MW) Hybrid",
        data_source="internal",
        description="Hybrid installed capacity — populated in formatter, empty at extraction",
    ),
    ColumnDef(
        name="Installed/Break-up Capacity (MW) Hydro",
        data_source="internal",
        description="Hydro installed capacity — populated in formatter, empty at extraction",
    ),

    # ── 21–23. Battery (BESS) columns ────────────────────────────────────
    ColumnDef(
        name="Battery MWh",
        data_source="extraction",
        llm_key="Battery MWh",
        aliases=[
            "Battery MWh",
            "BESS MWh",
            "Battery (MWh)",
            "Battery Energy Storage (MWh)",
        ],
        norm_func="norm_battery_mwh",
        description="Battery energy storage capacity in MWh (from BESS rows)",
    ),
    ColumnDef(
        name="Battery Injection (MW)",
        data_source="extraction",
        llm_key="Battery Injection (MW)",
        aliases=[
            "Battery Injection (MW)",
            "BESS Injection (MW)",
            "Injection (MW)",
        ],
        norm_func="norm_battery_injection",
        description="Battery injection capacity in MW. With BESS, generally smaller than drawl.",
    ),
    ColumnDef(
        name="Battery Drawl (MW)",
        data_source="extraction",
        llm_key="Battery Drawl (MW)",
        aliases=[
            "Battery Drawl (MW)",
            "BESS Drawl (MW)",
            "Drawl (MW)",
            "Drawal (MW)",
        ],
        norm_func="norm_battery_drawl",
        description="Battery drawl capacity in MW. With BESS, generally larger than injection.",
    ),

    # ── 24–26. PSP (Pump Storage) columns ────────────────────────────────
    ColumnDef(
        name="PSP MWh",
        data_source="extraction",
        llm_key="PSP MWh",
        aliases=["PSP MWh", "Pump Storage MWh"],
        norm_func="norm_psp_mwh",
        description="Pump storage project capacity in MWh",
    ),
    ColumnDef(
        name="PSP Injection (MW)",
        data_source="extraction",
        llm_key="PSP Injection (MW)",
        aliases=["PSP Injection (MW)", "Pump Storage Injection (MW)"],
        norm_func="norm_psp_injection",
        description="Pump storage injection capacity in MW",
    ),
    ColumnDef(
        name="PSP Drawl (MW)",
        data_source="extraction",
        llm_key="PSP Drawl (MW)",
        aliases=["PSP Drawl (MW)", "Pump Storage Drawl (MW)", "PSP Drawal (MW)"],
        norm_func="norm_psp_drawl",
        description="Pump storage drawl capacity in MW",
    ),

    # ── 27–28. Commissioned [TGNA, GNA] (placeholder — from JCC) ────────
    ColumnDef(
        name="Commissioned TGNA",
        data_source="internal",
        description="TGNA value — populated from JCC mapping downstream, empty at extraction",
    ),
    ColumnDef(
        name="Commissioned GNA",
        data_source="internal",
        description="GNA value — populated from JCC mapping downstream, empty at extraction",
    ),

    # ── 29. Application/Submission Date ──────────────────────────────────
    ColumnDef(
        name="Application/Submission Date",
        data_source="extraction",
        llm_key="Application/Submission Date",
        aliases=[
            "Application No. & Date",
            "Submission Date",
            "Application Date",
            "Application/Submission Date",
        ],
        norm_func="extract_date",
        description="Application or submission date",
    ),

    # ── 30. Mode(Criteria for applying) ──────────────────────────────────
    ColumnDef(
        name="Mode(Criteria for applying)",
        data_source="extraction",
        llm_key="Mode(Criteria for applying)",
        aliases=[
            "Criterion for applying",
            "Criteria for applying",
            "Mode",
            "Mode(Criteria for applying)",
        ],
        norm_func="norm_mode_criteria",
        description="Mode or criteria for applying, normalised to LOA or PPA / Land BG",
    ),

    # ── 31. Applied Start of Connectivity ────────────────────────────────
    ColumnDef(
        name="Applied Start of Connectivity sought by developer date"
             "( start date of connectivity as per the application)",
        data_source="extraction",
        llm_key="Applied Start of Connectivity sought by developer date",
        aliases=[
            "Start Date of Connectivity (As per Application)",
            "Applied Start of Connectivity sought by developer date",
            "Start Date of Connectivity",
        ],
        norm_func="extract_date",
        description="Start date of connectivity as per the application",
    ),

    # ── 32. GNA Operationalization Date ──────────────────────────────────
    ColumnDef(
        name="GNA Operationalization Date",
        data_source="extraction",
        llm_key="GNA Operationalization Date",
        aliases=["GNA Operationalization Date", "SCoD", "SCOD"],
        norm_func="extract_date",
        description="GNA operationalization date (near SCoD/SCOD in text)",
    ),

    # ── 33. GNA Operationalization (Yes/No) ──────────────────────────────
    ColumnDef(
        name="GNA Operationalization (Yes/No)",
        data_source="calculated",
        derived_from="GNA Operationalization Date",
        norm_func="gna_yes_no",
        description="Yes if GNA Operationalization Date is today/past, No if it is future",
    ),

    # ── 34. Date from which additional capacity is to be added ───────────
    ColumnDef(
        name="Date from which additional capacity is to be added",
        data_source="extraction",
        llm_key="Date from which additional capacity is to be added",
        aliases=[
            "Date from which additional capacity is to be added",
            "Additional Capacity Date",
        ],
        norm_func="extract_date",
        description="Date from which additional capacity is to be added",
    ),

    # ── 35. Nature of Applicant ──────────────────────────────────────────
    ColumnDef(
        name="Nature of Applicant",
        data_source="extraction",
        llm_key="Nature of Applicant",
        aliases=["Nature of Applicant"],
        norm_func="clean",
        description="Nature of applicant (Generator, Bulk consumer, etc.)",
    ),

    # ── 36. Status of application ────────────────────────────────────────
    ColumnDef(
        name="Status of application(Withdrawn / granted. Revoked.)",
        data_source="extraction",
        llm_key="Status of application(Withdrawn / granted. Revoked.)",
        aliases=[
            "Status of Application",
            "Status",
            "Withdrawn / granted / Revoked",
        ],
        norm_func="norm_status",
        description="Application status: Withdrawn, Granted, Applied",
    ),

    # ── 37. Voltage level ────────────────────────────────────────────────
    ColumnDef(
        name="Voltage level",
        data_source="extraction",
        llm_key="Voltage",
        aliases=["Voltage", "Voltage Level", "kV Level"],
        norm_func="norm_voltage",
        description="Voltage level of the substation/connectivity point (e.g. 400 kV, 220 kV)",
    ),

    # ── 38. Bay No (placeholder — populated downstream) ──────────────────
    ColumnDef(
        name="Bay No",
        data_source="internal",
        description="Bay number — populated in downstream mapping, empty at extraction",
    ),

    # ── Internal bookkeeping columns (at the end) ────────────────────────
    ColumnDef(
        name="PDF",
        data_source="internal",
        description="Source PDF file path",
    ),
    ColumnDef(
        name="Page Number",
        data_source="internal",
        description="Page number within the PDF",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience lookups
# ═══════════════════════════════════════════════════════════════════════════════

# Ordered dict: column_name → ColumnDef
COLUMN_REGISTRY: OrderedDict[str, ColumnDef] = OrderedDict(
    (c.name, c) for c in COLUMN_DEFS
)

# Final column order for Excel output
CMETS_COLUMNS: list[str] = [c.name for c in COLUMN_DEFS]

# Only columns that the LLM extracts (have llm_key)
LLM_EXTRACTION_KEYS: list[str] = [
    c.llm_key for c in COLUMN_DEFS if c.llm_key is not None
]

# Meeting-level columns
MEETING_COLUMNS: list[str] = [
    c.name for c in COLUMN_DEFS if c.data_source == "meeting_meta"
]

# Extraction columns (LLM-extracted from page text)
EXTRACTION_COLUMNS: list[str] = [
    c.name for c in COLUMN_DEFS if c.data_source == "extraction"
]

# Derived columns
DERIVED_COLUMNS: list[str] = [
    c.name for c in COLUMN_DEFS if c.data_source == "derived"
]

# Calculated columns
CALCULATED_COLUMNS: list[str] = [
    c.name for c in COLUMN_DEFS if c.data_source == "calculated"
]


def get_column_def(name: str) -> Optional[ColumnDef]:
    """Look up a column definition by name."""
    return COLUMN_REGISTRY.get(name)


def get_llm_key_to_column_map() -> dict[str, str]:
    """Return mapping from LLM JSON key → final column name."""
    return {
        c.llm_key: c.name
        for c in COLUMN_DEFS
        if c.llm_key is not None
    }
