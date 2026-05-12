# Pipeline Workflow, Columns, Sources, and Business Rules

This document explains the current workflow in non-technical language. It is based on the code in the CMETS, Effectiveness, JCC, Bay Allocation, and mapping handlers.

## Workflow Diagram

These diagrams use only plain text, arrows, and simple string characters so they can be copied into any Markdown or text viewer.

### Source Workflows

#### CMETS Source

```text
CMETS PDFs
  -> Read agenda/minutes PDFs
  -> Classify meeting as GNA or LTA from first pages
  -> Reject non-application pages/tables
     (GNARE, bulk consumer, drawee tables)
  -> Extract application rows
  -> Keep rows with at least one ID:
     GNA/ST II Application ID
     LTA Application ID
     Application ID under Enhancement 5.2 or revision
  -> Normalize IDs, dates, status, type, state, voltage, BESS, PSP
  -> Deduplicate application rows
  -> 01_cmets_extracted.xlsx
```

#### Effectiveness Source

```text
Effectiveness PDFs
  -> Read PDF pages
  -> Extract application_id, applicant, project type, MW break-up
  -> Extract substation, state, region, expected_date
  -> Normalize IDs, dates, capacity values
  -> Build lookup:
     application_id -> effectiveness record
  -> 02_effectiveness_extracted.xlsx
```

#### JCC Source

```text
JCC PDFs
  -> Page gate:
     Pooling + Quantum + Connectivity
  -> Table gate:
     applicant + quantum + schedule + GNA status columns
  -> Extract connectivity_applicant
  -> Extract schedule_as_per_current_jcc
     used for GNA/TGNA MW calculation
  -> Extract connectivity_start_date_under_gna
     used for effective/TGNA decision
  -> Extract schedule_current_jcc_ists_scope
     from "Under ISTS Scope Connectivity / Transmission System"
     may contain Bay No
  -> Keep JCC rows for Layer 4 matching
  -> 04_jcc_extracted.xlsx
```

#### Bay Allocation Source

```text
Bay Allocation PDFs
  -> Page gate:
     Name of Substation + RE Capacity Granted + Margin on Existing
  -> Detect bay allocation table headers
  -> Extract name_of_substation
  -> Extract substation_coordinates
  -> Extract region
  -> Extract 220kV bay records:
     220kV Bay No. + 220kV Name of Entity
  -> Extract 400kV bay records:
     400kV Bay No. + 400kV Name of Entity
  -> Build lookup:
     voltage bucket + entity name -> bay record
  -> 05_bayallocation_extracted.xlsx
```

### Combined Workflow

```text
01_cmets_extracted.xlsx
  -> Module 3: CMETS + Effectiveness mapping
  -> Match CMETS IDs against effectiveness application_id
     order: GNA/ST II Application ID -> LTA Application ID -> 5.2 ID
  -> Update applicant, substation, state, quantum, region, type, capacity columns
  -> Update GNA Operationalization Date from expected_date when later
  -> Update Additional Capacity Date using GNA -> LTA -> 5.2
  -> 03_cmets_effectiveness_mapped.xlsx
  -> Layer 4: CMETS-first JCC mapping
  -> Match CMETS IDs inside JCC connectivity_applicant
     order: GNA/ST II Application ID -> LTA Application ID -> 5.2 ID
  -> Once JCC row is identified:
     extract Bay No from schedule_current_jcc_ists_scope if present
  -> Check connectivity_start_date_under_gna
     -> if effective:
        GNA = sum all MW in schedule_as_per_current_jcc
     -> if not effective:
        TGNA = sum only MW tagged Commissioned in schedule_as_per_current_jcc
  -> 04_cmets_jcc_mapped.xlsx
  -> Module 6: Bay mapping
  -> Check Bay No (JCC)
     -> if present:
        use JCC Bay No and skip Bay Allocation search for that row
     -> if not present:
        search Bay Allocation lookup using:
        CMETS Voltage level + CMETS Name of Developers
  -> Populate Bay No, Substation Name, Coordinates, Bay No Source
  -> 06_cmets_bay_mapped.xlsx
```

## Source-by-Source Workflow

### 1. CMETS Source Workflow

1. Read CMETS PDFs.
2. Extract meeting metadata from first pages. If GNA keyword count is comparable to or higher than LTA, write meeting number/date to `CMETS GNA Approved` and `CMETS GNA Meeting Date`; otherwise write to LTA columns.
3. Reject pages/tables that are not connectivity application rows, especially GNARE, bulk consumer, and drawee tables.
4. Extract application rows with the CMETS prompt.
5. Keep only rows that have at least one primary ID: `GNA/ST II Application ID`, `LTA Application ID`, or `Application ID under Enhancement 5.2 or revision`.
6. Normalize IDs, dates, status, project type, state, voltage, battery, and PSP columns.
7. Deduplicate by `GNA/ST II Application ID`, `LTA Application ID`, `Project Location`, and `Name of Developers`.

### 2. Effectiveness Source Workflow

1. Read effectiveness PDFs.
2. Extract each row's `application_id` plus applicant, region, project type, capacity values, substation, state, and `expected_date`.
3. Build a lookup keyed by `application_id`.
4. This source is not matched by developer name. It is matched only by IDs from CMETS against effectiveness `application_id`.

### 3. JCC Source Workflow

1. Read JCC PDFs.
2. Accept only pages that contain `Pooling`, `Quantum`, and `Connectivity`.
3. Accept only tables whose headers indicate applicant, quantum, generation commissioning schedule, current schedule, and GNA status content.
4. Extract JCC rows containing `connectivity_applicant`, `schedule_as_per_current_jcc`, `schedule_current_jcc_ists_scope`, and `connectivity_start_date_under_gna`.
5. The `schedule_current_jcc_ists_scope` column corresponds to the PDF column "Under ISTS Scope Connectivity / Transmission System". This text may contain the bay number.
6. JCC rows are not mapped immediately during extraction. They are used later by the CMETS-first Layer 4 mapping.
7. TGNA/GNA and JCC bay number extraction happen only after a CMETS row is matched to a JCC row.

### 4. Bay Allocation Source Workflow

1. Read Bay Allocation PDFs.
2. Accept pages that contain substation name, RE capacity granted, and margin-on-existing markers.
3. Extract substation name, coordinates, region, 220kV bay records, and 400kV bay records.
4. Build a lookup with two buckets: `220kv` and `400kv`.
5. Each lookup entry stores Bay Allocation `entity_name`, `bay_no`, `name_of_substation`, and `substation_coordinates`.

## Universal Matching Priority

Most cross-source matching follows this business order:

1. Use `GNA/ST II Application ID`.
2. If not found, use `LTA Application ID`.
3. If not found, use `Application ID under Enhancement 5.2 or revision`.

Effectiveness and JCC depend heavily on this ID cascade. Bay Allocation fallback does not use application IDs; it uses voltage plus developer/entity name after the JCC bay-number check has failed.

## Cross-Source Matching Map

| Workflow step | CMETS column used | Other source column searched | Match order / rule | Columns updated after match |
| ------------- | ----------------- | ---------------------------- | ------------------ | --------------------------- |
| CMETS + Effectiveness merge | `GNA/ST II Application ID`, `LTA Application ID`, `Application ID under Enhancement 5.2 or revision` | Effectiveness `application_id` lookup key | Try GNA first, then LTA, then 5.2. The first ID found in the lookup wins. | `Name of Developers`, `Substation`, `State`, `Application Quantum (MW)(ST II)`, `Region`, `Type of Project`, installed capacity break-up columns |
| GNA Operationalization Date update | `GNA/ST II Application ID`, then `LTA Application ID` | Effectiveness `application_id`; date value from `expected_date` | Try GNA, then LTA. If `expected_date` is later than CMETS `GNA Operationalization Date`, update CMETS date. | `GNA Operationalization Date`, `GNA Operationalization (Yes/No)` |
| Additional Capacity Date update | `GNA/ST II Application ID`, `LTA Application ID`, `Application ID under Enhancement 5.2 or revision` | Effectiveness `application_id`; date value from `expected_date` | Try GNA, then LTA, then 5.2. If `expected_date` is later than CMETS date, update CMETS date. | `Date from which additional capacity is to be added` |
| CMETS + JCC Layer 4 | `GNA/ST II Application ID`, `LTA Application ID`, `Application ID under Enhancement 5.2 or revision` | JCC `connectivity_applicant` free text | Split all IDs from CMETS cells. Search each ID inside JCC `connectivity_applicant`, in GNA -> LTA -> 5.2 order. | `TGNA`, `GNA`, `Match Source` |
| JCC bay number extraction | Matched CMETS row from Layer 4 | JCC `schedule_current_jcc_ists_scope` / `ists_scope` | Once the CMETS row is identified in JCC by GNA -> LTA -> 5.2 ID, read the "Under ISTS Scope Connectivity / Transmission System" text. If a bay number is present, use it as the bay number. | `Bay No (JCC)`, then `Bay No (Bay Allocation)` in Module 6 |
| CMETS + Bay Allocation fallback | `Voltage level`, `Name of Developers` | Bay lookup voltage bucket and `entity_name` | Run only when the matched JCC row does not provide a bay number. Normalize voltage to `220kv` or `400kv`; then match developer/entity by exact, substring, or parenthetical core-name comparison. | `Bay No (Bay Allocation)`, `Substation Name (Bay Allocation)`, `Substation Coordinates (Bay Allocation)`, `Bay No Source` |

## CMETS Columns

| Column                       | Source                 | Rule in plain English                                                                                                                                                                                               |
| ---------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PDF`                      | CMETS internal         | File path/name of the source PDF.                                                                                                                                                                                   |
| `Page Number`              | CMETS internal         | Page number where the row was extracted.                                                                                                                                                                            |
| `CMETS GNA Approved`       | CMETS meeting metadata | Meeting number is placed here when the PDF is classified as GNA. Meeting number patterns include `42nd Consulting Meeting`, `42nd CMETS`, `CMETS_NR/42`, or `Meeting No. 42`.                               |
| `CMETS LTA Approved`       | CMETS meeting metadata | Meeting number is placed here when LTA keywords are clearly dominant.                                                                                                                                               |
| `CMETS GNA Meeting Date`   | CMETS meeting metadata | Date from first page, or page 2 fallback, written as `dd.mm.yyyy` when PDF is GNA. Pattern examples: `11th November 2025`, `25 April 2025`, `November 11, 2025`.                                            |
| `CMETS LTA Meeting Date`   | CMETS meeting metadata | Same date logic, but filled when PDF is LTA.                                                                                                                                                                        |
| `Substation`               | CMETS PDF text         | Extracted from headers like Connectivity Location, Nearest Pooling Station, Connectivity Granted at, Connectivity Injection Point, Sub-station. Value patterns include names like `Bhadla-V` or `Aligarh (PG)`. |
| `Project Location`         | CMETS PDF text         | Extracted directly when Project Location appears.                                                                                                                                                                   |
| `Name of Developers`       | CMETS PDF text         | Extracted from Applicant/Developer columns. Company-name patterns help identify it. Values containing LOA/criterion/applying artefacts are rejected.                                                                |
| `GNA/ST II Application ID` | CMETS PDF text         | GNA or Stage-II application ID. Accepted values are normalized as numeric IDs and kept as one of the primary keys for Effectiveness and JCC matching.                                                               |

- Remove duplicate rows using GNA, LTA, Project Location, and Developer as the key.

## Effectiveness Columns

| Column                      | Source                 | Rule in plain English                                                                                                                                  |
| --------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `source_file`             | Effectiveness internal | Source effectiveness PDF name.                                                                                                                         |
| `sl_no`                   | Effectiveness PDF      | Serial number from the table.                                                                                                                          |
| `application_id`          | Effectiveness PDF      | Main ID used to build the lookup for mapping.                                                                                                          |
| `name_of_applicant`       | Effectiveness PDF      | Applicant name. Can overwrite CMETS developer name when IDs match.                                                                                     |
| `region`                  | Effectiveness PDF      | Region. Populates mapped `Region`.                                                                                                                   |
| `type_of_project`         | Effectiveness PDF      | Project type such as Solar, Wind, Hybrid, Hydro, ESS. Populates mapped `Type of Project`.                                                            |
| `installed_capacity_mw`   | Effectiveness PDF      | Numeric float. Can overwrite CMETS Application Quantum.                                                                                                |
| `solar_mw`                | Effectiveness PDF      | Numeric solar capacity. Populates `Installed capacity (MW) solar`.                                                                                   |
| `wind_mw`                 | Effectiveness PDF      | Numeric wind capacity. Populates `Installed capacity (MW) wind`.                                                                                     |
| `ess_mw`                  | Effectiveness PDF      | Numeric ESS/BESS capacity. Populates `Installed capacity (MW) ess`.                                                                                  |
| `hydro_mw`                | Effectiveness PDF      | Numeric hydro/pump-storage capacity. Populates `Installed capacity (MW) hydro`.                                                                      |
| `connectivity_mw`         | Effectiveness PDF      | Numeric connectivity capacity. Listed as an overlapping update candidate but current merge updates Application Quantum using installed capacity first. |
| `present_connectivity_mw` | Effectiveness PDF      | Current/deemed GNA connectivity. Extracted and exported, not directly mapped into CMETS columns in current merge.                                      |
| `substation`              | Effectiveness PDF      | Can overwrite CMETS Substation when IDs match.                                                                                                         |
| `state`                   | Effectiveness PDF      | Can overwrite CMETS State when IDs match.                                                                                                              |
| `expected_date`           | Effectiveness PDF      | Used to update GNA Operationalization Date and Additional Capacity Date. Date parsing accepts numeric and month-name formats.                          |

### Effectiveness Mapping Rules

- Build lookup as `application_id -> effectiveness record`.
- Disk JSON cache overrides the in-memory current extraction if the same application ID appears.
- On a CMETS match, update only when the effectiveness value is not blank/null/NA.
- Date rule: if CMETS date is empty, use effectiveness date. If effectiveness date is later, update. If CMETS date is same/later, keep CMETS date.

## Effectiveness-Derived Mapped Columns

| Output column                               | Source                            | Rule                                                                                              |
| ------------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------- |
| `Region`                                  | Effectiveness `region`          | Filled on ID match.                                                                               |
| `Type of Project`                         | Effectiveness `type_of_project` | Filled on ID match.                                                                               |
| `Installed capacity (MW) solar`           | Effectiveness `solar_mw`        | Filled on ID match.                                                                               |
| `Installed capacity (MW) wind`            | Effectiveness `wind_mw`         | Filled on ID match.                                                                               |
| `Installed capacity (MW) ess`             | Effectiveness `ess_mw`          | Filled on ID match.                                                                               |
| `Installed capacity (MW) hydro`           | Effectiveness `hydro_mw`        | Filled on ID match.                                                                               |
| `Installed capacity (MW) hybrid`          | Effectiveness component MW        | If effectiveness type is hybrid, sum solar + wind + ESS + hydro.                                  |
| `Installed/Break-up Capacity (MW) Solar`  | Effectiveness + CMETS Type        | Sum effectiveness solar MW and CMETS `Type` solar MW.                                           |
| `Installed/Break-up Capacity (MW) Wind`   | Effectiveness + CMETS Type        | Sum effectiveness wind MW and CMETS `Type` wind MW.                                             |
| `Installed/Break-up Capacity (MW) Hybrid` | Effectiveness + CMETS Type        | If project is hybrid or has multiple categories, sum all effectiveness MW plus all CMETS Type MW. |
| `Installed/Break-up Capacity (MW) Hydro`  | Effectiveness + CMETS Type        | Sum effectiveness hydro MW and CMETS hydro/PSP type MW.                                           |

## JCC Columns

| Column                                | Source        | Rule in plain English                                                                            |
| ------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------ |
| `source_pdf`                        | JCC internal  | Source JCC PDF name.                                                                             |
| `page_number`                       | JCC internal  | Page where the table row was found.                                                              |
| `sr_no`                             | JCC PDF table | Serial number.                                                                                   |
| `pooling_station`                   | JCC PDF table | Pooling station/substation in JCC row.                                                           |
| `connectivity_applicant`            | JCC PDF table | Applicant text. This is where CMETS GNA/LTA/5.2 IDs are searched.                                |
| `connectivity_quantum_mw`           | JCC PDF table | Connectivity quantum in MW.                                                                      |
| `gen_comm_schedule_prev_jcc`        | JCC PDF table | Previous JCC generation commissioning schedule.                                                  |
| `schedule_as_per_current_jcc`       | JCC PDF table | Current JCC generation/line schedule. Used to compute GNA/TGNA MW.                               |
| `schedule_current_jcc_ists_scope`   | JCC PDF table | Text from "Under ISTS Scope Connectivity / Transmission System". It may contain bay number details. It is not used for GNA/TGNA MW calculation, but after a CMETS row is matched to this JCC row, the code extracts bay number from this text first. |
| `connectivity_start_date_under_gna` | JCC PDF table | Status text used to decide whether the matched row is GNA or TGNA.                               |
| `remarks`                           | JCC PDF table | Remarks from JCC table.                                                                          |

### JCC Extraction Rules

- A JCC page must contain `Pooling`, `Quantum`, and `Connectivity`.
- A table is accepted if its header contains at least 3 of: pooling, applicant, quantum, gen comm, schedule as per, connectivity start.
- Header rows are skipped when they contain pooling/grantee scope/under ISTS wording.

### JCC Mapping and Computed Columns

| Output column    | Source          | Rule                                                                                                                                                 |
| ---------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TGNA`         | Matched JCC row | If `connectivity_start_date_under_gna` is not effective and schedule text has MW values marked `Commissioned`, sum those commissioned MW values. |
| `GNA`          | Matched JCC row | If status contains `effective` and is not `not effective` or `non-effective`, sum all MW values in `schedule_as_per_current_jcc`.            |
| `Match Source` | Mapping logic   | Shows whether row matched through GNA, LTA, or 5.2 ID.                                                                                               |
| `Bay No (JCC)` | Matched JCC row | Extracted from `schedule_current_jcc_ists_scope` / `ists_scope` after the CMETS row is matched to JCC. Multiple bay numbers are joined with `\|`. |

Layer 4 now runs every pipeline execution. For every CMETS row, it searches JCC `connectivity_applicant` in this order: GNA ID, then LTA ID, then 5.2 ID. Once the JCC row is identified, the same matched row is used for GNA/TGNA and for extracting the JCC bay number from the ISTS-scope column.

### JCC Commissioned TGNA and GNA Logic

The code already contains this logic in `pipeline/jcc_handler/jcc_output_layer.py`.

1. Matching happens first. CMETS IDs are searched inside JCC `connectivity_applicant`. The ID cascade is `GNA/ST II Application ID` -> `LTA Application ID` -> `Application ID under Enhancement 5.2 or revision`.
2. After a JCC row is matched, `connectivity_start_date_under_gna` decides whether the row is treated as GNA or TGNA.
3. GNA path: if `connectivity_start_date_under_gna` contains `effective`, except negated phrases like `not effective` or `non-effective`, then the system reads `schedule_as_per_current_jcc` and sums every MW value in that schedule. The result is written to `GNA`.
4. TGNA path: if `connectivity_start_date_under_gna` is non-empty but not effective, the system reads `schedule_as_per_current_jcc` and sums only MW values whose nearby text says `Commissioned`. The result is written to `TGNA`.
5. GNA and TGNA are mutually exclusive for a matched JCC row. A row can populate `GNA`, or `TGNA`, or neither if the matched JCC row lacks usable MW text.

Example TGNA schedule text:

```text
111.8 MW: 19.05.2025 (Commissioned)
88.2 MW: 01.06.2025 (Commissioned)
100 MW: 30.09.2025
```

Only the commissioned values are used for TGNA, so `TGNA = 111.8 + 88.2 = 200`. The uncommissioned `100 MW` is ignored for TGNA.

## Bay Allocation Columns

### Full PDF Table Columns

| Column                                               | Source                     | Rule in plain English                         |
| ---------------------------------------------------- | -------------------------- | --------------------------------------------- |
| `sl_no`                                            | Bay Allocation table col 0 | Starts a new substation when numeric.         |
| `name_of_substation`                               | Col 1                      | Substation name.                              |
| `substation_coordinates`                           | Col 2                      | Coordinates of the substation.                |
| `region`                                           | Col 3                      | Region.                                       |
| `transformation_capacity_planned_mva`              | Col 4                      | Planned transformation capacity.              |
| `transformation_capacity_existing_mva`             | Col 5                      | Existing transformation capacity.             |
| `transformation_capacity_under_implementation_mva` | Col 6                      | Under-implementation transformation capacity. |
| `bay_no_220kv`                                     | Col 7                      | RE capacity granted 220kV bay number.         |
| `connectivity_quantum_mw_220kv`                    | Col 8                      | 220kV connectivity quantum.                   |
| `name_of_entity_220kv`                             | Col 9                      | 220kV entity/developer name.                  |
| `bay_no_400kv`                                     | Col 10                     | RE capacity granted 400kV bay number.         |
| `connectivity_quantum_mw_400kv`                    | Col 11                     | 400kV connectivity quantum.                   |
| `name_of_entity_400kv`                             | Col 12                     | 400kV entity/developer name.                  |
| `margin_bay_no_220kv`                              | Col 13                     | 220kV margin bay number.                      |
| `margin_available_mw_220kv`                        | Col 14                     | 220kV margin available.                       |
| `margin_bay_no_400kv`                              | Col 15                     | 400kV margin bay number.                      |
| `margin_available_mw_400kv`                        | Col 16                     | 400kV margin available.                       |
| `space_provision_220kv`                            | Col 17                     | Space provision for 220kV line bays.          |
| `space_provision_400kv`                            | Col 18                     | Space provision for 400kV line bays.          |
| `remarks`                                          | Col 19                     | Remarks.                                      |

### Bay Allocation Excel Columns

| Column                     | Source         | Rule                                         |
| -------------------------- | -------------- | -------------------------------------------- |
| `source_pdf`             | Internal       | Bay Allocation PDF name.                     |
| `page_number`            | Internal       | Page number.                                 |
| `sl_no`                  | Extracted      | Substation serial number.                    |
| `name_of_substation`     | Extracted      | Substation name.                             |
| `substation_coordinates` | Extracted      | Coordinates.                                 |
| `region`                 | Extracted      | Region.                                      |
| `220kv_bay_no`           | Extracted dict | Serialized as `bay: entity \| bay: entity`. |
| `400kv_bay_no`           | Extracted dict | Serialized as `bay: entity \| bay: entity`. |

### Bay Mapping Columns

| Output column                               | Source                | Rule                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Bay No (JCC)`                            | Layer 4 JCC match     | Extracted from the matched JCC row's "Under ISTS Scope Connectivity / Transmission System" text. This is the preferred bay source.                                                                                                                                                                                         |
| `Bay No (Bay Allocation)`                 | JCC first, else Bay Allocation lookup | If `Bay No (JCC)` is present, copy it here and skip Bay Allocation search for that row. If JCC has no bay number, match CMETS voltage to 220kV/400kV and match CMETS developer name to bay entity name by exact/substring/core-name comparison. Multiple bay numbers are joined by `\|`. |
| `Substation Name (Bay Allocation)`        | Bay Allocation lookup | From matched bay allocation substation. Duplicates are removed. Populated only when the Bay Allocation fallback is used.                                                                                                                                                                                                    |
| `Substation Coordinates (Bay Allocation)` | Bay Allocation lookup | From matched bay allocation substation. Populated only when the Bay Allocation fallback is used. If coordinates already exist and no JCC bay number is present, the row is skipped and not searched again.                                                                                                                  |
| `Bay No Source`                           | Mapping logic         | `JCC Under ISTS Scope` when the bay number came from JCC; `Bay Allocation PDF` when it came from the fallback lookup.                                                                                                                                                                                                      |

### Bay Number Selection Logic

1. Module 4 Layer 4 matches each CMETS row to a JCC row by searching CMETS IDs inside JCC `connectivity_applicant` in GNA -> LTA -> 5.2 order.
2. After the JCC row is identified, the code reads `schedule_current_jcc_ists_scope` / `ists_scope`, which corresponds to "Under ISTS Scope Connectivity / Transmission System".
3. If that text contains a bay number, `Bay No (JCC)` is populated and Module 6 copies it into `Bay No (Bay Allocation)` with `Bay No Source = JCC Under ISTS Scope`.
4. If the matched JCC row does not contain a bay number, Module 6 searches Bay Allocation PDFs for that row using voltage plus developer/entity name.
5. If Bay Allocation fallback matches, Module 6 writes bay number, substation name, coordinates, and `Bay No Source = Bay Allocation PDF`.

### Exact Row Identification and Source Dependency

The pipeline starts from a CMETS row. Other sources do not create the final row by themselves; they enrich the already-selected CMETS row.

| Source used to enrich CMETS row | Exact CMETS column names used to identify/search | Exact source column names searched | How the source row is identified |
| ------------------------------- | ------------------------------------------------ | ---------------------------------- | -------------------------------- |
| Effectiveness PDF | `GNA/ST II Application ID`, then `LTA Application ID`, then `Application ID under Enhancement 5.2 or revision` | `application_id` | Split IDs from the CMETS columns. Search them in `application_id` in priority order. First matching effectiveness record wins. |
| JCC PDF | `GNA/ST II Application ID`, then `LTA Application ID`, then `Application ID under Enhancement 5.2 or revision` | `connectivity_applicant` | Split IDs from the CMETS columns. Search each ID inside JCC `connectivity_applicant` in priority order. First matching JCC row wins. |
| Bay Allocation PDF fallback | `Voltage level`, `Name of Developers` | Voltage bucket from `220kv.bay_no` / `400kv.bay_no`; entity text from Bay Allocation `name_of_entity_220kv` or `name_of_entity_400kv` | Normalize CMETS `Voltage level` to `220kv` or `400kv`. Search only that voltage bucket. Match CMETS `Name of Developers` against Bay Allocation entity name by exact, substring, or core-name comparison. |

Bay Allocation does not depend on CMETS application IDs. It depends on CMETS `Voltage level` and `Name of Developers`, but only after the JCC bay-number check fails.

### Exact Column Lineage for Bay Mapping

| Final output column | Preferred source | Fallback source | Exact source columns used | Rule |
| ------------------- | ---------------- | --------------- | ------------------------- | ---- |
| `Bay No (JCC)` | JCC PDF | None | JCC `schedule_current_jcc_ists_scope` / `ists_scope` | After the JCC row is identified through CMETS IDs, extract bay number from the "Under ISTS Scope Connectivity / Transmission System" text. |
| `Bay No (Bay Allocation)` | JCC `Bay No (JCC)` | Bay Allocation PDF | JCC `schedule_current_jcc_ists_scope` / `ists_scope`; fallback Bay Allocation `220kv.bay_no` or `400kv.bay_no` | If JCC bay exists, copy it here. If not, use Bay Allocation fallback matching and write matched bay number(s). |
| `Substation Name (Bay Allocation)` | Bay Allocation PDF | None | Bay Allocation `name_of_substation` | Populated only when Bay Allocation fallback finds a matched row. |
| `Substation Coordinates (Bay Allocation)` | Bay Allocation PDF | None | Bay Allocation `substation_coordinates` | Populated only when Bay Allocation fallback finds a matched row. |
| `Bay No Source` | Mapping logic | Mapping logic | Derived from whether JCC or Bay Allocation supplied the bay number | `JCC Under ISTS Scope` when JCC supplied the bay number; `Bay Allocation PDF` when fallback supplied it. |

### Bay Allocation PDF Columns Extracted

The Bay Allocation PDF extraction stores each substation as one record with 220kV and 400kV bay dictionaries.

| Extracted field in JSON / Excel | Exact PDF table column | How it is used later |
| -------------------------------- | ---------------------- | -------------------- |
| `name_of_substation` | `Name of Substation` | Written to `Substation Name (Bay Allocation)` after fallback match. |
| `substation_coordinates` | `Coordinates` / substation coordinate column | Written to `Substation Coordinates (Bay Allocation)` after fallback match. |
| `region` | `Region` | Stored in lookup entry; not currently written to bay output columns. |
| `220kv.bay_no` / `220kv_bay_no` | 220kV `Bay No.` plus 220kV `Name of Entity` | Builds lookup entries for CMETS rows where `Voltage level` normalizes to `220kv`. |
| `400kv.bay_no` / `400kv_bay_no` | 400kV `Bay No.` plus 400kV `Name of Entity` | Builds lookup entries for CMETS rows where `Voltage level` normalizes to `400kv`. |

### Bay Allocation Fallback Row Match in Detail

1. Read CMETS `Voltage level`.
2. Normalize `Voltage level`:
   - values containing `220` become `220kv`
   - values containing `400` become `400kv`
   - any other value is skipped as `no_voltage`
3. Read CMETS `Name of Developers`.
4. Pick Bay Allocation lookup entries only from the matching voltage bucket.
5. Compare CMETS `Name of Developers` with Bay Allocation entity name from the selected bucket:
   - exact normalized name match
   - CMETS name contained in Bay Allocation entity name
   - Bay Allocation entity name contained in CMETS name
   - core-name match after removing parenthetical capacity text
6. If one or more entries match, deduplicate by bay number and write:
   - matched bay numbers to `Bay No (Bay Allocation)`
   - matched `name_of_substation` to `Substation Name (Bay Allocation)`
   - matched `substation_coordinates` to `Substation Coordinates (Bay Allocation)`
   - `Bay No Source = Bay Allocation PDF`
