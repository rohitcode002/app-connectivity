"""
extract_cmets.py — Standalone CMETS extraction script
======================================================
Run this to extract only CMETS source data without running the full pipeline.

Usage:
    python extract_cmets.py
    python extract_cmets.py --max-pages 50
    python extract_cmets.py --mode laptop --api-key sk-...
    python extract_cmets.py --single-pdf /path/to/file.pdf
    python extract_cmets.py --clear-cache
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from config import load_runtime_config
from pipeline.cmets_handler.runner import run_cmets_extraction

_START_DIR = Path(__file__).resolve().parent


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone CMETS PDF extraction")
    parser.add_argument("--mode", choices=["vm", "laptop"], default=None,
                        help="Override execution mode (default from config)")
    parser.add_argument("--api-key", default=None,
                        help="OpenAI API key (laptop mode override)")
    parser.add_argument("--llm-script", default=None,
                        help="Path to llm_client script (vm mode override)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max pages to process per PDF (-1 = all, default from config)")
    parser.add_argument("--source-dir", default=None,
                        help="Override source PDF directory")
    parser.add_argument("--output-dir", default=None,
                        help="Override JSON cache output directory")
    parser.add_argument("--excel-path", default=None,
                        help="Override Excel output path")
    parser.add_argument("--single-pdf", default=None,
                        help="Extract a single PDF file")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear JSON cache before extraction")
    return parser.parse_args()


def main() -> None:
    args = _build_args()
    runtime = load_runtime_config(
        mode_override=args.mode,
        api_key_override=args.api_key,
        llm_script_override=args.llm_script,
        max_pages_override=args.max_pages,
    )

    max_pages = runtime.max_pages

    output_dir = Path(args.output_dir).resolve() if args.output_dir else _START_DIR / "output" / "cmets_cache"
    if args.clear_cache and output_dir.exists():
        print(f"  Clearing CMETS cache: {output_dir}")
        shutil.rmtree(output_dir)

    print("\n" + "=" * 64)
    print("  STANDALONE CMETS EXTRACTION")
    print(f"  Max pages per PDF : {max_pages if max_pages != -1 else 'ALL'}")
    print("=" * 64)

    out_path = run_cmets_extraction(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        excel_path=args.excel_path,
        single_pdf=args.single_pdf,
        runtime=runtime,
        max_pages=max_pages,
    )

    print(f"\n  CMETS extraction complete → {out_path}")


if __name__ == "__main__":
    main()
