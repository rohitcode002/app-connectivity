"""
pipeline/shared_utils.py
========================
Centralized utility functions shared across multiple handlers
(CMETS, Effectiveness, Mapping) to reduce redundant logic.
"""

from __future__ import annotations

import json
import re
from typing import Optional
import pandas as pd

def parse_json(text: str) -> dict | list:
    """Safely parse JSON from a raw LLM response string."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}

def safe_str(val) -> str:
    """Convert a value to a clean string, handling None/NaN."""
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    return str(val).strip()

def safe_float(val) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return 0.0 if pd.isna(val) else float(val)
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0

def find_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """Return the first matching column name (case-insensitive) from a DataFrame."""
    for c in candidates:
        for col in df.columns:
            if c.lower() == col.lower():
                return col
    return None

def ids_from_cell(cell_val) -> list[str]:
    """Split a cell value into individual application IDs."""
    raw = safe_str(cell_val)
    if not raw:
        return []
    return [x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()]

def lookup_first(ids: list[str], lookup: dict) -> Optional[dict]:
    """Return the first matching record from *lookup* for any ID in *ids*."""
    for id_ in ids:
        if id_ in lookup:
            return lookup[id_]
    return None

def classify_project_type(type_str: Optional[str]) -> set[str]:
    """Classify a project type string into normalized categories (solar, wind, ess, hydro, hybrid)."""
    text = safe_str(type_str).lower()
    if not text:
        return set()

    cats: set[str] = set()
    if "solar"  in text: cats.add("solar")
    if "wind"   in text: cats.add("wind")
    if "ess"    in text or "energy storage" in text or "bess" in text: cats.add("ess")
    if "hydro"  in text or "pump" in text or "psp" in text: cats.add("hydro")
    if "hybrid" in text: cats.add("hybrid")

    return cats


_COMPONENT_CANON: dict[str, str] = {
    "solar": "Solar",
    "wind": "Wind",
    "bess": "BESS",
    "ess": "BESS",
    "battery": "BESS",
    "battery energy storage": "BESS",
    "hybrid": "Hybrid",
    "hydro": "Hydro",
    "hydel": "Hydro",
    "psp": "PSP",
    "pump storage": "PSP",
    "pumped storage": "PSP",
}

_COMPONENT_LABEL_RE = re.compile(
    r"battery\s+energy\s+storage|pump(?:ed)?\s+storage|solar|wind|bess|ess|"
    r"battery|hybrid|hydro|hydel|psp",
    re.IGNORECASE,
)
_NUMBER_RE = r"(\d+(?:,\d{3})*(?:\.\d+)?)"
_DURATION_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*[-\s]?(?:hours?|hrs?|hr|h)\b",
    re.IGNORECASE,
)


def _component_label(raw: str) -> str | None:
    key = re.sub(r"\s+", " ", raw.lower()).strip()
    return _COMPONENT_CANON.get(key)


def _capacity_value(raw: str) -> float:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_bess_duration_hours(v: Optional[str]) -> float:
    """Return BESS duration hours from text such as ``BESS (300, 4hr)``."""
    text = safe_str(v)
    if not text or not re.search(r"\bbess\b|\bbattery\b", text, re.IGNORECASE):
        return 0.0

    bess_match = re.search(r"\bbess\b|\bbattery\b", text, re.IGNORECASE)
    if bess_match:
        start = max(0, bess_match.start() - 80)
        end = min(len(text), bess_match.end() + 80)
        nearby = text[start:end]
        duration_match = _DURATION_RE.search(nearby)
        if duration_match:
            return _capacity_value(duration_match.group(1))

    duration_match = _DURATION_RE.search(text)
    return _capacity_value(duration_match.group(1)) if duration_match else 0.0


def parse_type_capacity(v: Optional[str]) -> dict[str, float]:
    """Parse component MW breakups from a CMETS Type/capacity cell."""
    text = safe_str(v)
    if not text:
        return {}

    buckets: dict[str, float] = {}

    def add(raw_label: str, raw_value: str) -> None:
        component = _component_label(raw_label)
        value = _capacity_value(raw_value)
        if component and value > 0:
            buckets[component] = buckets.get(component, 0.0) + value

    label_pat = _COMPONENT_LABEL_RE.pattern
    explicit = re.compile(
        rf"\b({label_pat})\b\s*(?:[:\-–—]|\()\s*{_NUMBER_RE}\s*(?:MW)?\)?",
        re.IGNORECASE,
    )
    trailing = re.compile(
        rf"{_NUMBER_RE}\s*MW\s*\(\s*({label_pat})\s*\)",
        re.IGNORECASE,
    )
    legacy = re.compile(
        rf"{_NUMBER_RE}\s*\(\s*({label_pat})\s*\)",
        re.IGNORECASE,
    )
    bess_duration = re.compile(
        rf"{_NUMBER_RE}\s*(?:MW\s*)?\(\s*(BESS|battery)\s*[-,]?\s*"
        rf"\d+(?:\.\d+)?\s*(?:hours?|hrs?|hr|h)\s*\)",
        re.IGNORECASE,
    )

    for match in explicit.finditer(text):
        if (
            _component_label(match.group(1)) == "BESS"
            and re.match(r"\s*(?:hours?|hrs?|hr|h)\b", text[match.end():], re.IGNORECASE)
        ):
            continue
        add(match.group(1), match.group(2))
    for match in trailing.finditer(text):
        add(match.group(2), match.group(1))
    for match in legacy.finditer(text):
        add(match.group(2), match.group(1))
    for match in bess_duration.finditer(text):
        add(match.group(2), match.group(1))

    return buckets


def components_from_type_keywords(v: Optional[str]) -> set[str]:
    """Detect canonical components from Type/project-type wording."""
    text = safe_str(v)
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
    """Convert parsed components to the canonical Type string."""
    if isinstance(components, dict):
        component_set = {name for name, value in components.items() if _capacity_value(value) > 0}
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


def normalize_type(v: Optional[str]) -> Optional[str]:
    """Normalize a Type text value through component parsing."""
    text = safe_str(v)
    if not text:
        return None
    components = set(parse_type_capacity(text)) | components_from_type_keywords(text)
    return components_to_type(components)
