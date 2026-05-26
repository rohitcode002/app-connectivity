"""
pipeline.cmets_handler — CMETS PDF Extraction Handler
======================================================
Public API:
    from pipeline.cmets_handler import run_cmets_extraction
"""

from importlib import import_module


def __getattr__(name: str):
    if name != "run_cmets_extraction":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("pipeline.cmets_handler.runner"), name)
    globals()[name] = value
    return value


__all__ = ["run_cmets_extraction"]
