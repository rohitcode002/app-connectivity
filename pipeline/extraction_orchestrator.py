"""Helpers for extraction-by-source using per-source cache tables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Iterable
import shutil

from config import RuntimeConfig
from pipeline.downloader.pdf_cache import get_pdf_cache
from pipeline.cmets_handler import run_cmets_extraction
from pipeline.effectiveness_handler import run_effectiveness_extraction
from pipeline.jcc_handler import run_jcc_extraction, run_jcc_mapping
from pipeline.bayallocation_handler import run_bayallocation_extraction

_START_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ExtractionSource:
    name: str
    key: str
    handler: str
    source_dir: Path
    fallback_dir: Path
    output_dir: Path
    excel_path: Path
    runner: Callable[..., object]
    allow_types: tuple[str, ...] | None = None
    flatten: bool = False


@dataclass(frozen=True)
class PendingPdf:
    pdf_name: str
    pdf_type: str
    pdf_path: Path


def _infer_type(source_name: str, pdf_path: Path, pdf_type: str | None) -> str:
    if pdf_type and str(pdf_type).strip():
        return str(pdf_type).strip()
    parts = pdf_path.parts
    jcc_regions = {
        "Eastern Region",
        "North Eastern Region",
        "Northern Region",
        "Southern Region",
        "Western Region",
    }
    if source_name in parts:
        idx = parts.index(source_name)
        if idx + 1 < len(parts) - 1:
            first = parts[idx + 1]
            if first in jcc_regions and idx + 2 < len(parts) - 1:
                return parts[idx + 2]
            return first
    parent = pdf_path.parent.name
    if parent and parent != source_name:
        return parent
    return ""


def _resolve_source_dir(primary: Path, fallback: Path) -> Path:
    if primary.exists() and any(primary.rglob("*.pdf")):
        alternate = primary
        parts = list(primary.parts)
        if "source_output" in parts:
            idx = parts.index("source_output")
            parts[idx] = "source_output1"
            alternate = Path(*parts)
        if alternate != primary and alternate.exists():
            primary_count = sum(1 for p in primary.rglob("*.pdf") if p.is_file())
            alternate_count = sum(1 for p in alternate.rglob("*.pdf") if p.is_file())
            if alternate_count > primary_count:
                return alternate
        return primary
    return fallback


def _resolve_pdf_path(source_dir: Path, pdf_name: str) -> Path | None:
    for path in source_dir.rglob(pdf_name):
        if path.is_file():
            return path
    return None


def _normalize_region_name(name: str) -> str:
    return " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())


def _path_matches_regions(pdf_path: Path, regions: Iterable[str] | None) -> bool:
    selected = {_normalize_region_name(region) for region in (regions or []) if str(region).strip()}
    if not selected:
        return True

    known_regions = {
        "eastern region",
        "north eastern region",
        "northern region",
        "southern region",
        "western region",
    }
    path_regions = {
        norm
        for part in pdf_path.parts
        for norm in [_normalize_region_name(part)]
        if norm in known_regions
    }
    return not path_regions or bool(path_regions & selected)


def _seed_cache_from_source(cache, source_name: str, source_dir: Path) -> None:
    entries = []
    for pdf in source_dir.rglob("*.pdf"):
        if not pdf.is_file():
            continue
        pdf_type = _infer_type(source_name, pdf, "")
        entries.append((pdf.name, pdf_type, str(pdf)))
    cache.record_existing_pdfs(entries)


def _collect_pending(
    source: ExtractionSource,
    db_path: Path,
    regions: Iterable[str] | None = None,
) -> list[PendingPdf]:
    cache = get_pdf_cache(db_path, source.key, source.name)
    active_dir = _resolve_source_dir(source.source_dir, source.fallback_dir)
    _seed_cache_from_source(cache, source.name, active_dir)

    pending = []
    for row in cache.get_pending_extractions():
        pdf_name = row.get("pdf_name", "")
        if not pdf_name:
            continue
        pdf_type = row.get("pdf_type", "")
        pdf_path_str = row.get("pdf_path", "")
        pdf_path = Path(pdf_path_str) if pdf_path_str else None
        if pdf_path is None or not pdf_path.exists():
            pdf_path = _resolve_pdf_path(active_dir, pdf_name)
        if pdf_path is None:
            continue
        if not _path_matches_regions(pdf_path, regions):
            continue
        inferred_type = _infer_type(source.name, pdf_path, pdf_type)
        if source.allow_types and inferred_type not in source.allow_types:
            continue
        pending.append(PendingPdf(pdf_name=pdf_name, pdf_type=inferred_type, pdf_path=pdf_path))
    return pending


def _collect_available(source: ExtractionSource, regions: Iterable[str] | None = None) -> list[PendingPdf]:
    """Collect source PDFs for rebuilding an Excel from existing JSON/source data."""
    active_dir = _resolve_source_dir(source.source_dir, source.fallback_dir)
    available: list[PendingPdf] = []
    for pdf_path in sorted(active_dir.rglob("*.pdf")):
        if not pdf_path.is_file():
            continue
        if not _path_matches_regions(pdf_path, regions):
            continue
        inferred_type = _infer_type(source.name, pdf_path, "")
        if source.allow_types and inferred_type not in source.allow_types:
            continue
        available.append(PendingPdf(pdf_name=pdf_path.name, pdf_type=inferred_type, pdf_path=pdf_path))
    return available


def _prepare_temp_dir(pdfs: Iterable[PendingPdf], flatten: bool) -> tuple[TemporaryDirectory, Path]:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    for item in pdfs:
        if flatten or not item.pdf_type:
            dest_dir = root
        else:
            dest_dir = root / item.pdf_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / item.pdf_name
        if not dest.exists():
            shutil.copy2(item.pdf_path, dest)
    return tmp, root


def _cache_path_for(output_dir: Path, pdf_name: str) -> Path:
    return output_dir / f"{Path(pdf_name).stem}.json"


def _run_source_runner(source: ExtractionSource, runtime: RuntimeConfig, pdfs: list[PendingPdf]) -> None:
    """Run extraction only for any source. No mapping layers."""
    tmp_ctx, temp_dir = _prepare_temp_dir(pdfs, source.flatten)
    max_pages = runtime.max_pages
    try:
        source.runner(
            source_dir=str(temp_dir),
            output_dir=str(source.output_dir),
            excel_path=str(source.excel_path),
            runtime=runtime,
            max_pages=max_pages,
        )
    finally:
        tmp_ctx.cleanup()


def run_jcc_mapping_step(start_dir: Path | None = None) -> None:
    """Run JCC mapping layers after all extractions are complete.

    Called separately by the full pipeline (extract_main.py) after
    all source extractions have finished.
    """
    root = start_dir or _START_DIR
    excel_root = root / "excels"
    cache_dir = root / "output" / "jcc_cache"

    run_jcc_mapping(
        jcc_cache_dir=str(cache_dir),
        jcc_output_excel_path=str(excel_root / "04_jcc_output_layer.xlsx"),
        jcc_mapped_excel_path=str(excel_root / "04_jcc_extracted_mapped.xlsx"),
        layer4_excel_path=str(excel_root / "04_cmets_jcc_mapped.xlsx"),
        cmets_excel_path=str(excel_root / "01_cmets_extracted.xlsx"),
        effectiveness_excel_path=str(excel_root / "02_effectiveness_extracted.xlsx"),
    )


def extract_pending_for_source(
    source: ExtractionSource,
    runtime: RuntimeConfig,
    db_path: Path,
    regions: Iterable[str] | None = None,
) -> dict:
    pending = _collect_pending(source, db_path, regions=regions)
    if not pending:
        if source.excel_path.exists():
            return {"source": source.name, "pending": 0, "extracted": 0}

        available = _collect_available(source, regions=regions)
        if not available:
            return {"source": source.name, "pending": 0, "extracted": 0}

        print(f"\n[Extraction] Rebuilding missing Excel for {source.name}: {source.excel_path}")
        _run_source_runner(source, runtime, available)
        return {"source": source.name, "pending": 0, "extracted": 0, "excel_rebuilt": 1}

    _run_source_runner(source, runtime, pending)

    cache = get_pdf_cache(db_path, source.key, source.name)
    extracted = 0
    for item in pending:
        cache_path = _cache_path_for(source.output_dir, item.pdf_name)
        if cache_path.exists():
            cache.mark_extracted(item.pdf_name, pdf_type=item.pdf_type, pdf_path=item.pdf_path)
            extracted += 1
    return {"source": source.name, "pending": len(pending), "extracted": extracted}


def get_extraction_sources(start_dir: Path | None = None) -> list[ExtractionSource]:
    root = start_dir or _START_DIR
    output_root = root / "output" / "source_output"
    source_root = root / "source"
    output_cache = root / "output"
    excel_root = root / "excels"

    return [
        ExtractionSource(
            name="CTUIL-ISTS-CMETS",
            key="cmets",
            handler="cmets",
            source_dir=output_root / "CTUIL-ISTS-CMETS",
            fallback_dir=source_root / "cmets_pdfs",
            output_dir=output_cache / "cmets_cache",
            excel_path=excel_root / "01_cmets_extracted.xlsx",
            runner=run_cmets_extraction,
            allow_types=("minutes", "Minutes"),
            flatten=True,
        ),
        ExtractionSource(
            name="CTUIL-Regenerators-Effective-Date-wise",
            key="effectiveness",
            handler="effectiveness",
            source_dir=output_root / "CTUIL-Regenerators-Effective-Date-wise",
            fallback_dir=source_root / "effectiveness_pdfs",
            output_dir=output_cache / "effectiveness_cache",
            excel_path=excel_root / "02_effectiveness_extracted.xlsx",
            runner=run_effectiveness_extraction,
            flatten=False,
        ),
        ExtractionSource(
            name="CTUIL-ISTS-JCC",
            key="jcc",
            handler="jcc",
            source_dir=output_root / "CTUIL-ISTS-JCC",
            fallback_dir=source_root / "jcc_pdfs",
            output_dir=output_cache / "jcc_cache",
            excel_path=excel_root / "04_jcc_extracted.xlsx",
            runner=run_jcc_extraction,
            allow_types=("Minutes",),
            flatten=False,
        ),
        ExtractionSource(
            name="CTUIL-Renewable-Energy",
            key="bayallocation",
            handler="bayallocation",
            source_dir=output_root / "CTUIL-Renewable-Energy" / "Bays Allocation",
            fallback_dir=source_root / "bayallocation",
            output_dir=output_cache / "bayallocation_cache",
            excel_path=excel_root / "05_bayallocation_extracted.xlsx",
            runner=run_bayallocation_extraction,
            allow_types=None,
            flatten=False,
        ),
    ]


def run_pending_extractions(
    runtime: RuntimeConfig,
    only_sources: Iterable[str] | None = None,
    regions: Iterable[str] | None = None,
) -> list[dict]:
    db_path = _START_DIR / "pipeline_tracker.db"
    sources = get_extraction_sources(_START_DIR)
    selected_sources = list(only_sources or runtime.source_names or [])
    if selected_sources:
        selected = {name.strip() for name in selected_sources if name.strip()}
        sources = [s for s in sources if s.name in selected or s.key in selected or s.handler in selected]
    selected_regions = list(regions or runtime.source_regions or [])
    return [extract_pending_for_source(source, runtime, db_path, regions=selected_regions) for source in sources]
