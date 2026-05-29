"""
cmets_handler/battery_extractor.py — BESS extraction logic
==========================================================
Separate file to handle Battery (BESS) related extraction operations.
Uses an LLM to accurately extract MWh, Injection, and Drawl from context text
and the 'type' column (e.g., "BESS (19)").
"""

import json
import re
from typing import Optional

from config import MODEL, load_runtime_config
from llm_client import call_llm, extract_text_from_response
from pipeline.shared_utils import parse_bess_duration_hours, parse_json, parse_type_capacity
from pipeline.token_usage import record_llm_token_usage


def clean(v):
    """Local clean helper (mirrors normalization.clean to avoid circular import)."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in {"null", "none", "na", "n/a", "-", "--"} else (v or None)

# ── Prompt Templates ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert data extractor. Extract the Battery Energy Storage System (BESS) parameters from the provided text.
Look for:
1. Battery MWh capacity. Do NOT treat plain BESS (X) as MWh by itself;
   BESS (X) is MW/injection unless an hour duration is present.
2. Battery Injection (MW).
3. Battery Drawl (MW).
4. Duration in hours if mentioned (e.g. "4 hours", "four (4) hours", "2 hrs").
   If MWh is NOT explicitly stated but Injection MW and a duration are found,
   calculate: mwh = inj × duration_hours.

If a value is not found, return null. Ensure your output is ONLY a valid JSON object with the keys "mwh", "inj", "drw", and "duration_hours". DO NOT include markdown formatting or explanations.
"""

USER_TEMPLATE = """\
Extract the BESS values from the following text snippets:
Type String: {type_col}
Additional Context: {context}

Return exactly this JSON format:
{{
    "mwh": <float or null>,
    "inj": <float or null>,
    "drw": <float or null>,
    "duration_hours": <float or null>
}}
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

# Word-to-number mapping for common written-out hour values
_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}


def _parse_duration_hours(text: str) -> Optional[float]:
    """Parse a duration in hours from free text.

    Handles patterns like:
      - "4 hours", "4 hrs", "4-hour", "4h"
      - "four (4) hours", "four hours"
      - "4.5 hours"
    Returns the numeric hours or None.
    """
    if not text:
        return None

    # Pattern 1: written word with optional parenthetical digit, e.g. "four (4) hours"
    m = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"\s*(?:\(\s*(\d+(?:\.\d+)?)\s*\)\s*)?"
        r"(?:hours?|hrs?)\b",
        text, re.IGNORECASE,
    )
    if m:
        # Prefer the parenthetical digit if present
        if m.group(2):
            return float(m.group(2))
        return float(_WORD_TO_NUM.get(m.group(1).lower(), 0)) or None

    # Pattern 2: plain numeric duration, e.g. "4 hours", "4.5 hrs", "4-hour", "4h"
    m = re.search(
        r"\b(\d+(?:\.\d+)?)\s*[-\s]?(?:hours?|hrs?)\b",
        text, re.IGNORECASE,
    )
    if m:
        return float(m.group(1))

    return None


_RUNTIME_CONFIG = None

# ── Main Extraction Logic ─────────────────────────────────────────────────────

def extract_battery_values(
    raw_mwh: Optional[str],
    raw_inj: Optional[str],
    raw_drw: Optional[str],
    raw_mode: Optional[str],
    type_col: Optional[str],
    row_context: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Use an LLM to extract Battery MWh, Injection, and Drawl.
    Fast-fails using a keyword check to avoid calling the LLM unnecessarily.

    Parameters
    ----------
    row_context : str, optional
        Full concatenated text from the entire row (quantum, type, etc.)
        so that duration patterns like "4 hours" can be found even when
        they appear in columns outside the battery-specific fields.
    """
    global _RUNTIME_CONFIG

    # Fast-fail: only run LLM if BESS is actually mentioned
    text_to_search = " ".join(str(v or "") for v in [raw_mwh, raw_inj, raw_drw, raw_mode, type_col])
    # Broader context for duration parsing (includes quantum, type, etc.)
    full_context = text_to_search + (" " + row_context if row_context else "")
    if not re.search(r"\b(bess|battery\s*energy|battery)\b", full_context, re.IGNORECASE):
        return raw_mwh, raw_inj, raw_drw

    if _RUNTIME_CONFIG is None:
        _RUNTIME_CONFIG = load_runtime_config()

    prompt_payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(
                type_col=type_col or "None",
                context=full_context
            )}
        ],
        "temperature": 0,
        "max_tokens": 200,
    }

    mwh = raw_mwh
    inj = raw_inj
    drw = raw_drw
    type_bess_mw = parse_type_capacity(type_col).get("BESS", 0.0)
    if (inj is None or clean(inj) is None) and type_bess_mw > 0:
        inj = f"{type_bess_mw:g}"

    try:
        resp = call_llm(
            prompt_payload,
            vm=_RUNTIME_CONFIG.vm_mode,
            api_key=_RUNTIME_CONFIG.api_key,
            model=MODEL,
            script_path=_RUNTIME_CONFIG.llm_script_path
        )
        content = extract_text_from_response(resp)
        totals = record_llm_token_usage(
            "cmets",
            prompt_payload,
            resp,
            content,
            purpose="battery_value_extraction",
            model=MODEL,
        )
        total_display = totals["total_tokens"] + totals["estimated_total_tokens"]
        print(f"      [Battery Extractor] token total so far: {total_display}")
        result = parse_json(content)

        if "mwh" in result and result["mwh"] is not None:
            mwh = str(result["mwh"])
        if "inj" in result and result["inj"] is not None:
            inj = str(result["inj"])
        if "drw" in result and result["drw"] is not None:
            drw = str(result["drw"])

        # ── Duration formula fallback ─────────────────────────────────────
        # If the LLM already computed mwh via the duration formula, great.
        # Otherwise, apply the formula ourselves using either the LLM's
        # duration_hours or a regex parse of the raw text.
        if mwh is None or clean(mwh) is None:
            duration = None
            if "duration_hours" in result and result["duration_hours"] is not None:
                try:
                    duration = float(result["duration_hours"])
                except (ValueError, TypeError):
                    pass
            # Regex fallback: parse duration from the combined text
            if duration is None:
                duration = parse_bess_duration_hours(type_col) or _parse_duration_hours(full_context)

            inj_val = inj or raw_inj
            if duration and duration > 0 and inj_val and clean(inj_val):
                try:
                    computed_mwh = float(clean(inj_val)) * duration
                    mwh = str(computed_mwh)
                    print(f"      [Battery Extractor] Duration formula: "
                          f"{clean(inj_val)} MW × {duration} h = {computed_mwh} MWh")
                except (ValueError, TypeError):
                    pass

    except Exception as exc:
        print(f"      [Battery Extractor LLM Error] {exc}")

    # ── Post-LLM duration formula (in case LLM call itself failed) ────────
    if mwh is None or clean(mwh) is None:
        duration = parse_bess_duration_hours(type_col) or _parse_duration_hours(full_context)
        inj_val = inj or raw_inj
        if duration and duration > 0 and inj_val and clean(inj_val):
            try:
                computed_mwh = float(clean(inj_val)) * duration
                mwh = str(computed_mwh)
                print(f"      [Battery Extractor] Duration formula (post-LLM): "
                      f"{clean(inj_val)} MW × {duration} h = {computed_mwh} MWh")
            except (ValueError, TypeError):
                pass

    # If Type explicitly carries a BESS duration, the duration formula is
    # authoritative. This fixes cases where BESS MW was copied into MWh.
    duration = parse_bess_duration_hours(type_col)
    inj_val = inj or raw_inj
    if duration > 0 and inj_val and clean(inj_val):
        try:
            computed_mwh = float(clean(inj_val)) * duration
            mwh = str(computed_mwh)
        except (ValueError, TypeError):
            pass

    return mwh, inj, drw
