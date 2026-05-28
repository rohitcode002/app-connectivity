"""
cmets_handler/normalization.py — Row validation & normalisation
================================================================
Post-LLM cleaning: value coercion, state extraction, date parsing,
deduplication, and Pydantic model construction.

Each column's normalisation function is referenced by name in
column_registry.py.  The mapping from name → callable is in
NORM_FUNCTIONS at the bottom of this file.

Edit this file to change how extracted raw values are cleaned /
normalised before being written to JSON and Excel.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from pipeline.cmets_handler.models import MappedRow
from pipeline.cmets_handler.column_registry import (
    COLUMN_DEFS,
    get_llm_key_to_column_map,
)
from pipeline.cmets_handler.battery_extractor import extract_battery_values

# ── Indian states / UTs ──────────────────────────────────────────────────────
INDIA_STATES_UTS = [
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana",
    "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "andaman and nicobar islands", "chandigarh",
    "dadra and nagar haveli and daman and diu", "delhi",
    "jammu and kashmir", "ladakh", "lakshadweep", "puducherry",
]


# ═══════════════════════════════════════════════════════════════════════════════
# NORM FUNCTIONS — each column references one of these by name
# ═══════════════════════════════════════════════════════════════════════════════

# ── clean ────────────────────────────────────────────────────────────────────
def clean(v: Optional[str]) -> Optional[str]:
    """Basic cleaning: strip whitespace, convert null-like strings to None."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in {"null", "none", "na", "n/a", "-", "--"} else (v or None)


_ROMAN_VALUES = {
    "i": "I",
    "ii": "II",
    "iii": "III",
    "iv": "IV",
    "v": "V",
    "vi": "VI",
    "vii": "VII",
    "viii": "VIII",
    "ix": "IX",
    "x": "X",
}

_SUBSTATION_NOISE_RE = re.compile(
    r"\b("
    r"schedule|commissioning|implementation|informed|applicant|developer|"
    r"connectivity|granted|grant|generation|generating|injection|quantum|"
    r"remarks?|deliberation|agenda|minutes?|application|applied|route|scope"
    r")\b",
    re.IGNORECASE,
)

_STATION_MARKER_RE = re.compile(
    r"(?:\s*\(?\b(?:PS|SS|GSS|S/S|S\.S\.|S\s*/\s*S)\b\.?\)?)+\s*$",
    re.IGNORECASE,
)


def _normalize_station_roman(text: str) -> str:
    """Upper-case roman station suffixes while leaving normal words alone."""
    def repl(match: re.Match) -> str:
        prefix, roman = match.groups()
        return f"{prefix}{_ROMAN_VALUES.get(roman.lower(), roman.upper())}"

    return re.sub(r"(-\s*)(i{1,3}|iv|v|vi{0,3}|ix|x)\b", repl, text, flags=re.IGNORECASE)


def _strip_station_markers(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _STATION_MARKER_RE.sub("", text).strip()
    return text


def _looks_like_station_name(text: str) -> bool:
    if not text or not re.search(r"[A-Za-z]", text):
        return False
    lowered = text.lower().strip()
    if lowered in {"hvdc", "pg", "pgcil", "sec", "section"}:
        return False
    if _SUBSTATION_NOISE_RE.search(text) and not re.search(r"\b(?:bay|bays)\s+at\b|\bpooling\s+station\b", text, re.IGNORECASE):
        return False
    return True


def _station_specificity(text: str) -> int:
    score = 0
    if re.search(r"-\s*(?:i{1,3}|iv|v|vi{0,3}|ix|x|\d+)\b", text, re.IGNORECASE):
        score += 4
    if re.search(r"\b(?:PS|SS|GSS|S/S|S\.S\.|pooling\s+station)\b", text, re.IGNORECASE):
        score += 2
    if re.search(r"\b(?:HVDC|PG|PGCIL|BBMB)\b", text, re.IGNORECASE):
        score += 1
    return score


def _add_default_station_index(text: str) -> str:
    if re.search(r"-\s*(?:[IVX]+|\d+)\b", text, re.IGNORECASE):
        return text
    if re.search(r"[(),;:]|\b(?:PG|PGCIL|BBMB|HVDC)\b", text, re.IGNORECASE):
        return text
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", text):
        return text
    return f"{text}-I"


def _clean_substation_candidate(text: str, *, add_default_index: bool = True) -> Optional[str]:
    text = clean(text)
    if not text:
        return None

    text = re.sub(r"\b\d{2,4}\s*k\s*v\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{2,4}\s*kv\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\bBays?\s+at\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:nearest\s+)?pooling\s+station\s*(?:at|is|:|-)?\s*", "", text, flags=re.IGNORECASE)
    text = re.split(r"\s*/\s*(?!\s*S\b)", text, maxsplit=1)[0]
    text = re.sub(r"\((?:sec(?:tion)?|ckt|circuit)[^)]*\)", "", text, flags=re.IGNORECASE)
    text = _strip_station_markers(text)
    text = _normalize_station_roman(text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,;)])", r"\1", text)
    text = re.sub(r"([(,;])\s+", r"\1", text)
    text = text.strip(" -;,")

    if not _looks_like_station_name(text):
        return None
    if add_default_index:
        text = _add_default_station_index(text)
    return text or None


def norm_substation(v: Optional[str]) -> Optional[str]:
    """Normalise CMETS substation names for matching and final output."""
    raw = clean(v)
    if not raw:
        return None

    parenthetical_candidates = [
        candidate
        for candidate in re.findall(r"\(([^()]*)\)", raw)
        if _looks_like_station_name(candidate)
    ]
    outer = re.sub(r"\([^()]*\)", " ", raw)
    best_raw = raw
    best_score = _station_specificity(outer)

    for candidate in parenthetical_candidates:
        score = _station_specificity(candidate)
        if score > best_score:
            best_raw = candidate
            best_score = score

    # Sentence fragments sometimes contain a useful "bay at <station>" tail.
    tail_match = re.search(
        r"\b(?:bay|bays)\s+at\s+([A-Za-z][A-Za-z .'-]*(?:-\s*(?:[IVX]+|\d+))?)",
        raw,
        flags=re.IGNORECASE,
    )
    if tail_match and best_raw == raw:
        best_raw = tail_match.group(1)

    cleaned = _clean_substation_candidate(best_raw)
    if cleaned:
        return cleaned

    if best_raw != outer:
        return _clean_substation_candidate(outer)
    return None


# ── extract_state ────────────────────────────────────────────────────────────
def extract_state(loc: Optional[str]) -> Optional[str]:
    """Derive Indian state/UT name from Project Location text."""
    loc = clean(loc)
    if not loc:
        return None
    lower = loc.lower()
    for state in sorted(INDIA_STATES_UTS, key=len, reverse=True):
        if state in lower:
            return state
    if "," in loc:
        tail = loc.split(",")[-1].strip(" .")
        return tail.lower() or None
    return None


# ── norm_num_ids ─────────────────────────────────────────────────────────────
def norm_num_ids(v: Optional[str], strip_zeros: bool = False) -> Optional[str]:
    """Normalise numeric application ID (6+ digit number).

    Returns only the FIRST matching ID — each ID column must hold
    at most one single value.
    """
    v = clean(v)
    if not v:
        return None
    ids = re.findall(r"\b\d{6,}\b", v)
    if not ids:
        return v
    first = ids[0]
    return (first.lstrip("0") or "0") if strip_zeros else first


def norm_num_ids_strip(v: Optional[str]) -> Optional[str]:
    """Normalise numeric IDs with leading-zero stripping (for LTA IDs)."""
    return norm_num_ids(v, strip_zeros=True)


# ── extract_ids ──────────────────────────────────────────────────────────────
def extract_ids(v: Optional[str]) -> list[str]:
    """Extract all 6+ digit IDs from a string."""
    v = clean(v)
    return re.findall(r"\b\d{6,}\b", v) if v else []


def _is_lta(v: str) -> bool:
    return str(v).startswith("04")


def _has_ctx(pattern: str, *vals: Optional[str]) -> bool:
    text = " ".join(str(x or "") for x in vals)
    return bool(re.search(pattern, text, re.IGNORECASE))


def _pick_gna(ids: list[str], prefer_st2: bool) -> Optional[str]:
    if not ids:
        return None
    if prefer_st2:
        return next((i for i in ids if not _is_lta(i)), None)
    non_lta = [i for i in ids if not _is_lta(i)]
    return non_lta[0] if non_lta else ids[0]


# ── derive_enhancement_id ────────────────────────────────────────────────────
def derive_enhancement_id(enh, gna, lta, mode) -> Optional[str]:
    """Derive Enhancement 5.2 application ID from context fields."""
    explicit_enh_ids = extract_ids(enh)
    if explicit_enh_ids:
        return _pick_gna(explicit_enh_ids, prefer_st2=True)
    if not _has_ctx(r"\b(5\.?2|regulation\s*5\.?2|enhancement|revision)\b", enh, gna, lta, mode):
        return None
    st2 = _has_ctx(r"\b(stage\s*ii|st\s*ii|gna/st\s*ii)\b", enh, gna, lta, mode)
    for ids in (extract_ids(enh), extract_ids(gna)):
        c = _pick_gna(ids, st2)
        if c:
            return c
    lta_ids = extract_ids(lta)
    if len(lta_ids) == 1:
        return None if _is_lta(lta_ids[0]) else lta_ids[0]
    return _pick_gna(lta_ids, st2)


# ── extract_date ─────────────────────────────────────────────────────────────
def _parse_dates(v: Optional[str]) -> list[datetime]:
    """Parse all supported dates from text."""
    v = clean(v)
    if not v:
        return []

    parsed: list[datetime] = []

    for match in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", v):
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            parsed.append(datetime(int(year), int(month), int(day)))
        except ValueError:
            pass

    for match in re.finditer(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", v):
        year, month, day = match.groups()
        try:
            parsed.append(datetime(int(year), int(month), int(day)))
        except ValueError:
            pass

    for match in re.finditer(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b", v):
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                parsed.append(datetime.strptime(match.group(0), fmt))
                break
            except ValueError:
                pass

    return parsed


def extract_date(v: Optional[str]) -> Optional[str]:
    """Extract, normalise, and return the latest date as DD.MM.YYYY."""
    dates = _parse_dates(v)
    if not dates:
        return None
    return max(dates).strftime("%d.%m.%Y")


# ── gna_yes_no ───────────────────────────────────────────────────────────────
def gna_yes_no(date_str: Optional[str]) -> Optional[str]:
    """Determine Yes/No based on whether GNA date is operationalized."""
    d = extract_date(date_str)
    if not d:
        return None
    dt = datetime.strptime(d, "%d.%m.%Y")
    return "Yes" if dt.date() <= datetime.now().date() else "No"


# ── norm_status ──────────────────────────────────────────────────────────────
def norm_status(v: Optional[str]) -> Optional[str]:
    """Normalise application status to canonical values."""
    v = clean(v)
    if not v:
        return "Applied"
    lower = v.lower()
    if "withdraw" in lower:
        return "Withdrawn"
    if any(word in lower for word in ("revoke", "cancel", "reject")):
        return "Revoked"
    if "grant" in lower or "approved" in lower:
        return "granted"
    return "Applied"


# ── PSP columns ─────────────────────────────────────────────────────────────

_PSP_DETECT_RE = re.compile(
    r"\b(pump\s*(?:ed)?\s*storage|psp)\b", re.IGNORECASE
)

_PSP_INJ_RE = re.compile(
    r"(?:max(?:imum)?\s*)?injection\s*[:\-]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PSP_DRW_RE = re.compile(
    r"(?:max(?:imum)?\s*)?(?:drawl|drawal)\s*[:\-]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PSP_MWH_RE_1 = re.compile(
    r"for\s+(\d+(?:\.\d+)?)\s*(?:MWh|MW)", re.IGNORECASE,
)
_PSP_MWH_RE_2 = re.compile(
    r"(\d+(?:\.\d+)?)\s*MWh", re.IGNORECASE,
)


def psp_cols(
    *vals: Optional[str],
    row_context: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract PSP MWh/Injection/Drawl from combined text.

    Detection checks both the direct PSP fields AND the broader
    row_context (Nature of Applicant, Type, quantum, etc.) for
    keywords like "Pumped Storage", "PSP", "pump storage".
    """
    # Narrow text from direct PSP fields
    narrow = " ".join(str(v or "") for v in vals)
    # Broader context for PSP keyword detection
    full = narrow + (" " + row_context if row_context else "")

    if not _PSP_DETECT_RE.search(full):
        return None, None, None

    # Parse values from the full context so we catch "Max Injection: 100"
    # even if it appears in nature/type/quantum columns
    mwh_m   = _PSP_MWH_RE_1.search(full) or _PSP_MWH_RE_2.search(full)
    inj_m   = _PSP_INJ_RE.search(full)
    drawl_m = _PSP_DRW_RE.search(full)
    return (mwh_m.group(1) if mwh_m else None,
            inj_m.group(1) if inj_m else None,
            drawl_m.group(1) if drawl_m else None)


def norm_psp_mwh(v: Optional[str]) -> Optional[str]:
    """Normalise PSP MWh value."""
    return clean(v)


def norm_psp_injection(v: Optional[str]) -> Optional[str]:
    """Normalise PSP Injection (MW) value."""
    return clean(v)


def norm_psp_drawl(v: Optional[str]) -> Optional[str]:
    """Normalise PSP Drawl (MW) value."""
    return clean(v)


# ── Battery (BESS) columns ──────────────────────────────────────────────────

def norm_battery_mwh(v: Optional[str]) -> Optional[str]:
    """Normalise Battery MWh value."""
    return clean(v)


def norm_battery_injection(v: Optional[str]) -> Optional[str]:
    """Normalise Battery Injection (MW) value."""
    return clean(v)


def norm_battery_drawl(v: Optional[str]) -> Optional[str]:
    """Normalise Battery Drawl (MW) value."""
    return clean(v)


# ── norm_dev ─────────────────────────────────────────────────────────────────
def norm_dev(v: Optional[str]) -> Optional[str]:
    """Clean developer name: remove LOA/CRITERION artefacts."""
    v = clean(v)
    if not v:
        return None
    return None if any(t in v.upper() for t in (" LOA", "CRITERION", "APPLYING")) else v


# ── norm_mode_criteria ───────────────────────────────────────────────────────
def norm_mode_criteria(v: Optional[str]) -> Optional[str]:
    """Normalise criteria/mode values to the two table output buckets."""
    v = clean(v)
    if not v:
        return None
    if re.search(r"(?:LOA|PPA)", v, re.IGNORECASE):
        return "LOA or PPA"
    return "Land BG"


# ── norm_type ────────────────────────────────────────────────────────────────

_COMPONENT_CANON: dict[str, str] = {
    "solar": "Solar",
    "wind": "Wind",
    "bess": "BESS",
    "ess": "BESS",
    "battery": "BESS",
    "battery energy storage": "BESS",
    "hydro": "Hydro",
    "hydel": "Hydro",
    "psp": "PSP",
    "pump storage": "PSP",
    "pumped storage": "PSP",
}

_COMPONENT_LABEL_RE = re.compile(
    r"battery\s+energy\s+storage|pump(?:ed)?\s+storage|solar|wind|bess|ess|"
    r"battery|hydro|hydel|psp",
    re.IGNORECASE,
)
_NUMBER_RE = r"(\d+(?:,\d{3})*(?:\.\d+)?)"


def _component_label(raw: str) -> str | None:
    key = re.sub(r"\s+", " ", raw.lower()).strip()
    return _COMPONENT_CANON.get(key)


def _capacity_value(raw: str) -> float:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_type_capacity(v: Optional[str]) -> dict[str, float]:
    """Parse component MW breakups from a raw CMETS Type/capacity cell.

    The parser prefers explicit component labels, then bracketed suffixes,
    then legacy compact forms such as ``100(Solar)``. Only positive MW values
    are added to the returned component bucket.
    """
    text = clean(v)
    if not text:
        return {}

    buckets: dict[str, float] = {}

    def add(raw_label: str, raw_value: str) -> None:
        component = _component_label(raw_label)
        value = _capacity_value(raw_value)
        if component and value > 0:
            buckets[component] = buckets.get(component, 0.0) + value

    label_pat = _COMPONENT_LABEL_RE.pattern

    # Explicit label first: "Solar: 100 MW", "BESS - 50 MW", "Wind (12)".
    explicit = re.compile(
        rf"\b({label_pat})\b\s*(?:[:\-–—]|\()\s*{_NUMBER_RE}\s*(?:MW)?\)?",
        re.IGNORECASE,
    )
    for match in explicit.finditer(text):
        add(match.group(1), match.group(2))

    # Trailing qualifier: "100 MW (Solar)".
    trailing = re.compile(
        rf"{_NUMBER_RE}\s*MW\s*\(\s*({label_pat})\s*\)",
        re.IGNORECASE,
    )
    for match in trailing.finditer(text):
        add(match.group(2), match.group(1))

    # Legacy compact fallback: "100(Solar)".
    legacy = re.compile(
        rf"{_NUMBER_RE}\s*\(\s*({label_pat})\s*\)",
        re.IGNORECASE,
    )
    for match in legacy.finditer(text):
        add(match.group(2), match.group(1))

    return buckets


def _components_from_keywords(v: Optional[str]) -> set[str]:
    text = clean(v)
    if not text:
        return set()

    components: set[str] = set()
    for match in _COMPONENT_LABEL_RE.finditer(text):
        component = _component_label(match.group(0))
        if component:
            components.add(component)

    if re.search(r"\bhybrid\b", text, re.IGNORECASE):
        components.update({"Solar", "Wind"})

    return components


def components_to_type(components: set[str] | list[str] | tuple[str, ...] | dict[str, float]) -> Optional[str]:
    """Convert parsed components to the canonical CMETS Type string."""
    if isinstance(components, dict):
        component_set = {name for name, value in components.items() if _capacity_value(str(value)) > 0}
    else:
        component_set = {str(name) for name in components if str(name)}

    if not component_set:
        return None

    if {"Solar", "Wind", "BESS"}.issubset(component_set):
        return "Hybrid+BESS"
    if {"Solar", "Wind"}.issubset(component_set):
        return "Hybrid"
    if component_set == {"Solar", "BESS"}:
        return "Solar+BESS"
    if component_set == {"Wind", "BESS"}:
        return "Wind+BESS"
    if component_set == {"Hydro", "BESS"}:
        return "Hydro+BESS"
    if component_set == {"PSP"}:
        return "PSP"
    if len(component_set) == 1:
        return next(iter(component_set))

    order = ["Solar", "Wind", "Hydro", "PSP", "BESS"]
    return "+".join(component for component in order if component in component_set)


def norm_type(v: Optional[str]) -> Optional[str]:
    """Normalise the Type column to the canonical component combination."""
    v = clean(v)
    if not v:
        return None

    components = set(parse_type_capacity(v)) | _components_from_keywords(v)
    return components_to_type(components)


def enrich_type_with_mw(row: dict) -> Optional[str]:
    """Build a Type string with MW values from the row context.

    Combines the raw LLM-extracted Type value with capacity evidence from
    the row's other columns (Application Quantum, Battery, PSP, etc.).

    If the LLM Type already has MW values (e.g. "Solar (300)"), they're
    preserved.  If only bare keywords (e.g. "Solar"), MW values are
    looked up from Application Quantum and capacity columns.

    Output format: "Solar (52) + BESS (6.88)"
    """
    raw_type = clean(row.get("Type"))
    if not raw_type:
        return None

    # ── Step 1: Parse any MW values already in the Type string ───────────
    existing_buckets = parse_type_capacity(raw_type)

    # ── Step 2: Detect component keywords present in the Type string ────
    keywords = _components_from_keywords(raw_type)

    # ── Step 3: Enrich from capacity columns if MW values are missing ────
    # Map component → candidate columns to check for MW values
    _CAPACITY_SOURCES: dict[str, list[str]] = {
        "BESS": [
            "Battery MWh",
            "Battery Injection (MW)",
            "Battery Drawl (MW)",
        ],
        "PSP": [
            "PSP MWh",
            "PSP Injection (MW)",
            "PSP Drawl (MW)",
        ],
    }

    # For BESS / PSP, try to pull MW from their dedicated columns
    for comp, src_cols in _CAPACITY_SOURCES.items():
        if comp in keywords and comp not in existing_buckets:
            for col in src_cols:
                val = _capacity_value(str(row.get(col, "")))
                if val > 0:
                    existing_buckets[comp] = val
                    break

    # If a single Application Quantum value exists and there's exactly one
    # non-BESS/PSP component without a value, assign it
    app_quantum = _capacity_value(
        str(row.get("Application Quantum (MW)(ST II)", ""))
    )
    primary_components = keywords - {"BESS", "PSP"}
    missing_primary = [c for c in primary_components if c not in existing_buckets]

    if app_quantum > 0 and len(missing_primary) == 1:
        existing_buckets[missing_primary[0]] = app_quantum
    elif app_quantum > 0 and len(primary_components) == 1 and not missing_primary:
        # Already has a value — keep it
        pass

    # ── Step 4: Build the final Type string ──────────────────────────────
    order = ["Solar", "Wind", "Hydro", "BESS", "PSP"]
    all_components = set(existing_buckets.keys()) | keywords

    parts: list[str] = []
    for comp in order:
        if comp not in all_components:
            continue
        mw = existing_buckets.get(comp)
        if mw and mw > 0:
            # Format: remove trailing .0 for whole numbers
            mw_str = f"{mw:g}"
            parts.append(f"{comp} ({mw_str})")
        else:
            parts.append(comp)

    if not parts:
        return raw_type  # fallback to raw value

    return " + ".join(parts)


# ── norm_voltage ─────────────────────────────────────────────────────────────
def norm_voltage(v: Optional[str]) -> Optional[str]:
    """Normalise voltage string to '<N> kV' format."""
    v = clean(v)
    if not v:
        return None
    m = re.search(r"(\d{2,3})\s*kV", v, re.IGNORECASE)
    if m:
        return f"{m.group(1)} kV"
    return v


# ═══════════════════════════════════════════════════════════════════════════════
# NORM FUNCTION REGISTRY — maps function name (str) → callable
# ═══════════════════════════════════════════════════════════════════════════════
# Each column in column_registry.py references a norm_func by NAME.
# This dict resolves those names to actual functions.

NORM_FUNCTIONS: dict[str, callable] = {
    "clean":                 clean,
    "extract_state":         extract_state,
    "norm_num_ids":          norm_num_ids,
    "norm_num_ids_strip":    norm_num_ids_strip,
    "derive_enhancement_id": derive_enhancement_id,
    "extract_date":          extract_date,
    "gna_yes_no":            gna_yes_no,
    "norm_status":           norm_status,
    "norm_substation":       norm_substation,
    "norm_dev":              norm_dev,
    "norm_mode_criteria":    norm_mode_criteria,
    "norm_type":             norm_type,
    "norm_voltage":          norm_voltage,
    "norm_psp_mwh":          norm_psp_mwh,
    "norm_psp_injection":    norm_psp_injection,
    "norm_psp_drawl":        norm_psp_drawl,
    "norm_battery_mwh":      norm_battery_mwh,
    "norm_battery_injection": norm_battery_injection,
    "norm_battery_drawl":    norm_battery_drawl,
}


# ── Row-level blocklist values for Nature of Applicant ────────────────────────
# Rows with these values are skipped entirely (not connectivity generators).
_NATURE_BLOCKLIST = [
    "bulk consumer",
    "drawee entity",
    "drawee entity connected",
]


def _has_any_primary_key(row: dict) -> bool:
    """Return True if the row has at least one primary ID field.

    Primary keys: GNA/ST II Application ID, LTA Application ID,
    or Application ID under Enhancement 5.2 or revision.
    """
    return bool(
        clean(row.get("GNA/ST II Application ID"))
        or clean(row.get("LTA Application ID"))
        or clean(row.get("Application ID under Enhancement 5.2 or revision"))
    )


def _is_nature_blocklisted(row: dict) -> bool:
    """Return True if the Nature of Applicant value is in the blocklist."""
    nature = clean(row.get("Nature of Applicant"))
    if not nature:
        return False
    lower = nature.lower()
    return any(blocked in lower for blocked in _NATURE_BLOCKLIST)


def remap_llm_keys(row: dict) -> dict:
    """Remap LLM response keys to final column names using the registry.

    The LLM returns keys like 'substaion', 'type', 'Voltage' etc.
    This function maps them to canonical column names like 'Substation',
    'Type', 'Voltage level'.
    """
    key_map = get_llm_key_to_column_map()
    remapped = {}
    for k, v in row.items():
        final_col = key_map.get(k, k)  # map to final name, or keep as-is
        remapped[final_col] = v
    return remapped


def validate_rows(raw_rows: list[dict]) -> list[MappedRow]:
    """Filter raw dicts → valid MappedRow objects.

    A row must have at least ONE primary key ID (GNA/ST-II, LTA,
    or Enhancement 5.2) and must NOT have a blocklisted Nature of
    Applicant value.
    """
    out = []
    for row in raw_rows:
        # Remap LLM keys → final column names
        row = remap_llm_keys(row)

        # Primary key check: need at least one ID
        if not _has_any_primary_key(row):
            continue
        # Nature of Applicant blocklist
        if _is_nature_blocklisted(row):
            print(f"      [SKIP] Blocklisted Nature of Applicant: {row.get('Nature of Applicant')}")
            continue
        try:
            out.append(MappedRow.model_validate(row))
        except Exception as e:
            print(f"      [Pydantic skip] {e}")
    return out


def normalize(rows: list[MappedRow]) -> list[MappedRow]:
    """Apply full normalisation to a list of validated rows.

    Each column is normalised according to its norm_func defined in
    column_registry.py.  Special handling for multi-field derived
    columns (Enhancement ID, PSP, Battery, State, GNA Yes/No).
    """
    out = []
    for row in rows:
        p = row.model_dump(by_alias=True)

        # ── Raw values needed for multi-field derivations ─────────────────
        raw_gna  = p.get("GNA/ST II Application ID")
        raw_lta  = p.get("LTA Application ID")
        raw_mode = p.get("Mode(Criteria for applying)")
        raw_enh  = p.get("Application ID under Enhancement 5.2 or revision")
        raw_opd  = p.get("GNA Operationalization Date")
        raw_stat = p.get("Status of application(Withdrawn / granted. Revoked.)")

        # ── PSP raw values ────────────────────────────────────────────────
        raw_psp_mwh = p.get("PSP MWh")
        raw_psp_inj = p.get("PSP Injection (MW)")
        raw_psp_drw = p.get("PSP Drawl (MW)")

        # ── Battery raw values ────────────────────────────────────────────
        raw_bat_mwh = p.get("Battery MWh")
        raw_bat_inj = p.get("Battery Injection (MW)")
        raw_bat_drw = p.get("Battery Drawl (MW)")

        # ── Simple single-column normalisations ──────────────────────────
        p["Substation"]                  = norm_substation(p.get("Substation"))
        p["Project Location"]            = clean(p.get("Project Location"))
        p["Name of Developers"]          = norm_dev(p.get("Name of Developers"))
        p["GNA/ST II Application ID"]    = norm_num_ids(raw_gna, strip_zeros=False)
        p["Application Quantum (MW)(ST II)"] = clean(p.get("Application Quantum (MW)(ST II)"))
        p["Mode(Criteria for applying)"] = norm_mode_criteria(p.get("Mode(Criteria for applying)"))
        p["Nature of Applicant"]         = clean(p.get("Nature of Applicant"))

        # ── Primary key check after normalisation ─────────────────────────
        has_gna = bool(clean(p["GNA/ST II Application ID"]))
        p["LTA Application ID"]          = norm_num_ids(raw_lta, strip_zeros=True)
        has_lta = bool(clean(p["LTA Application ID"]))
        p["Application ID under Enhancement 5.2 or revision"] = derive_enhancement_id(
            raw_enh, raw_gna, raw_lta, raw_mode
        )
        has_enh = bool(clean(p["Application ID under Enhancement 5.2 or revision"]))

        if not (has_gna or has_lta or has_enh):
            continue

        # ── Intra-row ID dedup ────────────────────────────────────────────
        # Ensure the same numeric ID does not appear in multiple columns.
        # Priority: GNA > Enhancement 5.2 > LTA. If an ID in LTA matches
        # one already in GNA or Enh 5.2, clear it from LTA (and vice versa).
        gna_ids = _id_tokens(p.get("GNA/ST II Application ID"))
        enh_ids = _id_tokens(p.get("Application ID under Enhancement 5.2 or revision"))
        lta_ids = _id_tokens(p.get("LTA Application ID"))

        # LTA vs GNA/Enh — if LTA ID overlaps with GNA or Enh, clear LTA
        if lta_ids and (lta_ids & gna_ids or lta_ids & enh_ids):
            p["LTA Application ID"] = None
            has_lta = False
            print(f"      [ID Dedup] LTA ID cleared — same ID already in GNA/Enh 5.2")

        # Enh vs GNA — if Enhancement ID overlaps with GNA, clear Enh
        if enh_ids and enh_ids & gna_ids:
            p["Application ID under Enhancement 5.2 or revision"] = None
            has_enh = False
            print(f"      [ID Dedup] Enh 5.2 ID cleared — same ID already in GNA")

        if not (has_gna or has_lta or has_enh):
            continue

        # ── Date columns ─────────────────────────────────────────────────
        p["Application/Submission Date"] = extract_date(p.get("Application/Submission Date"))
        p["Applied Start of Connectivity sought by developer date"
          "( start date of connectivity as per the application)"] = extract_date(
            p.get("Applied Start of Connectivity sought by developer date"
                  "( start date of connectivity as per the application)")
        )
        p["Date from which additional capacity is to be added"] = extract_date(
            p.get("Date from which additional capacity is to be added")
        )
        p["GNA Operationalization Date"] = extract_date(raw_opd)

        # ── Calculated: GNA Yes/No ───────────────────────────────────────
        p["GNA Operationalization (Yes/No)"] = gna_yes_no(p["GNA Operationalization Date"])

        # ── Status ───────────────────────────────────────────────────────
        p["Status of application(Withdrawn / granted. Revoked.)"] = norm_status(raw_stat)

        # ── Calculated: Granted Quantum ──────────────────────────────────
        # If status is "granted" → copy Application Quantum; else empty.
        norm_stat = p["Status of application(Withdrawn / granted. Revoked.)"]
        if norm_stat and norm_stat.lower() == "granted":
            p["Granted Quantum GNA/LTA(MW)"] = clean(p.get("Application Quantum (MW)(ST II)"))
        else:
            p["Granted Quantum GNA/LTA(MW)"] = None

        # ── PSP columns (multi-field derivation) ─────────────────────────
        # Build row context early so both PSP and Battery can use it
        row_context = " ".join(str(v or "") for v in p.values())
        mwh, inj, drw = psp_cols(
            raw_psp_mwh, raw_psp_inj, raw_psp_drw, raw_mode,
            row_context=row_context,
        )
        p["PSP MWh"]            = clean(mwh or raw_psp_mwh)
        p["PSP Injection (MW)"] = clean(inj or raw_psp_inj)
        p["PSP Drawl (MW)"]     = clean(drw or raw_psp_drw)

        # ── Battery (BESS) columns (multi-field derivation) ──────────────
        # row_context already built above for duration pattern matching
        bat_mwh, bat_inj, bat_drw = extract_battery_values(
            raw_bat_mwh, raw_bat_inj, raw_bat_drw, raw_mode,
            p.get("Type", ""),
            row_context=row_context,
        )
        p["Battery MWh"]            = clean(bat_mwh or raw_bat_mwh)
        p["Battery Injection (MW)"] = clean(bat_inj or raw_bat_inj)
        p["Battery Drawl (MW)"]     = clean(bat_drw or raw_bat_drw)

        # ── PSP overrides Battery Injection ───────────────────────────────
        # If PSP values are populated, battery injection is cleared because
        # the injection/drawl belongs to pump storage, not BESS.
        psp_has_values = bool(
            clean(p["PSP Injection (MW)"]) or clean(p["PSP Drawl (MW)"])
        )
        if psp_has_values:
            p["Battery Injection (MW)"] = None
            print(f"      [PSP Override] PSP populated → cleared Battery Injection (MW)")

        # ── Derived: State from Project Location ─────────────────────────
        p["State"] = extract_state(p.get("Project Location"))

        # ── Derived: Type (enriched with MW values from row context) ──────
        # Combines LLM-extracted type keywords with MW values from capacity
        # columns to produce e.g. "Solar (52) + BESS (6.88)".
        p["Type"] = enrich_type_with_mw(p)

        # ── Voltage level ────────────────────────────────────────────────
        p["Voltage level"] = norm_voltage(p.get("Voltage level"))

        out.append(MappedRow.model_validate(p))
    return out


def dedup_dicts(rows: list[dict]) -> list[dict]:
    """Remove duplicate row dicts by composite key."""
    seen: set = set()
    unique: list[dict] = []
    for row in rows:
        gna = str(row.get("GNA/ST II Application ID") or "").strip()
        lta = str(row.get("LTA Application ID") or "").strip()
        loc = str(row.get("Project Location") or "").strip().lower()
        dev = str(row.get("Name of the developers") or row.get("Name of Developers") or "").strip().lower()
        key = (gna, lta, loc, dev) if any([gna, lta, loc, dev]) else json.dumps(row, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


_APPLICATION_ID_COLUMNS = [
    "GNA/ST II Application ID",
    "LTA Application ID",
    "Application ID under Enhancement 5.2 or revision",
]


def _id_tokens(value: object) -> set[str]:
    """Return comparable application ID tokens from a cell value."""
    value = clean(value)
    if not value:
        return set()
    tokens = set()
    for token in re.findall(r"\b\d{6,}\b", str(value)):
        tokens.add(token)
        tokens.add(token.lstrip("0") or "0")
    return tokens


def _row_application_ids(row: dict) -> set[str]:
    """Return all GNA/LTA/5.2 IDs present in a row."""
    ids: set[str] = set()
    for col in _APPLICATION_ID_COLUMNS:
        ids.update(_id_tokens(row.get(col)))
    return ids


def _row_pdf_key(row: dict) -> str:
    """Return the source PDF key used to scope CMETS duplicate removal."""
    return clean(row.get("PDF")) or ""


def _merge_id_cell(existing: object, incoming: object) -> object:
    """Keep a single ID per cell — prefer the existing value.

    Each ID column must hold at most one value. If the existing
    (later) row already has an ID, keep it. Otherwise take the
    incoming (earlier) row's ID.
    """
    existing_clean = clean(existing)
    if existing_clean:
        return existing
    return incoming


def _merge_into_later_row(current: dict, later: dict) -> None:
    """Merge duplicate current row data into the later row.

    Later rows are treated as the newer/updated copy. Existing later values are
    preserved, and only missing later cells are filled from the current row.
    Each ID column keeps at most one value — the later row's ID takes precedence.
    """
    for col in _APPLICATION_ID_COLUMNS:
        merged_ids = _merge_id_cell(later.get(col), current.get(col))
        if clean(merged_ids):
            later[col] = merged_ids

    for col, value in current.items():
        if col in _APPLICATION_ID_COLUMNS:
            continue
        if clean(later.get(col)):
            continue
        cleaned_value = clean(value)
        if cleaned_value:
            later[col] = value


def consolidate_application_duplicates(rows: list[dict]) -> list[dict]:
    """Collapse later duplicate application rows within the same CMETS PDF.

    For each row, check whether any of its GNA/LTA/5.2 application IDs appears
    in a later row from the same PDF. If yes, carry the current row's non-empty
    data into that later row where the later row is blank, then remove the
    current row. This keeps the newest/upcoming row in that PDF while preserving
    useful values from the older duplicate row.
    """
    if not rows:
        return rows

    consolidated = [dict(row) for row in rows]
    removed: set[int] = set()

    for i, current in enumerate(consolidated):
        if i in removed:
            continue
        current_ids = _row_application_ids(current)
        if not current_ids:
            continue
        current_pdf = _row_pdf_key(current)
        if not current_pdf:
            continue

        for j in range(i + 1, len(consolidated)):
            if j in removed:
                continue
            if _row_pdf_key(consolidated[j]) != current_pdf:
                continue
            if current_ids.intersection(_row_application_ids(consolidated[j])):
                _merge_into_later_row(current, consolidated[j])
                removed.add(i)
                break

    return [row for idx, row in enumerate(consolidated) if idx not in removed]
