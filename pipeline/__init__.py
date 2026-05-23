"""
pipeline — PDF Download, Extraction & Mapping Pipeline
========================================================
Sub-packages:
    downloader/                → PDF Download Module (merged from ctuil-pdf-scraper)
    cmets_handler/             → Module 1: CMETS PDF extraction
    effectiveness_handler/     → Module 2: Effectiveness PDF extraction
    mapping_handler/           → Module 3: CMETS × Effectiveness merge
    jcc_handler/               → Module 4: JCC Meeting PDF extraction
    bayallocation_handler/     → Module 5: Bay Allocation PDF extraction
    bay_mapping_handler/       → Module 6: CMETS × Bay Allocation mapping
    final_mapping_handler/     → Module 7: Sequential mapping pipeline (all sources → final Excel)

Shared utilities:
    tracker.py                 → SQLite pipeline tracker
    excel_utils.py             → Generic JSON → Excel exporter
"""

from importlib import import_module

from pipeline.tracker import PipelineTracker
from pipeline.downloader import (
    download_cmets_pdfs,
    download_jcc_pdfs,
    download_effectiveness_pdfs,
    download_bayallocation_pdfs,
)

_LAZY_EXPORTS = {
    "run_cmets_extraction": ("pipeline.cmets_handler", "run_cmets_extraction"),
    "run_effectiveness_extraction": ("pipeline.effectiveness_handler", "run_effectiveness_extraction"),
    "run_mapping": ("pipeline.mapping_handler", "run_mapping"),
    "run_jcc_extraction": ("pipeline.jcc_handler", "run_jcc_extraction"),
    "run_bayallocation_extraction": ("pipeline.bayallocation_handler", "run_bayallocation_extraction"),
    "run_bay_mapping": ("pipeline.bay_mapping_handler", "run_bay_mapping"),
    "run_full_mapping_pipeline": ("pipeline.final_mapping_handler", "run_full_mapping_pipeline"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value

__all__ = [
    # Downloaders
    "download_cmets_pdfs",
    "download_jcc_pdfs",
    "download_effectiveness_pdfs",
    "download_bayallocation_pdfs",
    # Extractors
    "run_cmets_extraction",
    "run_effectiveness_extraction",
    "run_mapping",
    "run_jcc_extraction",
    "run_bayallocation_extraction",
    "run_bay_mapping",
    "run_full_mapping_pipeline",
    # Tracker
    "PipelineTracker",
]
