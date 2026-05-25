"""
cmets_handler/formatter.py — Final CMETS value formatter
========================================================
Creates a cleaned CMETS workbook after extraction/consolidation and before
downstream mapping consumes the CMETS data.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from pipeline.excel_utils import (
    _apply_data_style,
    _apply_header_style,
    _autosize_columns,
    _get_openpyxl,
)
from pipeline.cmets_handler.normalization import INDIA_STATES_UTS, clean


CAPACITY_COLUMNS = [
    "Installed/Break-up Capacity (MW) Solar",
    "Installed/Break-up Capacity (MW) Wind",
    "Installed/Break-up Capacity (MW) Hybrid",
    "Installed/Break-up Capacity (MW) Hydro",
]

REGION_COLUMN = "Region"

_REGION_PATTERNS = [
    (r"\bnorth[\s_-]*eastern[\s_-]*region\b", "NER"),
    (r"\bnorth[\s_-]*east(?:ern)?\b", "NER"),
    (r"\bnorthern[\s_-]*region\b", "NR"),
    (r"\bsouthern[\s_-]*region\b", "SR"),
    (r"\bwestern[\s_-]*region\b", "WR"),
    (r"\beastern[\s_-]*region\b", "ER"),
    (r"\bCMETS[\s_-]*NER\b", "NER"),
    (r"\bCMETS[\s_-]*NR\b", "NR"),
    (r"\bCMETS[\s_-]*SR\b", "SR"),
    (r"\bCMETS[\s_-]*WR\b", "WR"),
    (r"\bCMETS[\s_-]*ER\b", "ER"),
    (r"\bNER\b", "NER"),
    (r"\bNR\b", "NR"),
    (r"\bSR\b", "SR"),
    (r"\bWR\b", "WR"),
    (r"\bER\b", "ER"),
]

ID_COLUMNS = [
    "GNA/ST II Application ID",
    "LTA Application ID",
    "Application ID under Enhancement 5.2 or revision",
]

DATE_COLUMNS = [
    "CMETS GNA Meeting Date",
    "CMETS LTA Meeting Date",
    "Application/Submission Date",
    "Applied Start of Connectivity sought by developer date"
    "( start date of connectivity as per the application)",
    "GNA Operationalization Date",
    "Date from which additional capacity is to be added",
]


def _text(value: Any) -> str:
    value = clean(value)
    return str(value) if value is not None else ""


def _numbers_only(value: Any, *, min_digits: int = 1) -> str | None:
    pattern = r"\d+" if min_digits <= 1 else rf"\b\d{{{min_digits},}}\b"
    nums = re.findall(pattern, _text(value))
    return ", ".join(nums) if nums else None


def _format_date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None

    matchers = [
        (r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", ("%d", "%m", "%Y")),
        (r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", ("%Y", "%m", "%d")),
    ]
    for pattern, order in matchers:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = dict(zip(order, match.groups()))
        year = parts["%Y"]
        if len(year) == 2:
            year = "20" + year
        try:
            dt = datetime(int(year), int(parts["%m"]), int(parts["%d"]))
            return dt.strftime("%d.%m.%Y")
        except ValueError:
            pass

    match = re.search(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b", text)
    if match:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(match.group(0), fmt).strftime("%d.%m.%Y")
            except ValueError:
                pass
    return None


def _format_state(value: Any) -> str | None:
    text = _text(value).lower()
    if not text:
        return None
    for state in sorted(INDIA_STATES_UTS, key=len, reverse=True):
        if state in text:
            return state.title()
    return text.split(",")[-1].strip(" .").title() if "," in text else text.title()


def _format_region(*values: Any) -> str | None:
    text = " ".join(_text(value) for value in values if _text(value))
    if not text:
        return None
    for pattern, region in _REGION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return region
    return None


def _format_substation(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = re.sub(r"\b\d{2,4}\s*k\s*v\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{2,4}\s*kv\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,;)])", r"\1", text)
    text = re.sub(r"([(,;])\s+", r"\1", text)
    return text.strip(" -;,") or None


def _format_yes_no(value: Any) -> str | None:
    text = _text(value).lower()
    if text.startswith("y"):
        return "Yes"
    if text.startswith("n"):
        return "No"
    return None


def _format_status(value: Any) -> str | None:
    text = _text(value).lower()
    if not text:
        return None
    if any(word in text for word in ("withdraw", "revoke", "cancel", "reject")):
        return "Withdrawn"
    if "grant" in text or "approved" in text:
        return "Granted"
    return "Applied"


_TYPE_VALUE_RE = re.compile(
    r"(solar|wind|bess|ess|hydro|hybrid|psp|pump\s*storage)"
    r"\s*\(\s*([\d,.]+)",
    re.IGNORECASE,
)


def _format_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 4)


def _parse_type(value: Any) -> tuple[str | None, dict[str, int | float]]:
    text = _text(value)
    if not text:
        return None, {}

    lower = text.lower()
    found: set[str] = set()
    capacities = {"solar": 0.0, "wind": 0.0, "hybrid": 0.0, "hydro": 0.0}

    for match in _TYPE_VALUE_RE.finditer(text):
        raw_type = match.group(1).lower().strip()
        mw_value = float(match.group(2).replace(",", ""))
        if raw_type in {"bess", "ess"}:
            found.add("BESS")
        elif raw_type == "solar":
            found.add("Solar")
            capacities["solar"] += mw_value
        elif raw_type == "wind":
            found.add("Wind")
            capacities["wind"] += mw_value
        elif raw_type in {"hydro", "psp", "pump storage"}:
            found.add("Hydro")
            capacities["hydro"] += mw_value
        elif raw_type == "hybrid":
            found.add("Hybrid")
            capacities["hybrid"] += mw_value

    keyword_map = [
        ("solar", "Solar"),
        ("wind", "Wind"),
        ("bess", "BESS"),
        ("ess", "BESS"),
        ("hydro", "Hydro"),
        ("psp", "Hydro"),
        ("pump storage", "Hydro"),
        ("hybrid", "Hybrid"),
    ]
    for keyword, label in keyword_map:
        if keyword in lower:
            found.add(label)

    if {"Solar", "Wind"}.issubset(found):
        found.discard("Solar")
        found.discard("Wind")
        found.add("Hybrid")

    order = ["Solar", "Wind", "Hybrid", "Hydro", "BESS"]
    formatted_type = "+".join(label for label in order if label in found) or None
    formatted_caps = {
        "Installed/Break-up Capacity (MW) Solar": _format_number(capacities["solar"]),
        "Installed/Break-up Capacity (MW) Wind": _format_number(capacities["wind"]),
        "Installed/Break-up Capacity (MW) Hybrid": _format_number(capacities["hybrid"]),
        "Installed/Break-up Capacity (MW) Hydro": _format_number(capacities["hydro"]),
    }
    formatted_caps = {k: v for k, v in formatted_caps.items() if v}
    return formatted_type, formatted_caps


def _headers(ws) -> dict[str, int]:
    return {str(cell.value): cell.column for cell in ws[1] if cell.value}


def _ensure_columns(ws, columns: list[str]) -> dict[str, int]:
    headers = _headers(ws)
    for col in columns:
        if col not in headers:
            ws.cell(row=1, column=ws.max_column + 1, value=col)
            headers[col] = ws.max_column
    return headers


def _set(ws, row_idx: int, headers: dict[str, int], col: str, value: Any) -> None:
    if col in headers:
        ws.cell(row=row_idx, column=headers[col], value=value)


def _get(ws, row_idx: int, headers: dict[str, int], col: str) -> Any:
    if col not in headers:
        return None
    return ws.cell(row=row_idx, column=headers[col]).value


def format_cmets_excel(source_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Create a final formatted CMETS workbook and return its path."""
    src = Path(source_path).resolve()
    out = Path(output_path).resolve() if output_path else src.with_name(f"{src.stem}_final_formatted.xlsx")

    wb = load_workbook(src)
    ws = wb["Extracted Data"] if "Extracted Data" in wb.sheetnames else wb.active
    headers = _ensure_columns(ws, [REGION_COLUMN] + CAPACITY_COLUMNS)

    for row_idx in range(2, ws.max_row + 1):
        region = _format_region(
            _get(ws, row_idx, headers, REGION_COLUMN),
            _get(ws, row_idx, headers, "PDF"),
        )
        _set(ws, row_idx, headers, REGION_COLUMN, region)

        state = _format_state(_get(ws, row_idx, headers, "State")) or _format_state(
            _get(ws, row_idx, headers, "Project Location")
        )
        _set(ws, row_idx, headers, "State", state)
        _set(ws, row_idx, headers, "Substation", _format_substation(_get(ws, row_idx, headers, "Substation")))

        for col in ID_COLUMNS:
            _set(ws, row_idx, headers, col, _numbers_only(_get(ws, row_idx, headers, col), min_digits=6))

        _set(ws, row_idx, headers, "CMETS GNA Approved", _numbers_only(_get(ws, row_idx, headers, "CMETS GNA Approved")))
        _set(ws, row_idx, headers, "CMETS LTA Approved", _numbers_only(_get(ws, row_idx, headers, "CMETS LTA Approved")))

        for col in DATE_COLUMNS:
            _set(ws, row_idx, headers, col, _format_date(_get(ws, row_idx, headers, col)))

        _set(
            ws,
            row_idx,
            headers,
            "GNA Operationalization (Yes/No)",
            _format_yes_no(_get(ws, row_idx, headers, "GNA Operationalization (Yes/No)")),
        )
        _set(
            ws,
            row_idx,
            headers,
            "Status of application(Withdrawn / granted. Revoked.)",
            _format_status(_get(ws, row_idx, headers, "Status of application(Withdrawn / granted. Revoked.)")),
        )

        formatted_type, capacities = _parse_type(_get(ws, row_idx, headers, "Type"))
        _set(ws, row_idx, headers, "Type", formatted_type)
        for col in CAPACITY_COLUMNS:
            _set(ws, row_idx, headers, col, capacities.get(col))

    opx = _get_openpyxl()
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _apply_header_style(ws, opx)
    _apply_data_style(ws, opx)
    _autosize_columns(ws)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
