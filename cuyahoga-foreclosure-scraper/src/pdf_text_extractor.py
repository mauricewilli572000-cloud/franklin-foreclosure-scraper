"""
Hybrid PDF text extraction: pdfplumber first (fast, works for text-layer
PDFs), falling back to pytesseract OCR per page for scanned documents.

Known issue (see README / project memory): Tesseract can hang indefinitely
on GitHub Actions runners even though it works fine locally. Callers
running this in CI should wrap extract_text() in a timeout - see
run_pipeline.py's _extract_text_with_timeout().
"""

import io
from typing import Optional

import pdfplumber

try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


def _extract_with_pdfplumber(pdf_path: str) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def _extract_with_ocr(pdf_path: str) -> str:
    if not _OCR_AVAILABLE:
        return ""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            im = page.to_image(resolution=300).original
            text_parts.append(pytesseract.image_to_string(im))
    return "\n".join(text_parts)


def extract_text(pdf_path: str, min_text_chars: int = 40) -> str:
    """
    Try the text layer first; only fall back to OCR if too little text
    came back (i.e. the PDF is likely a scanned image).
    """
    text = ""
    try:
        text = _extract_with_pdfplumber(pdf_path)
    except Exception:
        text = ""

    if len(text.strip()) >= min_text_chars:
        return text

    try:
        ocr_text = _extract_with_ocr(pdf_path)
    except Exception:
        ocr_text = ""

    return ocr_text if len(ocr_text.strip()) > len(text.strip()) else text
