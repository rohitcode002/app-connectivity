# App Connectivity — Pipeline Documentation

Complete reference for all extraction rules, operations, data sources, and conditions across the pipeline.

---

## Table of Contents

- [Pipeline Overview](#pipeline-overview)
- [Module 1 — CMETS Extraction](#module-1--cmets-extraction)
- [Module 2 — Effectiveness Extraction](#module-2--effectiveness-extraction)
- [Module 3 — Mapping & Merge Operations](#module-3--mapping--merge-operations)
- [Module 4 — JCC Extraction & Matching](#module-4--jcc-extraction--matching)
- [Module 5 — Bay Allocation Extraction](#module-5--bay-allocation-extraction)
- [Module 6 — Bay Mapping](#module-6--bay-mapping)
- [Cross-Module Features](#cross-module-features)

---

## Pipeline Overview

### Entry Scripts

- `main.py` — download + extract only the PDFs that are not yet marked as extracted.
- `downloader_main.py` — download only (no extraction).
- `extraction_main.py` — extract only pending PDFs (based on per-source tables).

```
┌───────────────┐   ┌─────────────────────┐   ┌────────────────┐   ┌─────────────────────┐
│  CMETS PDFs   │   │  Effectiveness PDFs │   │   JCC PDFs     │   │ Bay Allocation PDFs │
│  (Module 1)   │   │  (Module 2)         │   │   (Module 4)   │   │ (Module 5)          │
└──────┬────────┘   └─────────┬───────────┘   └───────┬────────┘   └──────────┬──────────┘
       │                      │                       │                       │
       ▼                      ▼                       │                       │
┌──────────────────────────────────┐                  │                       │
│  Module 3 — Mapping & Merge     │                  │                       │
│  • Merge CMETS + Effectiveness  │                  │                       │
│  • GNA Date Update              │                  │                       │
│  • Capacity Calculation         │◄─────────────────┘                       │
│  • Bay Mapping                  │◄─────────────────────────────────────────┘
└──────────────┬───────────────────┘
               ▼
        Final Enriched Output
```

### All Input/Output Paths

| Module | Input | Cache | Output Excel |
|---|---|---|---|
| CMETS | `source/cmets_pdfs/*.pdf` | `output/cmets_cache/` | `excels/cmets_extracted.xlsx` |
| Effectiveness | `source/effectiveness_pdfs/*.pdf` | `output/effectiveness_cache/` | `excels/effectiveness_extracted.xlsx` |
| JCC | `source/jcc_pdfs/<Region>/<Type>/*.pdf` | `output/jcc_cache/` | `excels/jcc_extracted.xlsx`, `jcc_output_layer.xlsx`, `cmets_jcc_mapped.xlsx` |
| Bay Allocation | `source/bayallocation/*.pdf` | `output/bayallocation_cache/` | `excels/bayallocation_extracted.xlsx` |

### ID Cascade Strategy (Used Everywhere)

Wherever a CMETS row needs to match an effectiveness record, this priority order is used:

1. **GNA Application ID** — try first
2. **LTA Application ID** — fallback
3. **5.2 GNA (Enhancement) ID** — last resort

First successful match wins.

### Date Rules (Global)

- **Parsing accepts**: `dd.mm.yyyy`, `yyyy-mm-dd`, `d Month yyyy`
- **Output format**: Always `dd.mm.yyyy` (Indian convention)

---

## Module 1 — CMETS Extraction

Reads **CMETS / GNI connectivity** PDF documents → extracts structured row-level data about renewable energy connectivity applications.

### Extraction Steps

1. **Page Gate** — page must match ≥3 known column headers (e.g. "Applicant", "Connectivity", "Application No.") or value fingerprints
2. **LLM Extraction** — qualifying pages sent to GPT-4o-mini → returns structured JSON rows
3. **Normalization** — every value cleaned and standardized
4. **Caching** — results saved as JSON; delete cache to re-extract

### Columns Extracted

| Column | What It Contains |
|---|---|
| **GNA/ST II Application ID** | 10-digit ID (starts with 12, 22, or 11) |
| **LTA Application ID** | ID prefixed with 04 or 41 |
| **Application ID under Enhancement 5.2** | Only when Enhancement 5.2 context is present |
| **Name of Developers** | Company/applicant name |
| **Substation** | Connectivity location (e.g. "Aligarh (PG)") |
| **Project Location** | Project location as stated |
| **State** | Derived from Project Location |
| **Type** | Strict keywords + MW values (see [Type Extraction Workflow](#type-extraction-workflow)) |
| **Application Quantum (MW)(ST II)** | Applied connectivity quantum |
| **Granted Quantum GNA/LTA(MW)** | Actually granted quantum |
| **Voltage level** | e.g. "400 kV", "220 kV" |
| **Battery MWh / Injection / Drawl** | Only when BESS/Battery context present |
| **PSP MWh / Injection / Drawl** | Only when pump storage context present |
| **Application/Submission Date** | Submission date |
| **Applied Start of Connectivity date** | Start date per application |
| **Date from which additional capacity is to be added** | If explicitly present |
| **GNA Operationalization Date** | Near SCoD/SCOD terms |
| **GNA Operationalization (Yes/No)** | Computed (see [GNA Rules](#gna-operationalization-date--yesno)) |
| **Nature of Applicant** | Generator, Bulk consumer, etc. |
| **Mode (Criteria for applying)** | e.g. "SECI LOA", "Land BG Route" |
| **Status of application** | Withdrawn / granted / Revoked |
| **CMETS GNA/LTA Approved** | Meeting number (per PDF) |
| **CMETS GNA/LTA Meeting Date** | Meeting date (per PDF) |

### Skip Rules

Rows are **never extracted** if:
- "Nature of Applicant" is "Bulk consumer", "Drawee entity", or "Drawee entity connected"
- Table contains GNARE columns (e.g. "GNARE within Region (MW)")
- Row has none of the three primary key IDs

### Primary Key Rule

A row **must** have at least one of: GNA/ST II Application ID, LTA Application ID, or Enhancement 5.2 ID. Rows with none are discarded.

---

## Module 2 — Effectiveness Extraction

Reads **RE Effectiveness / Connectivity Status** PDF reports → extracts current status of connectivity applications.

### Extraction Steps

1. **PDF Discovery** — recursive scan for `*.pdf`
2. **Cache Check** — skip if JSON exists
3. **LLM Extraction** — text batched into ~10,000-char chunks → GPT-4o-mini (3 retries)
4. **Fallback** — pdfplumber table detection when no API key
5. **Deduplication** — by `application_id + name_of_applicant`

### Columns Extracted

| Column | What It Contains |
|---|---|
| **application_id** | Application ID (numeric) |
| **name_of_applicant** | Developer name |
| **region** | NR, SR, ER, WR, NER |
| **type_of_project** | Solar, Wind, Hybrid, Hydro, ESS |
| **installed_capacity_mw** | Total installed capacity |
| **solar_mw** | Solar capacity breakdown |
| **wind_mw** | Wind capacity breakdown |
| **ess_mw** | ESS/BESS capacity breakdown |
| **hydro_mw** | Hydro capacity breakdown |
| **connectivity_mw** | Connectivity quantum |
| **present_connectivity_mw** | Present connectivity quantum |
| **substation** | Substation name |
| **state** | State/UT |
| **expected_date** | Expected date of connectivity / GNA effective |

---

## Module 3 — Mapping & Merge Operations

Orchestrates the merge of CMETS + Effectiveness data and runs all post-extraction operations.

### Data Sources

```
CMETS PDF (Module 1)  +  Effectiveness PDF (Module 2)  +  Bay Allocation PDF (Module 5)
         │                          │                               │
         └──────────┬───────────────┘                               │
                    ▼                                               │
           Merge via ID Cascade                                     │
           (GNA → LTA → 5.2)                                       │
                    │                                               │
                    ▼                                               │
         ┌──────────────────────┐                                   │
         │ 1. Merge             │                                   │
         │ 2. GNA Date Update   │                                   │
         │ 3. Add. Capacity Date│                                   │
         │ 4. Capacity Calc     │                                   │
         │ 5. Bay Mapping  ◄────┼───────────────────────────────────┘
         └──────────────────────┘
```

### What Gets Updated on Match

**Overlapping columns** (effectiveness overwrites CMETS if valid):

| Effectiveness Field | CMETS Column Updated |
|---|---|
| name_of_applicant | Name of Developers |
| substation | Substation |
| state | State |
| installed_capacity_mw | Application Quantum (MW)(ST II) |

**New enrichment columns**:

| Column | Source |
|---|---|
| Region | Effectiveness `region` |
| Type of Project | Effectiveness `type_of_project` |
| Installed capacity (MW) solar/wind/ess/hydro | Effectiveness per-technology MW |
| Installed capacity (MW) hybrid | Computed sum (when hybrid) |

### CMETS Columns Used to Identify Rows in Other Sources

| Target Source | CMETS Columns Used | Target Columns Used | Match Rule |
|---|---|---|---|
| **Effectiveness** | `GNA/ST II Application ID`, fallback `LTA Application ID`, fallback `Application ID under Enhancement 5.2 or revision` | Effectiveness `application_id` | Split all IDs in the CMETS cell, then apply GNA → LTA → 5.2 cascade. First matching effectiveness record wins. |
| **JCC** | `GNA/ST II Application ID`, fallback `LTA Application ID`, fallback `Application ID under Enhancement 5.2 or revision` | JCC `connectivity_applicant` | Search each CMETS ID inside normalized JCC applicant text. First match by cascade wins and becomes the row used for GNA/TGNA and JCC bay extraction. |
| **Bay Allocation** | `Voltage level`, `Name of Developers` | Bay Allocation voltage-specific bay columns and `Name of Entity` | Normalize voltage to `220kv`/`400kv`, normalize developer/entity names, then match by exact, substring, or core-name comparison. |
| **Type/capacity logic** | `Type` | Effectiveness `solar_mw`, `wind_mw`, `ess_mw`, `hydro_mw`, `type_of_project` | Parse CMETS Type values and combine with effectiveness per-technology MW values according to the supported-type formulas. |

---

## Module 4 — JCC Extraction & Matching

Reads **JCC Meeting** PDFs → extracts connectivity schedule data → computes GNA/TGNA.

### Stage 1 — Extraction

**Page gate**: Must contain ALL of "Pooling", "Quantum", "Connectivity".

**Table detection**: Header must match ≥3 target fragments.

| Column | What It Contains |
|---|---|
| **pooling_station** | Station name |
| **connectivity_applicant** | Developer name + application IDs |
| **connectivity_quantum_mw** | Applied MW |
| **schedule_as_per_current_jcc** | MW values and dates |
| **schedule_current_jcc_ists_scope / ists_scope** | "Under ISTS Scope Connectivity / Transmission System" text; may contain bay number details |
| **connectivity_start_date_under_gna** | GNA status text |

### Stage 2 — Effectiveness → JCC Matching

**Data sources**: Effectiveness PDF (IDs, names) + JCC PDF (schedule, status)

**Matching** (priority order):
1. **Application ID matching** — GNA/LTA/5.2 IDs checked against JCC row; most hits wins
2. **Fuzzy name matching** — substation↔pooling (50%) + developer↔applicant (50%), +15% substring bonus, 45% threshold

### Stage 3 — CMETS → JCC Mapping

For each CMETS row, identify the matching JCC row using the same CMETS application-ID cascade:

1. Read CMETS **GNA/ST II Application ID** and search it in JCC `connectivity_applicant`
2. If not found, read CMETS **LTA Application ID** and search it in JCC `connectivity_applicant`
3. If not found, read CMETS **Application ID under Enhancement 5.2 or revision** and search it in JCC `connectivity_applicant`

The first matching JCC row is then used for GNA/TGNA calculation and for any bay number present in the JCC ISTS-scope column.

### GNA / TGNA Computation

```
Read "connectivity_start_date_under_gna" from matched JCC row:

┌──────────────────────────────────────────────────────────────┐
│ CASE 1: Contains "Effective" (NOT "not effective"):          │
│   → GNA = SUM of ALL MW values in schedule column            │
│   Example: "300 MW: 15.06.2025" → GNA = 300                 │
│                                                              │
│ CASE 2: Does NOT contain "Effective":                        │
│   → TGNA = SUM of MW values whose nearby text contains       │
│      a Commission / Commissioned / COD keyword               │
│   Example: "111.8 MW (Commissioned), 88.2 MW COD achieved"  │
│            → TGNA = 200                                      │
│                                                              │
│ GNA and TGNA are mutually exclusive.                         │
└──────────────────────────────────────────────────────────────┘
```

For TGNA, the schedule text comes from JCC `schedule_as_per_current_jcc`. Only MW values linked to commissioning evidence are summed; MW values without Commission/Commissioned/COD context are ignored.

---

## Module 5 — Bay Allocation Extraction

Reads Bay Allocation PDFs → extracts substation-level bay data at 220kV/400kV.

### Extraction Steps

1. **Page gate** — must contain all required keywords
2. **Table detection** — ≥3 matching column fragments
3. **Row parsing** with noise filtering (skip empty, sub-headers, section headers, totals)

### Data Per Substation

| Field | Source Column | What It Contains |
|---|---|---|
| **sl_no** | Column 0 | Serial number |
| **name_of_substation** | Column 1 | e.g. "Bhadla-V" |
| **substation_coordinates** | Column 2 | Geographic coordinates |
| **region** | Column 3 | NR, SR, ER, WR, NER |
| **220kV bay_no** | Col 7 (bay), Col 9 (entity) | Dict: bay number → entity name |
| **400kV bay_no** | Col 10 (bay), Col 12 (entity) | Dict: bay number → entity name |

### Bay Number Rules

- Numeric strings (e.g. "204", "34")
- Voltage-specific (220kV and 400kV are separate)
- Only bays with non-empty entity names are indexed for matching
- Empty entity → stored as empty string in extraction, excluded from lookup

---

## Module 6 — Bay Mapping

Enriches CMETS rows with bay numbers, substation names, and coordinates. Bay number lookup is two-step:

1. **JCC first** — after the CMETS row is matched to a JCC row by GNA → LTA → 5.2 ID, read JCC `schedule_current_jcc_ists_scope` / `ists_scope` from the "Under ISTS Scope Connectivity / Transmission System" column. If this text contains a bay number, populate the bay number from JCC.
2. **Bay Allocation fallback** — if the matched JCC row does not contain a bay number, search the Bay Allocation PDF data for that CMETS row.

### Matching Workflow

```
For each CMETS row:

Step 0: Try JCC bay number
  • Use CMETS GNA/ST II Application ID → LTA Application ID → 5.2 ID
    to identify the JCC row through JCC "connectivity_applicant"
  • Read JCC "Under ISTS Scope Connectivity / Transmission System"
    (`schedule_current_jcc_ists_scope` / `ists_scope`)
  • If a bay number is present there, use it for Bay No

Step 1: Normalize Voltage
  "220 kV" / "220kV"  →  "220kv"
  "400 kV" / "400kV"  →  "400kv"
  Other               →  SKIP

Step 2: Normalize Developer Name
  • Lowercase
  • Remove: "Pvt. Ltd.", "Private Limited", "LLP", "Ltd."
  • Strip punctuation, collapse whitespace

Step 3: Search Bay Entries (under matching voltage)
  For each bay entity:
    a. Exact match after normalization
    b. CMETS name inside bay entity (substring)
    c. Bay entity inside CMETS name (substring)
    d. Core names match (after removing parenthetical info)
    Any of a/b/c/d = MATCH

Step 4: Multiple Matches
  • Deduplicate by bay number
  • Concatenate with " | "
```

Bay Allocation matching uses these CMETS columns:

| Target | CMETS Column Used | External Column Used |
|---|---|---|
| JCC row identification | `GNA/ST II Application ID`, fallback `LTA Application ID`, fallback `Application ID under Enhancement 5.2 or revision` | JCC `connectivity_applicant` |
| JCC bay number extraction | Matched CMETS row's JCC match | JCC `schedule_current_jcc_ists_scope` / `ists_scope` from "Under ISTS Scope Connectivity / Transmission System" |
| Bay Allocation voltage bucket | `Voltage level` | Bay Allocation 220kV / 400kV bay columns |
| Bay Allocation entity match | `Name of Developers` | Bay Allocation `Name of Entity` |
| Bay Allocation substation/coordinates | Matched bay allocation row | Bay Allocation `name_of_substation`, `substation_coordinates` |

### Output Columns

| Column | Source | Rule |
|---|---|---|
| **Bay No (Bay Allocation)** | JCC first, else Bay Allocation PDF | Use bay number from matched JCC ISTS-scope text if present; otherwise use matched bay allocation number(s), ` \| ` separated |
| **Substation Name (Bay Allocation)** | Bay Allocation PDF | Substation name, deduplicated when Bay Allocation fallback is used |
| **Substation Coordinates (Bay Allocation)** | Bay Allocation PDF | Coordinates, only when Bay Allocation fallback match found |

### Coordinate Rules

- Come from Bay Allocation PDF table Column 2
- Only populated when a Bay Allocation fallback match is found
- Multiple substations → concatenated and deduplicated
- No match → stays empty

---

## Cross-Module Features

### Type Extraction Workflow

```
CMETS PDF Page (raw text)
  e.g. "Solar (300)", "Hybrid + BESS", "Generator (Wind)"
         │
         ▼
STAGE 1: LLM Extraction
  • Extract ONLY supported project-type values:
    Hybrid, BESS, Hybrid+BESS, Solar+BESS, Solar, Wind, Hydro
  • Preserve MW values in parentheses
  • NO sentences, descriptions, or other words
  Output: "Solar (300)", "Hybrid+BESS (300 + 50)",
          "Wind (12) + BESS (19)", or "Hydro (150)"
         │
         ▼
STAGE 2: Normalization (norm_type)
  Token-by-token regex parsing:
  1. Scan for: solar, wind, bess/ess, hybrid, hydro/psp/pump storage
  2. Map "Solar+Wind" → "Hybrid"
  3. Interpret "Hybrid" as Solar + Wind for capacity calculation
  4. Interpret "Hybrid+BESS" as Solar + Wind + BESS
  5. Interpret "Solar+BESS" as Solar + BESS
  6. Interpret "Hydro", "PSP", and "Pump Storage" as Hydro
  7. Capture parenthetical MW: (300), (19)
  8. Reconstruct: "keyword (value) + keyword (value)"
  9. Drop unrecognized (Thermal → None)
         │
         ▼
FINAL OUTPUT
  Solar (300), Wind (12), BESS (50), Hybrid (500),
  Solar+BESS (319), Hybrid+BESS (550), Hydro (150), or null
```

**Allowed Type values**: Hybrid, BESS, Hybrid+BESS, Solar+BESS, Solar, Wind, Hydro.

**Hybrid rule**: `Hybrid = Solar + Wind`. Any source text saying `Solar+Wind` is treated as `Hybrid`; if BESS is also present, it becomes `Hybrid+BESS`.

**Supported Type calculation**:

| Type Value | Calculation Meaning |
|---|---|
| `Solar` | Solar capacity only: `solar_mw` + CMETS solar MW |
| `Wind` | Wind capacity only: `wind_mw` + CMETS wind MW |
| `BESS` | Storage capacity: `ess_mw` + CMETS BESS/ESS MW |
| `Hydro` | Hydro/pump-storage capacity: `hydro_mw` + CMETS hydro/PSP MW |
| `Hybrid` | Solar + Wind |
| `Solar+BESS` | Solar + BESS |
| `Hybrid+BESS` | Solar + Wind + BESS |

### GNA Operationalization Date & Yes/No

| Data Point | Source | Column |
|---|---|---|
| GNA Date (initial) | CMETS PDF | Near SCoD/SCOD terms |
| Expected Date | Effectiveness PDF | `expected_date` |
| GNA Date (final) | Computed | The later of the two |

**Update rule** (runs during Module 3 merge):

| Condition | Action |
|---|---|
| CMETS date is empty | Use effectiveness date |
| Effectiveness date > CMETS date | Update to effectiveness date |
| CMETS date ≥ Effectiveness date | Keep CMETS date |

**Yes/No computation** (from the final date):

| Condition | Value |
|---|---|
| Final date is in the **FUTURE** | **Yes** (not yet operationalized) |
| Final date is **TODAY or PAST** | **No** (already operationalized) |

### Installed/Break-up Capacity Calculator

**Data sources**:
- **Source 1**: CMETS PDF → "Type" column (e.g. `"Wind (12) + BESS (44)"` → `{wind: 12, ess: 44}`)
- **Source 2**: Effectiveness PDF → `solar_mw`, `wind_mw`, `ess_mw`, `hydro_mw`

**Computation** (per matched row):

```
Step 1: Parse CMETS Type → per-technology MW values
Step 2: Read Effectiveness per-technology MW values
Step 3: Sum matching categories:
  Solar  = eff solar_mw  + cmets solar value
  Wind   = eff wind_mw   + cmets wind value
  BESS   = eff ess_mw    + cmets BESS/ESS value
  Hydro  = eff hydro_mw  + cmets hydro value
  Hybrid = solar + wind only
  Solar+BESS = solar + BESS
  Hybrid+BESS = solar + wind + BESS
Step 4: Write non-zero values to output columns
```

Standalone BESS is retained in `Installed capacity (MW) ess`. In the installed/break-up calculator, BESS contributes to `Solar+BESS` and `Hybrid+BESS`; `Hybrid` itself remains only Solar + Wind.

**Category mapping**:

| Type Keyword | Category |
|---|---|
| Solar | solar |
| Wind | wind |
| BESS, ESS | ess |
| Hydro, PSP, Pump Storage | hydro |
| Hybrid, Solar+Wind | hybrid (solar + wind) |
| Solar+BESS | solar + ess |
| Hybrid+BESS | solar + wind + ess |

### Battery (BESS) Extraction

- Triggered only when BESS/Battery keywords are detected in Type or description
- Uses **LLM call** to extract MWh, Injection (MW), Drawl (MW)
- General rule: Drawl > Injection for BESS
- Values from Type column (e.g. `BESS (19)`) are also extracted

### Additional Capacity Date

Same future-date comparison as GNA Date Update but targets "Date from which additional capacity is to be added". Uses full GNA → LTA → 5.2 ID cascade. No Yes/No recomputation.
