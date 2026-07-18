"""
core/ocr.py
-----------
Optical Character Recognition fallback for image-based resumes.

Used ONLY when normal text extraction (pdfplumber / python-docx) returns
little or no text -- i.e. the resume is a scanned document or a photo/image.
Runs entirely locally via Tesseract: it makes NO API calls and consumes NO
LLM quota, so it is free to run on every fallback without eating into your
Gemini/OpenRouter free-tier limits.

System dependencies (these are BINARIES, installed on the host -- not pip):
    - tesseract-ocr   (the OCR engine itself)
    - poppler-utils   (lets pdf2image turn PDF pages into images)

Local install:
    macOS:          brew install tesseract poppler
    Debian/Ubuntu:  sudo apt-get install -y tesseract-ocr poppler-utils

On Railway (or any nixpacks host), the included nixpacks.toml installs both.

Python deps (added to requirements.txt): pytesseract, pdf2image, Pillow
"""

import shutil


class OCRUnavailableError(Exception):
    """Raised when OCR is needed but the Tesseract binary isn't installed."""
    pass


def tesseract_available() -> bool:
    """True if the `tesseract` binary is on the system PATH."""
    return shutil.which("tesseract") is not None


def ocr_image(filepath: str) -> str:
    """OCR a single image file (.png / .jpg / .jpeg / etc.) into text."""
    if not tesseract_available():
        raise OCRUnavailableError(
            "Tesseract is not installed, so image resumes can't be read. "
            "Install it with `brew install tesseract` (macOS) or "
            "`sudo apt-get install -y tesseract-ocr` (Linux)."
        )

    import pytesseract
    from PIL import Image

    with Image.open(filepath) as img:
        return pytesseract.image_to_string(img)


def ocr_pdf(filepath: str, dpi: int = 200) -> str:
    """
    Rasterize each page of a PDF to an image and OCR it.

    Only called when a PDF has (almost) no selectable text -- i.e. it's a
    scanned document. dpi=200 is a good speed/quality balance for resumes;
    raise it to 300 if scans are low-resolution, but OCR gets slower.
    """
    if not tesseract_available():
        raise OCRUnavailableError(
            "Tesseract is not installed, so scanned PDFs can't be read. "
            "Install it with `brew install tesseract poppler` (macOS) or "
            "`sudo apt-get install -y tesseract-ocr poppler-utils` (Linux)."
        )

    import pytesseract
    from pdf2image import convert_from_path

    pages = convert_from_path(filepath, dpi=dpi)
    chunks = []
    for page_img in pages:
        page_text = pytesseract.image_to_string(page_img)
        if page_text.strip():
            chunks.append(page_text)
    return "\n".join(chunks)