"""
test_ocr.py
-----------
Quick standalone check that OCR is wired up correctly, BEFORE touching the
web app or Slack. Run it from the project root against any resume file:

    python test_ocr.py path/to/scanned_resume.pdf     # scanned PDF  -> OCR
    python test_ocr.py path/to/photo_resume.jpg        # image        -> OCR
    python test_ocr.py sample_resumes/arjun_mehta.txt  # normal file  -> no OCR

It reports whether Tesseract is installed, prints the extracted text so you
can eyeball the quality, and shows the character count against the OCR
threshold so you can see whether the OCR fallback would have triggered.
"""

import sys

from core.ocr import tesseract_available
from core.extractor import extract_text, OCR_TEXT_THRESHOLD


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_ocr.py <path-to-resume-file>")
        sys.exit(1)

    path = sys.argv[1]

    print(f"Tesseract installed: {tesseract_available()}")
    if not tesseract_available():
        print("  -> Image/scanned resumes will NOT work until you install it:")
        print("     macOS:  brew install tesseract poppler")
        print("     Linux:  sudo apt-get install -y tesseract-ocr poppler-utils")
    print()

    print(f"Extracting: {path}")
    print("-" * 60)
    try:
        text = extract_text(path)
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    print(text if text.strip() else "(no text extracted)")
    print("-" * 60)
    print(
        f"Extracted {len(text.strip())} characters "
        f"(OCR fallback threshold is {OCR_TEXT_THRESHOLD})."
    )


if __name__ == "__main__":
    main()