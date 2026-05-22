"""
cmets_handler/prompts — Modular per-column LLM prompt assembly
================================================================
Each column (or group of overlapping columns) has its own file with a
standardized structure:

    OUTPUT_KEY  — str or list[str]   : LLM JSON output key(s)
    VARIANTS   — list or dict        : PDF header name variants
    RULE       — str                 : Column-specific extraction rule
    JSON_EXAMPLE — str or list[str]  : Example JSON fragment(s)

The assembled SYSTEM_PROMPT and USER_TEMPLATE are exported from here,
preserving backward compatibility with extraction.py.

To update a single column → edit only its file.
To add a new column → create a new file and register it in COLUMN_MODULES.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


# ── Column module paths (order = output key order) ───────────────────────────
_MODULE_PATHS: list[str] = [
    "pipeline.cmets_handler.prompts.project_location",
    "pipeline.cmets_handler.prompts.substation",
    "pipeline.cmets_handler.prompts.developer_name",
    "pipeline.cmets_handler.prompts.energy_type",
    "pipeline.cmets_handler.prompts.application_ids",
    "pipeline.cmets_handler.prompts.quantum",
    "pipeline.cmets_handler.prompts.battery",
    "pipeline.cmets_handler.prompts.psp",
    "pipeline.cmets_handler.prompts.nature_of_applicant",
    "pipeline.cmets_handler.prompts.mode_criteria",
    "pipeline.cmets_handler.prompts.dates",
    "pipeline.cmets_handler.prompts.gna_operationalization",
    "pipeline.cmets_handler.prompts.status",
    "pipeline.cmets_handler.prompts.voltage",
]

# Load all column modules
COLUMN_MODULES: list[ModuleType] = [import_module(p) for p in _MODULE_PATHS]


# ═════════════════════════════════════════════════════════════════════════════
# Helpers to normalise str | list[str] | dict values from column modules
# ═════════════════════════════════════════════════════════════════════════════

def _as_list(val) -> list[str]:
    """Coerce str | list[str] into list[str]."""
    if isinstance(val, str):
        return [val]
    return list(val)


def _flatten_variants(variants) -> list[str]:
    """Flatten VARIANTS (list or dict of lists) into a flat list."""
    if isinstance(variants, dict):
        flat = []
        for v_list in variants.values():
            flat.extend(v_list)
        return flat
    return list(variants)


# ═════════════════════════════════════════════════════════════════════════════
# Build the numbered output-key list
# ═════════════════════════════════════════════════════════════════════════════

def _build_output_keys() -> str:
    lines = []
    n = 1
    for mod in COLUMN_MODULES:
        for key in _as_list(mod.OUTPUT_KEY):
            lines.append(f" {n:>2}) {key}")
            n += 1
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Build the column-mapping + rules section
# ═════════════════════════════════════════════════════════════════════════════

def _build_column_section(mod: ModuleType) -> str:
    """Build one column's prompt section from its OUTPUT_KEY, VARIANTS, RULE."""
    keys = _as_list(mod.OUTPUT_KEY)
    variants = mod.VARIANTS
    rule = mod.RULE

    parts: list[str] = []

    # Header mapping lines
    if isinstance(variants, dict):
        # Multi-column: each key has its own variants
        for key in keys:
            key_variants = variants.get(key, [])
            if key_variants:
                aliases = " OR ".join(f'"{v}"' for v in key_variants)
                parts.append(f"- {key}  <- {aliases}")
            else:
                parts.append(f"- {key}")
    else:
        # Single column or simple list
        key = keys[0] if len(keys) == 1 else ", ".join(keys)
        if variants:
            aliases = " OR ".join(f'"{v}"' for v in variants)
            parts.append(f"- {key}  <- {aliases}")
        else:
            parts.append(f"- {key}")
        # If multiple keys share same variants (like Project Location + State)
        for extra_key in keys[1:]:
            parts.append(f"- {extra_key}")

    # Rule
    if rule:
        # Indent rule lines under the column header
        for line in rule.strip().splitlines():
            parts.append(f"      {line}")

    return "\n".join(parts)


def _build_all_column_sections() -> str:
    sections = []
    for mod in COLUMN_MODULES:
        sections.append(_build_column_section(mod))
    return "\n\n".join(sections)


# ═════════════════════════════════════════════════════════════════════════════
# Build the JSON example
# ═════════════════════════════════════════════════════════════════════════════

def _build_json_example() -> str:
    lines = []
    for mod in COLUMN_MODULES:
        examples = _as_list(mod.JSON_EXAMPLE)
        for ex in examples:
            lines.append(f"            {ex}")
    # Join with commas between lines, no trailing comma on last
    return ",\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Assemble the full SYSTEM_PROMPT
# ═════════════════════════════════════════════════════════════════════════════

def _assemble() -> str:
    from pipeline.cmets_handler.prompts._base import (
        ROLE_INTRO,
        EXTRACTION_RULES,
        PAGE_EMPTY_FOOTER,
    )

    output_keys = _build_output_keys()
    column_sections = _build_all_column_sections()
    json_example = _build_json_example()

    return f"""\
{ROLE_INTRO}
{output_keys}

═══════════════════════════════════════════════════════
COLUMN DETECTION — HEADER NAME MAPPING & RULES
═══════════════════════════════════════════════════════
The PDF may use different header names for the same logical column.
Use these mapping rules to determine which output key a column maps to:

{column_sections}

{EXTRACTION_RULES}

Return JSON in EXACTLY this shape:
{{{{
    "rows": [
        {{{{
{json_example}
        }}}}
    ]
}}}}

{PAGE_EMPTY_FOOTER}"""


# ── Assembled prompt (built once at import time) ─────────────────────────────
SYSTEM_PROMPT: str = _assemble()

USER_TEMPLATE: str = (
    "Detected column labels present on this page: {active_fields}\n\n"
    "Full page text:\n{page_text}"
)
