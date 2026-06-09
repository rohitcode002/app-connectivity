# Type Column Calculation & Extraction Flow

## Complete Documentation: How the "Type" Column is Calculated from CMETS → Effectiveness → DTBC

---

## **Overview**

The **"Type"** (or **"Type of Project"**) column flows through three main stages:

1. **CMETS Extraction** - LLM extracts raw Type from PDFs
2. **Effectiveness Mapping** - Type_of_project from effectiveness data enriches CMETS
3. **DTBC Final Resolution** - Final Type is recalculated from all capacity evidence

---

## **STAGE 1: CMETS EXTRACTION**

### **A. LLM Extraction from PDF**

The LLM extracts the "Type" column directly from CMETS PDF tables.

**Source Columns Recognized:**
- "Type of Source/Generation/Energy/Plant"
- "Source Type"
- "Generation Type"  
- "Energy Source"

**What the LLM Extracts:**
- Raw text like: `"Solar"`, `"Wind"`, `"Hybrid"`, `"Solar+BESS"`, `"Solar (300) + BESS (44)"`
- Sometimes with MW values: `"Wind (12) + BESS (44)"`
- Sometimes with duration: `"Solar (300) + BESS (44, 4hr)"`

**Location:** `pipeline/cmets_handler/extraction.py` → LLM call → raw row extraction

---

### **B. Type Normalization (CMETS)**

After extraction, the raw Type value goes through normalization.

**Function:** `norm_type(v)` in `pipeline/cmets_handler/normalization.py`

**Process:**
1. **Parse capacity values** from Type string using `parse_type_capacity(v)`
2. **Detect component keywords** using `_components_from_keywords(v)`
3. **Combine** components from both methods
4. **Convert to canonical format** using `components_to_type()`

#### **parse_type_capacity(v)** - Extracts MW Values

**Patterns Detected:**
```python
# Explicit label first: "Solar: 100 MW", "BESS - 50 MW", "Wind (12)"
"Solar: 100 MW"       → {"Solar": 100.0}
"BESS (50)"           → {"BESS": 50.0}
"Wind-12"             → {"Wind": 12.0}

# Trailing qualifier: "100 MW (Solar)"
"100 MW (Solar)"      → {"Solar": 100.0}
"300 MW (BESS)"       → {"BESS": 300.0}

# Legacy compact: "100(Solar)", "300 (BESS)"
"100(Solar)"          → {"Solar": 100.0}
"300 (BESS)"          → {"BESS": 300.0}

# BESS with duration: "300 (BESS 4 Hr)", "300 MW (BESS - 4hr)"
"300 (BESS 4hr)"      → {"BESS": 300.0}  # Duration extracted separately
"300 MW (BESS-4 hours)" → {"BESS": 300.0}
```

**Component Labels Recognized:**
- Solar, Wind, Hydro, Hydel
- BESS, ESS, Battery, Battery Energy Storage
- PSP, Pump Storage, Pumped Storage
- Hybrid, Thermal

**Canonical Mapping:**
```python
"solar"     → "Solar"
"wind"      → "Wind"
"bess/ess"  → "BESS"
"hydro"     → "Hydro"
"psp"       → "PSP"
```

#### **components_to_type()** - Canonical Type String

**Rules:**
```python
# Multi-component combinations
Solar + Wind + BESS           → "Hybrid+BESS"
Solar + Wind                  → "Hybrid"
Solar + BESS                  → "Solar+BESS"
Wind + BESS                   → "Wind+BESS"
Hydro + BESS                  → "Hydro+BESS"

# Single components
{Solar}                       → "Solar"
{Wind}                        → "Wind"
{Hydro}                       → "Hydro"
{BESS}                        → "BESS"
{PSP}                         → "PSP"

# Multiple without standard combinations
{Solar, Hydro, BESS}          → "Solar+Hydro+BESS"  (ordered)
```

**Component Order:** Solar → Wind → Hydro → PSP → BESS

---

### **C. Type Enrichment with MW (CMETS)**

**Function:** `enrich_type_with_mw(row)` in `pipeline/cmets_handler/normalization.py`

This function enriches the Type string with MW values from other columns when the Type lacks explicit MW values.

**Process:**
1. Parse any existing MW values in Type
2. Detect component keywords present in Type
3. **Enrich from capacity columns** if MW values are missing:
   ```python
   BESS sources: Battery MWh, Battery Injection (MW), Battery Drawl (MW)
   PSP sources:  PSP MWh, PSP Injection (MW), PSP Drawl (MW)
   ```
4. If single Application Quantum exists + one non-BESS/PSP component → assign it
5. Build final Type string with MW and duration

**Example:**
```python
Input Row:
  Type: "Solar + BESS"
  Application Quantum (MW)(ST II): 300
  Battery Injection (MW): 44
  (BESS duration in Type): 4 hours

Output Type: "Solar (300) + BESS (44, 4hr)"
```

---

## **STAGE 2: EFFECTIVENESS MAPPING**

### **A. Effectiveness Extraction**

**Source:** Effectiveness PDFs (from POSOCO Regenerators)

**Extraction Method:** Camelot (direct table parsing, no LLM)

**Location:** `pipeline/effectiveness_handler/extraction.py`

**Column Extracted:** `type_of_project`

**Recognized Headers:**
- "Type of Project"
- "Type"

**Values Extracted:**
```
"Solar"
"Wind"
"Hybrid"
"Solar+ESS"
"Hydro"
"Wind+ESS"
"Hybrid+ESS"
```

**Also Extracted:** Per-technology MW columns:
- `solar_mw`
- `wind_mw`
- `ess_mw`
- `hydro_mw`

---

### **B. Effectiveness → CMETS Type Mapping**

**Location:** `pipeline/final_mapping_handler/runner.py` → `_step1_effectiveness_mapping()`

**Process:**

1. **Load effectiveness data** → build `application_id → record` lookup

2. **For each CMETS row:**
   - Search effectiveness lookup in **GNA → LTA → 5.2** ID cascade
   - When matched, update CMETS columns directly:
     ```python
     effectiveness.name_of_applicant  → CMETS."Name of Developers"
     effectiveness.substation         → CMETS."Substation"
     effectiveness.state              → CMETS."State"
     effectiveness.installed_capacity_mw → CMETS."Application Quantum (MW)(ST II)"
     ```

3. **Compute Installed/Break-up Capacity columns:**
   - Parse CMETS Type for MW values
   - Match effectiveness `type_of_project` keyword → choose target column
   - **Target column = effectiveness MW + CMETS Type parsed MW**

**Function:** `_set_installed_breakdown_from_type()`

**Logic:**
```python
# Parse CMETS Type for MW values
cmets_type_mw = {
    "solar": MW from Type,
    "wind": MW from Type,
    "hybrid": MW from Type,
    "hydro": MW from Type
}

# Get effectiveness type and per-tech MW
eff_type = effectiveness.type_of_project.lower()  # "solar", "wind", "hybrid", etc.
eff_mw = {
    "solar": effectiveness.solar_mw,
    "wind": effectiveness.wind_mw,
    "ess": effectiveness.ess_mw,
    "hydro": effectiveness.hydro_mw
}

# Map effectiveness type to target output columns
if "solar" in eff_type or "hybrid" in eff_type:
    output["Installed/Break-up Capacity (MW) Solar"] = eff_mw["solar"] + cmets_type_mw["solar"]

if "wind" in eff_type or "hybrid" in eff_type:
    output["Installed/Break-up Capacity (MW) Wind"] = eff_mw["wind"] + cmets_type_mw["wind"]

if "hybrid" in eff_type:
    # Sum all components for hybrid
    output["Installed/Break-up Capacity (MW) Hybrid"] = (
        eff_mw["solar"] + eff_mw["wind"] + eff_mw["ess"] + eff_mw["hydro"] +
        cmets_type_mw["solar"] + cmets_type_mw["wind"] + cmets_type_mw["hydro"]
    )

if "hydro" in eff_type or "psp" in eff_type:
    output["Installed/Break-up Capacity (MW) Hydro"] = eff_mw["hydro"] + cmets_type_mw["hydro"]
```

**Note:** The CMETS "Type" column itself is **NOT** directly updated from effectiveness. It remains the CMETS-extracted value.

---

## **STAGE 3: DTBC FINAL TYPE RESOLUTION**

### **Location:** `pipeline/final_mapping_handler/data_capture.py` → `_resolve_type()`

This is the **final recalculation** of Type for the "Data to be Captured" output.

**Process:**

1. **Start with CMETS Type keywords:**
   ```python
   components = components_from_type_keywords(row["Type"])
   ```

2. **Add components from capacity evidence:**
   ```python
   capacity_cols = {
       "Solar": [
           "Installed/Break-up Capacity (MW) Solar",
           "Installed capacity (MW) solar"
       ],
       "Wind": [
           "Installed/Break-up Capacity (MW) Wind",
           "Installed capacity (MW) wind"
       ],
       "Hydro": [
           "Installed/Break-up Capacity (MW) Hydro",
           "Installed capacity (MW) hydro"
       ],
       "BESS": [
           "Battery MWh",
           "Battery Injection (MW)",
           "Battery Drawl (MW)",
           "Installed capacity (MW) ess"
       ],
       "PSP": [
           "PSP MWh",
           "PSP Injection (MW)",
           "PSP Drawl (MW)"
       ]
   }
   
   # For each component, check if ANY of its columns has value > 0
   for component, candidates in capacity_cols.items():
       for candidate in candidates:
           if row[candidate] > 0:
               components.add(component)
               capacity_components.add(component)
               break
   ```

3. **Special Hybrid Detection:**
   ```python
   nature = row["Nature of Applicant"].lower()
   has_solar_wind = {"Solar", "Wind"}.issubset(capacity_components)
   
   if "hybrid" in nature and has_solar_wind:
       return "Hybrid"
   ```

4. **Hardcoded Special Cases:**
   ```python
   # Specific application IDs with known types
   ids = row["GNA/ST II ID"] + row["LTA ID"] + row["Enhancement 5.2 ID"]
   
   if "2200000305" in ids or "2200000319" in ids:
       if {"Solar", "BESS"}.issubset(capacity_components):
           return "Solar+BESS"
   ```

5. **Convert to canonical Type:**
   ```python
   return components_to_type(components)
   ```

---

## **COMPLETE FLOW EXAMPLE**

### **Example 1: Simple Solar Project**

**Step 1: CMETS Extraction**
```
PDF Table: "Type: Solar"
→ LLM extracts: "Solar"
→ norm_type(): "Solar"
→ enrich_type_with_mw(): "Solar (300)" [from Application Quantum]
```

**Step 2: Effectiveness Mapping**
```
Effectiveness record matched (GNA ID):
  type_of_project: "Solar"
  solar_mw: 300
  installed_capacity_mw: 300

→ Installed/Break-up Capacity (MW) Solar = 300 + 0 = 300
```

**Step 3: DTBC Final Type**
```
Components from Type keywords: {"Solar"}
Components from capacity: {"Solar"} (Solar capacity > 0)
→ Final Type: "Solar"
```

---

### **Example 2: Hybrid + BESS with Effectiveness**

**Step 1: CMETS Extraction**
```
PDF Table: "Type: Hybrid+BESS"
→ LLM extracts: "Hybrid+BESS"
→ norm_type(): "Hybrid+BESS"
→ enrich_type_with_mw(): "Hybrid+BESS" (no MW values yet)
```

**Step 2: Effectiveness Mapping**
```
Effectiveness record matched:
  type_of_project: "Hybrid+ESS"
  solar_mw: 200
  wind_mw: 150
  ess_mw: 50

CMETS Type parsed MW: {} (none in this example)

→ Installed/Break-up Capacity (MW) Solar = 200
→ Installed/Break-up Capacity (MW) Wind = 150
→ Installed/Break-up Capacity (MW) Hybrid = 200 + 150 + 50 = 400
→ Installed capacity (MW) ess = 50
```

**Step 3: DTBC Final Type**
```
Components from Type keywords: {"Solar", "Wind", "BESS"} (from "Hybrid+BESS")
Components from capacity: {"Solar", "Wind", "BESS"} (all have values > 0)

Combined: {"Solar", "Wind", "BESS"}
→ components_to_type(): "Hybrid+BESS"
```

---

### **Example 3: Type Updated by Capacity Evidence**

**Step 1: CMETS Extraction**
```
PDF Table: "Type: Solar"
→ LLM extracts: "Solar"
→ norm_type(): "Solar"
```

**Step 2: Effectiveness Mapping**
```
Effectiveness record matched:
  type_of_project: "Solar+ESS"
  solar_mw: 300
  ess_mw: 50

→ Installed/Break-up Capacity (MW) Solar = 300
→ Installed capacity (MW) ess = 50

CMETS also has:
→ Battery Injection (MW) = 50
```

**Step 3: DTBC Final Type**
```
Components from Type keywords: {"Solar"}
Components from capacity: {"Solar", "BESS"} (both have values > 0)

Combined: {"Solar", "BESS"}
→ components_to_type(): "Solar+BESS"

Final Type: "Solar+BESS" (upgraded from "Solar" based on capacity evidence!)
```

---

## **KEY RULES SUMMARY**

### **Type Normalization:**
1. Components are detected from **keywords** AND **MW values** in text
2. Canonical format: `Solar`, `Wind`, `Hybrid`, `Solar+BESS`, `Hybrid+BESS`, etc.
3. Component order: Solar → Wind → Hydro → PSP → BESS

### **Effectiveness Integration:**
1. Effectiveness `type_of_project` does **NOT** overwrite CMETS Type directly
2. Instead, effectiveness MW values populate **Installed/Break-up Capacity** columns
3. CMETS Type MW values (if present) are **added** to effectiveness MW values

### **DTBC Final Resolution:**
1. Type is **recalculated** from capacity evidence columns
2. **Capacity wins:** If capacity columns show BESS but Type doesn't, BESS is added
3. **Nature helps:** If Nature = "Hybrid" and Solar+Wind capacity exist → "Hybrid"
4. Special cases handled via hardcoded application IDs

### **MW Parsing Priority:**
1. Explicit labels: `"Solar: 100 MW"` (highest priority)
2. Trailing qualifiers: `"100 MW (Solar)"`
3. Legacy compact: `"100(Solar)"`
4. BESS with duration: `"300 (BESS 4hr)"` → extracts MW and duration separately

---

## **FILES INVOLVED**

### **CMETS Extraction & Normalization:**
- `pipeline/cmets_handler/extraction.py` - LLM extraction
- `pipeline/cmets_handler/normalization.py` - Type parsing, normalization, enrichment
  - `norm_type()` - Normalize to canonical format
  - `parse_type_capacity()` - Extract MW values
  - `enrich_type_with_mw()` - Add MW from other columns
  - `components_to_type()` - Convert components to string

### **Effectiveness:**
- `pipeline/effectiveness_handler/extraction.py` - Extract type_of_project and MW columns
- `pipeline/effectiveness_handler/capacity_calculator.py` - Compute installed breakdown

### **Final Mapping:**
- `pipeline/final_mapping_handler/runner.py` - Effectiveness → CMETS mapping
  - `_step1_effectiveness_mapping()` - Main mapping function
  - `_set_installed_breakdown_from_type()` - Capacity calculation

### **DTBC Generation:**
- `pipeline/final_mapping_handler/data_capture.py` - Final Type resolution
  - `_resolve_type()` - Recalculate Type from all capacity evidence

### **Shared Utilities:**
- `pipeline/shared_utils.py` - Shared parsing functions
  - `parse_type_capacity()` - MW extraction
  - `classify_project_type()` - Category detection
  - `components_to_type()` - Canonical string conversion
  - `normalize_type()` - Full normalization pipeline

---

## **COLUMN RELATIONSHIPS**

```
CMETS Type Column
    ↓ (parsed for MW values)
Installed/Break-up Capacity (MW) Solar/Wind/Hybrid/Hydro
    ↓ (summed with effectiveness MW)
    + Effectiveness solar_mw/wind_mw/ess_mw/hydro_mw
    ↓ (capacity evidence collected)
DTBC Final Type
    (recalculated from all capacity columns)
```

**Bottom Line:** The Type column evolves from a simple keyword extraction to a comprehensive capacity-evidenced classification across the entire pipeline.
