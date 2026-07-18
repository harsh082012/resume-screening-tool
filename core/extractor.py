"""
extractor.py
------------
Extracts raw text from resumes stored in a folder or uploaded. Supports
PDF, DOCX, TXT, and now image formats (PNG/JPG/etc.) via OCR.

This is the "real-time" ingestion layer of the Resume Screening AI Tool.

OCR behaviour (new):
    - Normal text extraction (pdfplumber / python-docx) is always tried FIRST.
    - Only if it returns almost no text (a scanned/image-only PDF) does the
      code fall back to Tesseract OCR. OCR is slow, so it is never run on the
      majority of resumes that already have selectable text.
    - Image files (.png/.jpg/etc.) go straight to OCR, since there's no text
      layer to extract.
    - OCR runs locally with zero API calls, so it costs nothing and doesn't
      touch your LLM free-tier quota.
"""

import os
import re
from pathlib import Path

# Image formats we can OCR directly.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}

# Everything the tool can ingest.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"} | IMAGE_EXTENSIONS

# If normal extraction yields fewer than this many non-whitespace characters,
# we assume the file is scanned/image-only and try OCR instead.
OCR_TEXT_THRESHOLD = 100


def extract_text_from_pdf(filepath: str) -> str:
    """
    Extract text from a PDF using pdfplumber, falling back to OCR if the PDF
    turns out to be scanned (little or no selectable text).
    """
    import pdfplumber

    text_chunks = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_chunks.append(page_text)
    text = "\n".join(text_chunks)

    # Scanned / image-only PDFs have (almost) no extractable text -> try OCR.
    if len(text.strip()) < OCR_TEXT_THRESHOLD:
        text = _maybe_ocr_pdf(filepath, text)

    return text


def _maybe_ocr_pdf(filepath: str, existing_text: str) -> str:
    """
    Attempt OCR on a PDF and return whichever result has more text. If OCR is
    unavailable (Tesseract not installed) or fails, we log a warning and keep
    whatever little text we already had, rather than crashing the batch.
    """
    try:
        from core.ocr import ocr_pdf
        ocr_text = ocr_pdf(filepath)
    except Exception as e:  # OCRUnavailableError, poppler errors, etc.
        print(f"[WARN] OCR fallback skipped for {os.path.basename(filepath)}: {e}")
        return existing_text

    return ocr_text if len(ocr_text.strip()) > len(existing_text.strip()) else existing_text


def extract_text_from_docx(filepath: str) -> str:
    """Extract text from a DOCX using python-docx."""
    import docx

    doc = docx.Document(filepath)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

    # Also pull text out of tables (many resumes use table layouts)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    paragraphs.append(cell.text)

    return "\n".join(paragraphs)


def extract_text_from_txt(filepath: str) -> str:
    """Extract text from a plain text file."""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text_from_image(filepath: str) -> str:
    """OCR an image resume (.png/.jpg/etc.) into text."""
    from core.ocr import ocr_image
    return ocr_image(filepath)


def clean_text(text: str) -> str:
    """Normalize whitespace so the LLM gets tidy input."""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_text(filepath: str) -> str:
    """Dispatch to the right extractor based on file extension."""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        raw = extract_text_from_pdf(filepath)
    elif ext in (".docx", ".doc"):
        raw = extract_text_from_docx(filepath)
    elif ext == ".txt":
        raw = extract_text_from_txt(filepath)
    elif ext in IMAGE_EXTENSIONS:
        raw = extract_text_from_image(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return clean_text(raw)


def guess_candidate_name(filename: str, resume_text: str) -> str:
    """
    Best-effort candidate name guess: prefer the first non-empty line of the
    resume if it looks like a name, otherwise fall back to the filename.
    """
    first_line = ""
    for line in resume_text.splitlines():
        line = line.strip()
        if line:
            first_line = line
            break

    looks_like_name = (
        first_line
        and len(first_line.split()) <= 5
        and len(first_line) <= 60
        and not any(ch.isdigit() for ch in first_line)
        and "@" not in first_line
    )
    if looks_like_name:
        return first_line

    return Path(filename).stem.replace("_", " ").replace("-", " ").title()


def load_resumes_from_folder(folder_path: str) -> list:
    """
    Extract every supported resume in a folder (now including image files).

    Returns a list of dicts:
        {"filename": str, "candidate_name": str, "text": str}
    Files that fail to parse are skipped with a printed warning rather than
    crashing the whole batch run.
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Resume folder not found: {folder_path}")

    resumes = []
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:
                text = extract_text(str(entry))
                if not text.strip():
                    print(f"[WARN] No extractable text in {entry.name}, skipping.")
                    continue
                resumes.append(
                    {
                        "filename": entry.name,
                        "candidate_name": guess_candidate_name(entry.name, text),
                        "text": text,
                    }
                )
            except Exception as e:
                print(f"[WARN] Failed to parse {entry.name}: {e}")

    return resumes