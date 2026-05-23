"""
pipeline.final_mapping_handler — Sequential Mapping Pipeline
===============================================================
Chains all mapping steps in order:
    Step 1: CMETS + Effectiveness → 03_cmets_effectiveness_mapped.xlsx
    Step 2: Step1  + JCC          → 06_cmets_jcc_mapped.xlsx
    Step 3: Step2  + Bay Alloc    → 07_final_mapped.xlsx

Public API:
    from pipeline.final_mapping_handler import run_full_mapping_pipeline
"""
from pipeline.final_mapping_handler.runner import run_full_mapping_pipeline

__all__ = ["run_full_mapping_pipeline"]
