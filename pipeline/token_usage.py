"""Persistent token usage accounting for LLM-backed extraction modules."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


_START_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_USAGE_PATH = _START_DIR / "output" / "token_usage.json"


def _empty_module_totals() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_calls": 0,
        "estimated_prompt_tokens": 0,
        "estimated_completion_tokens": 0,
        "estimated_total_tokens": 0,
        "last_call": {},
    }


def _load_usage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "updated_at": "",
            "modules": {},
        }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("updated_at", "")
    data.setdefault("modules", {})
    return data


def _save_usage(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _usage_from_response(response_json: dict[str, Any]) -> tuple[int, int, int]:
    usage = response_json.get("usage") if isinstance(response_json, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0

    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _estimate_usage(prompt_payload: dict[str, Any], completion_text: str) -> tuple[int, int, int]:
    prompt_text = ""
    for message in prompt_payload.get("messages", []):
        if isinstance(message, dict):
            prompt_text += str(message.get("content") or "")
            prompt_text += "\n"
    prompt_tokens = _estimate_tokens_from_text(prompt_text)
    completion_tokens = _estimate_tokens_from_text(completion_text)
    return prompt_tokens, completion_tokens, prompt_tokens + completion_tokens


def record_llm_token_usage(
    module: str,
    prompt_payload: dict[str, Any],
    response_json: dict[str, Any],
    completion_text: str = "",
    *,
    pdf_name: str = "",
    page_number: int | None = None,
    purpose: str = "",
    model: str = "",
    path: Path = DEFAULT_TOKEN_USAGE_PATH,
) -> dict[str, Any]:
    """Add one LLM call's token usage to a persistent module total.

    Uses provider-reported usage when available. If a VM/script response does
    not include usage, records an estimated count separately so the run still
    has a useful accumulated number.
    """
    actual_prompt, actual_completion, actual_total = _usage_from_response(response_json)
    has_actual = actual_total > 0
    est_prompt = est_completion = est_total = 0
    if not has_actual:
        est_prompt, est_completion, est_total = _estimate_usage(prompt_payload, completion_text)

    data = _load_usage(path)
    modules = data.setdefault("modules", {})
    totals = modules.setdefault(module, _empty_module_totals())
    for key, default_value in _empty_module_totals().items():
        totals.setdefault(key, default_value)

    totals["calls"] += 1
    totals["prompt_tokens"] += actual_prompt
    totals["completion_tokens"] += actual_completion
    totals["total_tokens"] += actual_total
    if not has_actual:
        totals["estimated_calls"] += 1
        totals["estimated_prompt_tokens"] += est_prompt
        totals["estimated_completion_tokens"] += est_completion
        totals["estimated_total_tokens"] += est_total

    now = datetime.now().isoformat(timespec="seconds")
    totals["last_call"] = {
        "at": now,
        "pdf_name": pdf_name,
        "page_number": page_number,
        "purpose": purpose,
        "model": model,
        "actual_usage_available": has_actual,
        "prompt_tokens_added": actual_prompt,
        "completion_tokens_added": actual_completion,
        "total_tokens_added": actual_total,
        "estimated_prompt_tokens_added": est_prompt,
        "estimated_completion_tokens_added": est_completion,
        "estimated_total_tokens_added": est_total,
    }
    data["updated_at"] = now
    _save_usage(path, data)
    return totals


def get_module_token_usage(
    module: str,
    path: Path = DEFAULT_TOKEN_USAGE_PATH,
) -> dict[str, Any]:
    """Return cumulative token totals for one module."""
    data = _load_usage(path)
    totals = data.get("modules", {}).get(module, {})
    merged = _empty_module_totals()
    if isinstance(totals, dict):
        merged.update(totals)
    return merged

