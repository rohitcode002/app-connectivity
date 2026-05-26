"""
PDF Data Extractor — page-wise raw text and Markdown tables
===========================================================
Extracts content from a PDF per page using two methods:
  1. pdfplumber  — raw text only
  2. camelot     — tables rendered as Markdown

Output structure
----------------
<pdf_name>/
  pdfplumber/
    page_01.txt
    page_02.txt
    ...
  camelot/
    page_01.txt
    page_02.txt
    ...

Dependencies
------------
  pip install pdfplumber camelot-py[cv] opencv-python-headless PyMuPDF
  # Ghostscript required for camelot:
  # Ubuntu/Debian : sudo apt install ghostscript
  # macOS         : brew install ghostscript
"""

import os
import sys

# =============================================================================
# CONFIGURATION — change only this block
# =============================================================================

PDF_PATH = "42th.pdf"       # <- hardcoded PDF filename / path
CAMELOT_FLAVOR = "lattice"    # 'lattice' (bordered tables) | 'stream' (whitespace tables)

# =============================================================================


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def page_label(page_num: int, total: int) -> str:
    """Zero-padded page label, e.g. '03' for a 50-page doc."""
    return str(page_num).zfill(len(str(total)))


def get_output_root(pdf_path: str) -> str:
    """'reports/sample.pdf' -> 'sample'"""
    base = os.path.basename(pdf_path)
    name, _ = os.path.splitext(base)
    return name


def rows_to_markdown(rows: list) -> str:
    """
    Convert a list of rows (list of lists) to a Markdown table string.

    Example output:
      | Col A | Col B | Col C |
      |-------|-------|-------|
      | val1  | val2  | val3  |
    """
    if not rows:
        return "(empty table)"

    # Normalise every cell to a string and strip newlines
    cleaned = []
    for row in rows:
        cleaned.append([str(cell or "").replace("\n", " ").strip() for cell in row])

    # Calculate column widths for neat alignment
    col_count = max(len(row) for row in cleaned)

    # Pad all rows to the same column count
    for row in cleaned:
        while len(row) < col_count:
            row.append("")

    col_widths = [
        max(len(row[c]) for row in cleaned)
        for c in range(col_count)
    ]
    col_widths = [max(w, 3) for w in col_widths]  # minimum width of 3 for the separator

    def fmt_row(row):
        cells = [row[c].ljust(col_widths[c]) for c in range(col_count)]
        return "| " + " | ".join(cells) + " |"

    def separator():
        dashes = ["-" * col_widths[c] for c in range(col_count)]
        return "| " + " | ".join(dashes) + " |"

    lines = []
    lines.append(fmt_row(cleaned[0]))   # header row
    lines.append(separator())           # separator
    for row in cleaned[1:]:             # data rows
        lines.append(fmt_row(row))

    return "\n".join(lines)


# -- pdfplumber extraction ----------------------------------------------------

def extract_with_pdfplumber(pdf_path: str, root_dir: str) -> None:
    try:
        import pdfplumber
    except ImportError:
        print("[pdfplumber] Not installed. Run:  pip install pdfplumber")
        return

    dest = ensure_dir(os.path.join(root_dir, "pdfplumber"))

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"\n[pdfplumber] {total} page(s) found — saving raw page text to: {dest}")

        for i, page in enumerate(pdf.pages, start=1):
            label = page_label(i, total)
            text = page.extract_text() or ""
            content = text if text else "(no text found)"

            out_file = os.path.join(dest, f"page_{label}.txt")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(content)

            print(f"  Page {i}/{total} -> {os.path.basename(out_file)}  "
                  f"({len(text)} text chars)")

    print("[pdfplumber] Done.\n")


# -- camelot extraction -------------------------------------------------------

def extract_with_camelot(pdf_path: str, root_dir: str, flavor: str = "lattice") -> None:
    try:
        import camelot
    except ImportError:
        print("[camelot]   Not installed. Run:  pip install 'camelot-py[cv]'")
        return

    # Get page count
    total = None
    try:
        import fitz
        total = fitz.open(pdf_path).page_count
    except ImportError:
        pass

    if total is None:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                total = len(pdf.pages)
        except ImportError:
            print("[camelot]   Cannot determine page count. Install PyMuPDF or pdfplumber.")
            return

    dest = ensure_dir(os.path.join(root_dir, "camelot"))
    print(f"\n[camelot]   {total} page(s) found. Flavor='{flavor}' — saving to: {dest}")

    for page_num in range(1, total + 1):
        label = page_label(page_num, total)
        lines = []

        try:
            tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor=flavor)
        except Exception as exc:
            lines.append(f"ERROR reading page {page_num}: {exc}")
            tables = []

        if len(tables) == 0:
            lines.append(f"{'='*60}")
            lines.append(f"PAGE {page_num} — no tables detected")
            lines.append(f"{'='*60}")
        else:
            for t_idx, table in enumerate(tables, start=1):
                acc = table.parsing_report.get("accuracy", "n/a")
                lines.append(f"{'='*60}")
                lines.append(f"PAGE {page_num} — TABLE {t_idx}  (accuracy: {acc})")
                lines.append(f"{'='*60}")
                # Convert DataFrame to list-of-lists for the Markdown renderer
                rows = [table.df.columns.tolist()] + table.df.values.tolist()
                lines.append(rows_to_markdown(rows))

        out_file = os.path.join(dest, f"page_{label}.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"  Page {page_num}/{total} -> {os.path.basename(out_file)}  "
              f"({len(tables)} table(s) found)")

    print("[camelot]   Done.\n")


# -- main ---------------------------------------------------------------------

def main() -> None:
    pdf_path = PDF_PATH

    if not os.path.isfile(pdf_path):
        print(f"ERROR: File not found: '{pdf_path}'", file=sys.stderr)
        print("       Update PDF_PATH at the top of this script.", file=sys.stderr)
        sys.exit(1)

    root_dir = get_output_root(pdf_path)

    print("=" * 60)
    print("  PDF Extractor  (pdfplumber raw text + Camelot Markdown tables)")
    print(f"  Input          : {pdf_path}")
    print(f"  pdfplumber  -> : {root_dir}/pdfplumber/")
    print(f"  camelot     -> : {root_dir}/camelot/")
    print(f"  Camelot flavor : {CAMELOT_FLAVOR}")
    print("=" * 60)

    extract_with_pdfplumber(pdf_path, root_dir)
    extract_with_camelot(pdf_path, root_dir, flavor=CAMELOT_FLAVOR)

    print("=" * 60)
    print("  All done! Open matching page_NN.txt files side-by-side to compare.")
    print(f"  Results under: ./{root_dir}/")
    print("=" * 60)


if __name__ == "__main__":
    main()