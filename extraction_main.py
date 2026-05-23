"""Run the full extraction + mapping pipeline.

Phase 1 — Individual Extractions (can run in any order):
    1. CMETS             → 01_cmets_extracted.xlsx
    2. Effectiveness     → 02_effectiveness_extracted.xlsx
    3. JCC               → 04_jcc_extracted.xlsx
    4. Bay Allocation    → 05_bayallocation_extracted.xlsx

Phase 2 — Sequential Mapping (runs after ALL extractions complete):
    Step 1: CMETS + Effectiveness   → 03_cmets_effectiveness_mapped.xlsx
    Step 2: Step1 + JCC             → 06_cmets_jcc_mapped.xlsx
    Step 3: Step2 + Bay Allocation  → 07_final_mapped.xlsx

The final Excel (07_final_mapped.xlsx) contains ALL CMETS columns plus:
    - Effectiveness enrichment (Updated Date, Updated GNA Op Date, Yes/No, etc.)
    - JCC columns (TGNA, GNA)
    - Bay Allocation columns (Bay No, Substation Coordinates)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from config import load_runtime_config
from pipeline.extraction_orchestrator import run_pending_extractions
from pipeline.final_mapping_handler import run_full_mapping_pipeline

_START_DIR = Path(__file__).resolve().parent


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extraction + Mapping pipeline")
    parser.add_argument("--mode", choices=["vm", "laptop"], default=None,
                        help="Override execution mode (default: vm)")
    parser.add_argument("--api-key", default=None,
                        help="OpenAI API key (laptop mode override)")
    parser.add_argument("--llm-script", default=None,
                        help="Path to llm_client.bat (vm mode override)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max pages to process per PDF (-1 = all, default from config)")
    parser.add_argument("--sources", default=None, metavar="LIST",
                        help="Comma-separated source keys/names to extract (default: all)")
    parser.add_argument("--extract-only", action="store_true",
                        help="Run only extraction phase (skip mapping)")
    parser.add_argument("--map-only", action="store_true",
                        help="Run only mapping phase (skip extraction)")
    return parser.parse_args()


def main() -> None:
    args = _build_args()
    runtime = load_runtime_config(
        mode_override=args.mode,
        api_key_override=args.api_key,
        llm_script_override=args.llm_script,
        max_pages_override=args.max_pages,
    )

    only_sources = None
    if args.sources:
        only_sources = [part.strip() for part in args.sources.split(",") if part.strip()]

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1 — Individual Extractions
    # ══════════════════════════════════════════════════════════════════════
    if not args.map_only:
        print("\n" + "█" * 64)
        print("  PHASE 1 — INDIVIDUAL EXTRACTIONS")
        print("█" * 64)
        print(f"  Max pages per PDF : {runtime.max_pages if runtime.max_pages != -1 else 'ALL'}")

        results = run_pending_extractions(
            runtime,
            only_sources=only_sources,
            regions=runtime.source_regions,
        )
        print("\n" + "=" * 64)
        print("  PHASE 1 — EXTRACTION COMPLETE")
        for item in results:
            print(f"  {item['source']:<35} pending={item['pending']:<4} extracted={item['extracted']}")
        print("=" * 64 + "\n")
    else:
        print("\n[Pipeline] Skipping extraction phase (--map-only)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2 — Sequential Mapping Pipeline
    # ══════════════════════════════════════════════════════════════════════
    if not args.extract_only:
        print("\n" + "█" * 64)
        print("  PHASE 2 — SEQUENTIAL MAPPING PIPELINE")
        print("█" * 64)

        try:
            final_excel = run_full_mapping_pipeline(start_dir=_START_DIR)
            print(f"\n  ✓ FINAL OUTPUT → {final_excel}")
        except FileNotFoundError as exc:
            print(f"\n  ⚠ Mapping skipped: {exc}")
        except Exception as exc:
            print(f"\n  ⚠ Mapping failed: {exc}")
            import traceback
            traceback.print_exc()
    else:
        print("\n[Pipeline] Skipping mapping phase (--extract-only)")

    print("\n" + "█" * 64)
    print("  PIPELINE COMPLETE")
    print("█" * 64 + "\n")


if __name__ == "__main__":
    main()
