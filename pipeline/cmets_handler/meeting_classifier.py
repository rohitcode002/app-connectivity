"""
cmets_handler/meeting_classifier.py — CMETS Meeting Number & Date Extraction
===============================================================================
Reads the FIRST PAGE of each CMETS PDF and extracts:

    1. Meeting number  — e.g. "42nd" from "42nd Consulting Meeting"
                         or from "Ref: CTU/N/00/CMETS_NR/42"
    2. Meeting date    — e.g. "11th November 2025 (Tuesday)"
                         parsed as dd.mm.yyyy

Then determines which columns the values go into based on the GNA vs LTA
keyword count across the ENTIRE PDF:

    • If GNA keywords are more frequent than or tied with LTA keywords:
        → CMETS GNA Approved  = meeting number
        → CMETS GNA Meeting Date = meeting date

    • If LTA keywords are more frequent:
        → CMETS LTA Approved  = meeting number
        → CMETS LTA Meeting Date = meeting date

This is a **pre-extraction** step — the 4 values produced here are
the same for EVERY row extracted from the same PDF.

Pipeline Integration
--------------------
Called from runner.py BEFORE page-by-page extraction.  The returned
dict is injected into every flattened row during the _flatten() step.

    from pipeline.cmets_handler.meeting_classifier import classify_meeting
    meta = classify_meeting(pdf_path)
"""

from __future__ import annotations

import os
import re
import logging
import shutil
import subprocess
import sys
import tempfile
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber

from config import TESSERACT_CMD, TESSERACT_OCR_DIR, MODEL
from llm_client import call_llm, extract_text_from_response

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")


# ── Result container ─────────────────────────────────────────────────────────

@dataclass
class MeetingMeta:
    """Metadata extracted from the first page of a CMETS PDF."""
    meeting_number:       Optional[str] = None
    meeting_date:         Optional[str] = None   # dd.mm.yyyy
    meeting_date_method:  str = ""               # "page_1_pdf_text", "page_1_ocr", or "not_found"
    cmets_gna_approved:   Optional[str] = None   # meeting number (if GNA pathway)
    cmets_lta_approved:   Optional[str] = None   # meeting number (if LTA pathway)
    cmets_gna_meeting_date: Optional[str] = None # meeting date   (if GNA pathway)
    cmets_lta_meeting_date: Optional[str] = None # meeting date   (if LTA pathway)
    classification:       str = ""               # "GNA" or "LTA"
    gna_count:            int = 0
    lta_count:            int = 0
    first_page_text:      str = ""               # Physical page 1 diagnostic text saved in JSON
    first_page_ocr_text:  str = ""
    first_page_ocr_available: bool = False
    first_page_ocr_error: str = ""
    first_page_ocr_command: str = ""
    first_page_text_source: str = ""             # "pdf_text", "ocr", or "empty"
    first_page_image_base64: str = ""            # Physical page 1 PNG, embedded in JSON diagnostics
    first_page_image_mime_type: str = ""
    first_readable_page_number: Optional[int] = None

    def as_row_dict(self) -> dict:
        """Return the 4 columns to inject into every extracted row."""
        return {
            "CMETS GNA Approved":     self.cmets_gna_approved,
            "CMETS LTA Approved":     self.cmets_lta_approved,
            "CMETS GNA Meeting Date": self.cmets_gna_meeting_date,
            "CMETS LTA Meeting Date": self.cmets_lta_meeting_date,
        }

    def as_first_page_dict(self) -> dict:
        """Return physical page-1 evidence for the per-PDF JSON cache.

        NOTE: image_base64 is intentionally excluded to keep cache JSON
        files small.  The base64 is still available on the dataclass at
        runtime for OCR / diagnostics if needed.
        """
        return {
            "page_number": 1,
            "text_source": self.first_page_text_source or "empty",
            "text": self.first_page_text or "",
            "ocr_text": self.first_page_ocr_text or "",
            "ocr_available": self.first_page_ocr_available,
            "ocr_error": self.first_page_ocr_error or None,
            "ocr_command": self.first_page_ocr_command or None,
        }

    def as_diagnostics_dict(self) -> dict:
        """Return classifier diagnostics for debugging missing meeting metadata."""
        return {
            "meeting_number": self.meeting_number,
            "meeting_date": self.meeting_date,
            "meeting_date_method": self.meeting_date_method or "not_found",
            "classification": self.classification,
            "gna_count": self.gna_count,
            "lta_count": self.lta_count,
            "first_readable_page_number": self.first_readable_page_number,
        }


# ── Month name → number ─────────────────────────────────────────────────────

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # Abbreviated
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}


# ── Meeting number extraction ───────────────────────────────────────────────

def _extract_meeting_number(text: str) -> Optional[str]:
    """Extract the consulting meeting number from the first page text.

    Looks for patterns like:
      - "42nd Consulting Meeting"
      - "42nd CMETS"
      - "Ref: CTU/N/00/CMETS_NR/42"
      - "CMETS_SR/35"
    """
    # Pattern 1: "NNth/st/nd/rd Consulting Meeting" or "NNth/st/nd/rd CMETS"
    m = re.search(
        r"(\d{1,3})\s*(st|nd|rd|th)\s+(?:consulting\s+meeting|cmets)",
        text, re.IGNORECASE,
    )
    if m:
        return f"{m.group(1)}{m.group(2).lower()}"

    # Pattern 2: Ref number like "CMETS_NR/42" or "CMETS_SR/35" or "CMETS/42"
    m = re.search(
        r"CMETS(?:_[A-Z]{1,3})?[/\\](\d{1,3})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # Pattern 3: "Meeting No. 42" or "Meeting Number 42"
    m = re.search(
        r"meeting\s+(?:no\.?\s*|number\s*)(\d{1,3})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)

    return None


def _extract_meeting_number_from_filename(pdf_path: str) -> Optional[str]:
    """Extract meeting number from filenames like '45th CMETS-NR.pdf'."""
    stem = Path(pdf_path).stem
    m = re.search(r"\b(\d{1,3})\s*(st|nd|rd|th)\b", stem, re.IGNORECASE)
    if m:
        return f"{m.group(1)}{m.group(2).lower()}"
    return _extract_meeting_number(stem)


# ── Meeting date extraction ──────────────────────────────────────────────────

def _extract_meeting_date(text: str) -> Optional[str]:
    """Extract the meeting date from the first page text.

    Looks for natural language dates like:
      - "11th November 2025 (Tuesday)"
      - "3rd March 2026"
      - "25 April 2025"

    Returns date as dd.mm.yyyy string.
    Does NOT match numeric formats like 28-2025 or 11/2025.
    """
    # Pattern: "Nth Month YYYY" with optional day-of-week
    month_names = "|".join(_MONTHS.keys())
    pattern = (
        r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+"
        rf"({month_names})\s+"
        r"(\d{4})"
    )
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year = int(m.group(3))
        month = _MONTHS.get(month_name)
        if month and 1 <= day <= 31 and 2000 <= year <= 2099:
            return f"{day:02d}.{month:02d}.{year}"

    # Pattern 2: "Month Nth, YYYY" (American-ish)
    pattern2 = (
        rf"({month_names})\s+"
        r"(\d{1,2})\s*(?:st|nd|rd|th)?,?\s+"
        r"(\d{4})"
    )
    m = re.search(pattern2, text, re.IGNORECASE)
    if m:
        month_name = m.group(1).lower()
        day = int(m.group(2))
        year = int(m.group(3))
        month = _MONTHS.get(month_name)
        if month and 1 <= day <= 31 and 2000 <= year <= 2099:
            return f"{day:02d}.{month:02d}.{year}"

    # Pattern 3: numeric dates used in CMETS headers, e.g. "held on 10.04.2026"
    # or "held on 23-03-2026". Only called on the first readable meeting page.
    pattern3 = r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"
    for m in re.finditer(pattern3, text, re.IGNORECASE):
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2099:
            return f"{day:02d}.{month:02d}.{year}"

    return None


def _extract_meeting_date_from_filename(pdf_path: str) -> Optional[str]:
    """Extract meeting date from filenames such as '... 16-03-2026 final.pdf'."""
    stem = Path(pdf_path).stem
    return _extract_meeting_date(stem)


@dataclass
class OCRResult:
    text: str = ""
    available: bool = False
    error: str = ""
    command: str = ""


def _windows_path_to_wsl(path: str) -> str:
    """Convert C:\\... to /mnt/c/... when running from WSL/Linux."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not m:
        return path
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def _candidate_tesseract_commands() -> list[str]:
    """Return tesseract commands from PATH and config, preserving order."""
    candidates: list[str] = []

    found = shutil.which("tesseract")
    if found:
        candidates.append(found)

    configured = str(TESSERACT_CMD or "").strip()
    configured_dir = str(TESSERACT_OCR_DIR or "").strip()
    if configured:
        candidates.append(configured)
        candidates.append(_windows_path_to_wsl(configured))
    if configured_dir:
        candidates.append(str(Path(configured_dir) / "tesseract.exe"))
        candidates.append(str(Path(configured_dir) / "tesseract"))
        wsl_dir = _windows_path_to_wsl(configured_dir)
        candidates.append(str(Path(wsl_dir) / "tesseract.exe"))
        candidates.append(str(Path(wsl_dir) / "tesseract"))

    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _resolve_tesseract_command(require_configured: bool = False) -> tuple[str, str]:
    """Find a runnable tesseract command, optionally requiring the configured path."""
    commands = _candidate_tesseract_commands()
    if require_configured:
        configured = str(TESSERACT_CMD or "").strip()
        configured_wsl = _windows_path_to_wsl(configured)
        commands = [
            cmd for cmd in commands
            if cmd in {configured, configured_wsl}
            or str(cmd).endswith("/Tesseract-OCR/tesseract.exe")
            or str(cmd).endswith("\\Tesseract-OCR\\tesseract.exe")
        ]

    for command in commands:
        if shutil.which(command) or Path(command).exists():
            return command, ""

    if commands:
        return "", "tesseract not found; tried: " + ", ".join(commands)
    return "", "tesseract not configured"


def _render_first_page_with_pdfplumber(pdf_path: str, dpi: int) -> tuple[bytes, str]:
    """Render physical page 1 using pdfplumber's built-in renderer (pure Python).

    This is the most portable method — it needs only pdfplumber + Pillow,
    both of which are already project dependencies.  Works on Windows, Linux,
    and macOS without any native binaries.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return b"", "pdfplumber: PDF has no pages"
            img = pdf.pages[0].to_image(resolution=dpi)
            buf = io.BytesIO()
            img.original.save(buf, format="PNG")
            return buf.getvalue(), ""
    except Exception as exc:
        return b"", f"pdfplumber render failed: {exc}"


def _render_first_page_with_pdftoppm(pdf_path: str, dpi: int) -> tuple[bytes, str]:
    """Render physical page 1 with the pdftoppm command (Linux/macOS only)."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "cmets_page"
            subprocess.run(
                [
                    "pdftoppm",
                    "-f", "1",
                    "-l", "1",
                    "-png",
                    "-r", str(dpi),
                    pdf_path,
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
            )
            images = sorted(Path(tmpdir).glob("cmets_page-*.png"))
            if not images:
                return b"", "pdftoppm did not produce a page image"
            return images[0].read_bytes(), ""
    except subprocess.TimeoutExpired:
        return b"", "pdftoppm timed out"
    except Exception as exc:
        return b"", f"pdftoppm failed: {exc}"


def _render_first_page_with_pdf2image(pdf_path: str, dpi: int) -> tuple[bytes, str]:
    """Render physical page 1 with the optional pdf2image package."""
    try:
        from pdf2image import convert_from_path
    except Exception as exc:
        return b"", f"pdf2image not available: {exc}"

    try:
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            first_page=1,
            last_page=1,
            fmt="png",
            thread_count=1,
        )
        if not images:
            return b"", "pdf2image did not produce a page image"

        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue(), ""
    except Exception as exc:
        return b"", f"pdf2image failed: {exc}"


def _render_first_page_png_bytes(pdf_path: str, dpi: int = 200) -> tuple[bytes, str]:
    """Render physical page 1 as PNG bytes.

    Fallback chain (first success wins):
      1. pdfplumber  — pure-Python, works everywhere (Windows + Linux)
      2. pdftoppm    — CLI tool, Linux/macOS only
      3. pdf2image   — needs poppler installed
    """
    errors: list[str] = []

    # --- Attempt 1: pdfplumber (pure-Python, always available) ---
    image_bytes, error = _render_first_page_with_pdfplumber(pdf_path, dpi)
    if image_bytes:
        logger.debug("[MeetingClassifier] Page 1 rendered via pdfplumber")
        return image_bytes, ""
    errors.append(error)

    # --- Attempt 2: pdftoppm (Linux/macOS CLI tool) ---
    if not _IS_WINDOWS and shutil.which("pdftoppm"):
        image_bytes, error = _render_first_page_with_pdftoppm(pdf_path, dpi)
        if image_bytes:
            logger.debug("[MeetingClassifier] Page 1 rendered via pdftoppm")
            return image_bytes, ""
        errors.append(error)
    else:
        errors.append("pdftoppm not available" if _IS_WINDOWS else "pdftoppm not installed")

    # --- Attempt 3: pdf2image ---
    image_bytes, error = _render_first_page_with_pdf2image(pdf_path, dpi)
    if image_bytes:
        logger.debug("[MeetingClassifier] Page 1 rendered via pdf2image")
        return image_bytes, ""
    errors.append(error)

    return b"", "; ".join(err for err in errors if err)


def _ocr_with_llm_vision(
    image_bytes: bytes,
    vm_mode: bool,
    api_key: Optional[str],
    llm_script_path: Optional[str],
) -> OCRResult:
    """OCR using GPT-4o-mini vision — zero native dependencies needed.

    Sends the rendered page image to the LLM and asks it to extract ALL
    visible text.  This is the primary OCR method because it works on
    every platform (Windows, Linux, macOS) without Tesseract or poppler.
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    prompt = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an OCR assistant. Extract ALL visible text from the "
                    "provided image exactly as it appears, preserving line breaks. "
                    "Do NOT add any commentary or explanation — return ONLY the "
                    "raw text content."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    },
                    {
                        "type": "text",
                        "text": "Extract all text from this PDF page image.",
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 3000,
    }

    try:
        resp = call_llm(
            prompt,
            vm=vm_mode,
            api_key=api_key,
            model=MODEL,
            script_path=llm_script_path,
        )
        text = extract_text_from_response(resp)
        return OCRResult(
            text=text or "",
            available=True,
            command=f"llm_vision ({MODEL})",
        )
    except Exception as exc:
        return OCRResult(
            available=True,
            error=f"LLM vision OCR failed: {exc}",
            command=f"llm_vision ({MODEL})",
        )


def _ocr_with_tesseract_subprocess(image_bytes: bytes, tesseract_cmd: str) -> OCRResult:
    """OCR using tesseract CLI via subprocess (optional fallback).

    Uses delete=False on NamedTemporaryFile to avoid Windows file-locking
    issues (Windows cannot open a file that another process has open).
    """
    tmp_path = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(
            suffix=".png", delete=False,
        )
        tmp_path = tmp_file.name
        tmp_file.write(image_bytes)
        tmp_file.close()

        result = subprocess.run(
            [tesseract_cmd, tmp_path, "stdout", "--psm", "6"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return OCRResult(available=True, error="tesseract timed out", command=tesseract_cmd)
    except Exception as exc:
        return OCRResult(available=True, error=f"tesseract subprocess failed: {exc}", command=tesseract_cmd)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"tesseract exited with code {result.returncode}"
        return OCRResult(available=True, error=err, command=tesseract_cmd)

    return OCRResult(text=result.stdout or "", available=True, command=tesseract_cmd)


def _ocr_first_page_text(
    pdf_path: str,
    *,
    vm_mode: bool = False,
    api_key: Optional[str] = None,
    llm_script_path: Optional[str] = None,
) -> OCRResult:
    """OCR physical page 1 and return text plus availability/error details.

    Fallback chain:
      1. LLM Vision (GPT-4o-mini)  — zero native deps, works everywhere
      2. Tesseract CLI subprocess  — optional, only if tesseract is installed
    """
    print("  [MeetingClassifier] PDF text extraction failed — falling back to OCR...")
    logger.info("[MeetingClassifier] PDF text extraction failed — falling back to OCR")

    image_bytes, render_error = _render_first_page_png_bytes(pdf_path, dpi=220)
    if render_error:
        print(f"  [MeetingClassifier] ✗ Could not render page 1 to image: {render_error}")
        return OCRResult(error=render_error)

    print("  [MeetingClassifier] ✓ Page 1 rendered to image successfully")

    # --- Attempt 1: LLM Vision (always available, no native deps) ---
    print(f"  [MeetingClassifier] Trying OCR method: LLM Vision ({MODEL})...")
    ocr_result = _ocr_with_llm_vision(image_bytes, vm_mode, api_key, llm_script_path)
    if ocr_result.text.strip():
        print(f"  [MeetingClassifier] ✓ OCR succeeded via LLM Vision")
        return ocr_result
    if ocr_result.error:
        print(f"  [MeetingClassifier] ✗ LLM Vision: {ocr_result.error}")
        logger.debug("[MeetingClassifier] LLM Vision OCR failed: %s", ocr_result.error)

    # --- Attempt 2: tesseract CLI subprocess (only if installed) ---
    tesseract_cmd, command_error = _resolve_tesseract_command(require_configured=False)
    if command_error:
        print(f"  [MeetingClassifier] ✗ tesseract CLI: {command_error} (skipping)")
        # Return whatever LLM gave us (even if empty)
        return ocr_result

    print(f"  [MeetingClassifier] Trying OCR method: tesseract CLI ({tesseract_cmd})...")
    tess_result = _ocr_with_tesseract_subprocess(image_bytes, tesseract_cmd)
    if tess_result.text.strip():
        print(f"  [MeetingClassifier] ✓ OCR succeeded via tesseract CLI")
        return tess_result
    elif tess_result.error:
        print(f"  [MeetingClassifier] ✗ tesseract CLI: {tess_result.error}")
    else:
        print(f"  [MeetingClassifier] ✗ tesseract CLI returned empty text")

    # Return best available result
    return ocr_result if ocr_result.text.strip() else tess_result

def _first_page_png_base64(pdf_path: str) -> str:
    """Render physical page 1 as PNG and return base64 for JSON diagnostics."""
    image_bytes, render_error = _render_first_page_png_bytes(pdf_path, dpi=72)
    if render_error:
        logger.debug("[MeetingClassifier] first-page render failed for %s: %s", pdf_path, render_error)
        return ""
    return base64.b64encode(image_bytes).decode("ascii")


# ── GNA vs LTA keyword ratio ────────────────────────────────────────────────

# Patterns used to count GNA vs LTA presence.
# We scan the ENTIRE PDF (not just page 1) for a representative ratio.
_GNA_PATTERNS = [
    r"\bGNA\b",
    r"\bST[\s-]*II\b",
    r"\bStage[\s-]*II\b",
    r"\bGNA/ST\s*II\b",
]

_LTA_PATTERNS = [
    r"\bLTA\b",
    r"\bLong\s+Term\s+Access\b",
]


def _count_keywords(full_text: str) -> tuple[int, int]:
    """Count GNA-related and LTA-related keyword occurrences."""
    gna_count = sum(
        len(re.findall(pat, full_text, re.IGNORECASE))
        for pat in _GNA_PATTERNS
    )
    lta_count = sum(
        len(re.findall(pat, full_text, re.IGNORECASE))
        for pat in _LTA_PATTERNS
    )
    return gna_count, lta_count


def _classify(gna_count: int, lta_count: int) -> str:
    """Classify by whichever keyword count is higher; ties default to GNA."""
    return "LTA" if lta_count > gna_count else "GNA"


# ── Public API ───────────────────────────────────────────────────────────────

def classify_meeting(
    pdf_path: str,
    *,
    vm_mode: bool = False,
    api_key: Optional[str] = None,
    llm_script_path: Optional[str] = None,
) -> MeetingMeta:
    """Extract meeting metadata from a CMETS PDF.

    Steps:
      1. Read the first readable meeting page → extract meeting number + date
      2. Read ALL pages → count GNA vs LTA keywords
      3. Classify as "GNA" or "LTA" based on whichever count is higher
      4. Place meeting number + date into the appropriate columns

    Parameters
    ----------
    pdf_path : str
        Absolute path to the CMETS PDF.
    vm_mode : bool
        If True, use VM batch script for LLM calls.
    api_key : str | None
        OpenAI API key for direct LLM calls.
    llm_script_path : str | None
        Path to LLM batch script (VM mode).

    Returns
    -------
    MeetingMeta
        Contains the 4 column values to inject into every row.
    """
    meta = MeetingMeta()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                logger.warning("[MeetingClassifier] PDF has no pages: %s", pdf_path)
                return meta

            # ── Step 1: First physical page ────────────────────────────────
            #
            # CMETS meeting dates are expected on physical page 1 only. Try
            # normal PDF text first; if no date is found, OCR physical page 1
            # with configured Tesseract and save that OCR text in JSON.
            first_physical_page_pdf_text = pdf.pages[0].extract_text(
                x_tolerance=3, y_tolerance=3,
            ) or ""
            meta.first_page_image_base64 = _first_page_png_base64(pdf_path)
            meta.first_page_image_mime_type = "image/png" if meta.first_page_image_base64 else ""
            meta.first_readable_page_number = 1 if first_physical_page_pdf_text.strip() else None

            # Keep the broader meeting-number fallback, but do not use these
            # pages for meeting-date extraction.
            early_page_text_parts: list[str] = []
            for idx, page in enumerate(pdf.pages, 1):
                candidate = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if idx <= 5 and candidate.strip():
                    early_page_text_parts.append(candidate)
            early_pages_text = "\n".join(early_page_text_parts)

            meta.meeting_number = _extract_meeting_number_from_filename(pdf_path)
            if not meta.meeting_number:
                meta.meeting_number = (
                    _extract_meeting_number(first_physical_page_pdf_text)
                    or _extract_meeting_number(early_pages_text)
                )

            # ── Meeting date: try PDF text first, then OCR ────────────────
            print(f"  [MeetingClassifier] Attempting date extraction via: page_1_pdf_text")
            meta.meeting_date = _extract_meeting_date(first_physical_page_pdf_text)
            if meta.meeting_date:
                meta.meeting_date_method = "page_1_pdf_text"
                print(f"  [MeetingClassifier] ✓ Date found via page_1_pdf_text: {meta.meeting_date}")

            if not meta.meeting_date:
                print(f"  [MeetingClassifier] ✗ No date found in PDF text — attempting OCR fallback")
                first_page_ocr = _ocr_first_page_text(
                    pdf_path,
                    vm_mode=vm_mode,
                    api_key=api_key,
                    llm_script_path=llm_script_path,
                )
                meta.first_page_ocr_text = first_page_ocr.text
                meta.first_page_ocr_available = first_page_ocr.available
                meta.first_page_ocr_error = first_page_ocr.error
                meta.first_page_ocr_command = first_page_ocr.command
                meta.first_page_text = first_page_ocr.text
                meta.first_page_text_source = "ocr" if first_page_ocr.text.strip() else "empty"
                meta.meeting_date = _extract_meeting_date(first_page_ocr.text)
                meta.meeting_date_method = "page_1_ocr" if meta.meeting_date else "not_found"
                if meta.meeting_date:
                    print(f"  [MeetingClassifier] ✓ Date found via page_1_ocr: {meta.meeting_date}")
                else:
                    print(f"  [MeetingClassifier] ✗ No date found via OCR either")
                if not meta.meeting_number:
                    meta.meeting_number = _extract_meeting_number(first_page_ocr.text)
            else:
                meta.first_page_text = first_physical_page_pdf_text
                meta.first_page_text_source = "pdf_text" if first_physical_page_pdf_text.strip() else "empty"

            if not meta.meeting_number:
                logger.info(
                    "[MeetingClassifier] Could not extract meeting number from: %s",
                    pdf_path,
                )

            if not meta.meeting_date:
                logger.info(
                    "[MeetingClassifier] Could not extract meeting date from physical page 1: %s",
                    pdf_path,
                )

            # ── Step 2: Full PDF — count GNA vs LTA keywords ──────────────
            all_text_parts: list[str] = []
            for page in pdf.pages:
                t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if t.strip():
                    all_text_parts.append(t)
            full_text = "\n".join(all_text_parts)

            gna_count, lta_count = _count_keywords(full_text)
            meta.gna_count = gna_count
            meta.lta_count = lta_count

    except Exception as exc:
        logger.error("[MeetingClassifier] Failed to read PDF %s: %s", pdf_path, exc)
        return meta

    # ── Step 3: Classify ──────────────────────────────────────────────────
    meta.classification = _classify(meta.gna_count, meta.lta_count)

    # ── Step 4: Place values into the correct columns ─────────────────────
    if meta.classification == "GNA":
        meta.cmets_gna_approved = meta.meeting_number
        meta.cmets_gna_meeting_date = meta.meeting_date
    else:
        meta.cmets_lta_approved = meta.meeting_number
        meta.cmets_lta_meeting_date = meta.meeting_date

    summary = (
        f"  [MeetingClassifier] RESULT: Meeting #{meta.meeting_number or '?'}, "
        f"Date {meta.meeting_date or 'not found'} "
        f"(method: {meta.meeting_date_method or 'N/A'}), "
        f"GNA: {meta.gna_count}, LTA: {meta.lta_count} → {meta.classification}"
    )
    print(summary)
    logger.info(
        "[MeetingClassifier] %s — Meeting #%s, Date %s, "
        "GNA: %d, LTA: %d → %s",
        pdf_path, meta.meeting_number, meta.meeting_date,
        meta.gna_count, meta.lta_count, meta.classification,
    )

    return meta
