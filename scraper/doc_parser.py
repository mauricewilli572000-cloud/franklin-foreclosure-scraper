"""
PDF document extraction for Franklin County Clerk case filings
================================================================

Each E-Foreclosure case on the clerk portal links to a ZIP bundle of the
filed PDF documents (complaint, notice of default, lis pendens, judgment
entry, etc.). The results grid only exposes a handful of fields, so the
real data — interest rate, amount owed, loan type, full legal
description, and often the grantor/grantee names themselves — has to be
read out of those PDFs.

This module:
1. Unpacks the PDFs from a downloaded ZIP bundle.
2. Extracts text per page with pdfplumber. Franklin County court filings
   are a mix of typed originals and scanned/stamped documents, so any
   page whose extracted text is too short to be real (a scan with no
   text layer) is rasterized with PyMuPDF and run through Tesseract OCR
   instead.
3. Regex-parses the combined text for the fields the lead scraper needs.

Nothing here raises on a malformed file — a bad PDF, a bad zip, or a
field that just isn't present in a given filing all degrade to an empty
string / None rather than crashing the run.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Optional

import pdfplumber
import pytesseract
from PIL import Image

try:
    import pymupdf as fitz  # PyMuPDF — used only to rasterize pages for OCR
except ImportError:  # pragma: no cover
    try:
        import fitz  # older PyMuPDF versions expose the same API as `fitz`
    except ImportError:
        fitz = None

log = logging.getLogger("franklin_scraper.doc_parser")

MIN_TEXT_CHARS_PER_PAGE = 25   # below this, a page is treated as scanned/image-only
CID_GARBAGE_THRESHOLD = 5      # 5+ "(cid:N)" placeholders means the page's font mapping is broken
OCR_DPI = 300
MAX_PAGES_PER_PDF = 40         # safety cap so one runaway filing can't hang the run


# --------------------------------------------------------------------------
# Zip / PDF extraction
# --------------------------------------------------------------------------

def extract_pdfs_from_zip(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Return [(filename, pdf_bytes), ...] for every PDF in a case bundle."""
    pdfs: list[tuple[str, bytes]] = []
    if not zip_bytes:
        return pdfs
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".pdf"):
                    try:
                        pdfs.append((name, zf.read(name)))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Could not read %s from case bundle: %s", name, exc)
    except zipfile.BadZipFile as exc:
        log.warning("Downloaded case bundle is not a valid zip: %s", exc)
    return pdfs


def _ocr_page(pdf_bytes: bytes, page_index: int, filename: str = "") -> str:
    """Rasterize one page with PyMuPDF and OCR it with Tesseract."""
    if fitz is None:
        log.warning("PyMuPDF not installed; cannot OCR scanned/garbled page %d of %s", page_index, filename)
        return ""
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page_index >= doc.page_count:
            log.warning(
                "OCR skipped for %s: page index %d out of range (doc has %d pages)",
                filename, page_index, doc.page_count,
            )
            return ""
        page = doc[page_index]
        zoom = OCR_DPI / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        result = pytesseract.image_to_string(img)
        if not result.strip():
            log.warning("OCR produced no text for %s page %d (image may be blank or unreadable)", filename, page_index)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR failed on %s page %d: %s", filename, page_index, exc)
        return ""
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass


def _looks_garbled(text: str) -> bool:
    """Some PDFs embed a font with a broken/missing character map — text
    extraction then succeeds (plenty of characters come back, so the
    MIN_TEXT_CHARS_PER_PAGE check alone won't catch it) but the output is
    literal glyph-ID placeholders like "(cid:12)(cid:3)(cid:47)" instead
    of real letters. This flags that failure mode so those pages still
    fall back to OCR instead of silently keeping garbage text."""
    return text.count("(cid:") >= CID_GARBAGE_THRESHOLD


def extract_text_hybrid(pdf_bytes: bytes, filename: str = "") -> str:
    """pdfplumber text extraction with a per-page Tesseract OCR fallback
    for scanned pages OR pages whose extracted text is garbled cid-code
    placeholders. Always returns a string (possibly empty)."""
    if not pdf_bytes:
        return ""

    pages_text: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages[:MAX_PAGES_PER_PDF]):
                text = ""
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    log.debug("%s page %d: pdfplumber extraction failed: %s", filename, i, exc)

                if len(text.strip()) < MIN_TEXT_CHARS_PER_PAGE or _looks_garbled(text):
                    ocr_text = _ocr_page(pdf_bytes, i, filename)
                    if ocr_text.strip() and not _looks_garbled(ocr_text):
                        text = ocr_text
                    elif _looks_garbled(text):
                        log.warning(
                            "%s page %d: text extraction was garbled and OCR fallback "
                            "didn't produce a usable replacement — keeping garbled text",
                            filename, i,
                        )

                pages_text.append(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not open PDF %s: %s", filename, exc)
        return ""

    return "\n".join(pages_text)


# --------------------------------------------------------------------------
# Field parsing
# --------------------------------------------------------------------------
# NOTE: these regexes are written against common Ohio common-pleas
# foreclosure-filing phrasing (Case No / Plaintiff / Defendant / Interest
# Rate / Principal Balance / Legal Description). Franklin County's actual
# templates vary by filer (bank counsel firms all format differently), so
# treat this as a first pass — pull a handful of real downloaded PDFs and
# tune the patterns below against them. Every pattern is tried in order
# and the first match wins; add more candidate patterns as you find them.

FIELD_PATTERNS: dict[str, list[str]] = {
    "case_number": [
        # The footer-stamp format (2-digit year + CV + digits, e.g.
        # "26CV007738") is unambiguous and checked first. The label-based
        # pattern is scoped case-SENSITIVE — (?-i:...) — because the
        # global IGNORECASE flag applied to every pattern here would
        # otherwise make the literal words "Case"/"No" match the common
        # lowercase English words "case"/"no" anywhere in ordinary legal
        # prose, and turn [A-Z0-9\-] into "any letter at all", which is
        # how this once matched the word "Plaintiff". A digit is also
        # required in the captured value as a second safety net.
        r"\b(\d{2}CV\d{5,8})\b",
        r"(?-i:CASE\s*(?:NO\.?|NUMBER)|Case\s*(?:No\.?|Number))\s*[:\-]?\s*((?=[A-Z0-9\-]*\d)[A-Z0-9\-]{6,20})",
    ],
    "doc_type": [
        r"(?:Document|Filing)\s*Type\s*[:\-]?\s*([A-Za-z /]+)",
        r"\b(COMPLAINT(?:\s+(?:IN|FOR)\s+FORECLOSURE)?|NOTICE OF DEFAULT|LIS PENDENS|JUDGMENT ENTRY(?:\s+(?:IN|OF)\s+FORECLOSURE)?|DECREE (?:IN|OF) FORECLOSURE|ORDER OF SALE)\b",
    ],
    "filed": [
        r"(?:Date\s*Filed|Filed\s*Date|Filed\s*On|Electronically\s*Filed)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
    ],
    # grantor/grantee are handled primarily by extract_caption_names()
    # below, which understands the real Ohio-complaint caption layout
    # (name BEFORE the "Plaintiff"/"Defendant" label, with a mailing
    # address in between). These generic patterns are kept only as a
    # fallback for documents that don't match that layout at all.
    "grantor": [
        r"Defendant[s]?[:\-]?\s*\n?\s*([A-Z][A-Za-z0-9,.\-' ]+?)(?:\n|,\s*(?:et al\.?|Defendant))",
        r"(?:Property\s*Owner|Owner\s*of\s*Record)\s*[:\-]?\s*([A-Z][A-Za-z0-9,.\-' ]+)",
    ],
    "grantee": [
        r"Plaintiff[s]?[:\-]?\s*\n?\s*([A-Z][A-Za-z0-9,.\-'& ]+?)(?:\n|,\s*Plaintiff)",
    ],
    "interest_rate": [
        r"[Ii]nterest\s*[Rr]ate\s*(?:of)?\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,5})?)\s*(?:%|percent)\b",
        r"at\s+the\s+rate\s+of\s+(\d{1,2}(?:\.\d{1,5})?)\s*(?:%|percent)\b",
    ],
    "amount": [
        r"principal\s+amount\s+of\s+\$\s*([\d,]+\.\d{2})",
        r"[Pp]rincipal\s*[Bb]alance(?:\s*[Dd]ue)?\s*(?:of)?\s*[:\-]?\s*\$\s*([\d,]+\.\d{2})",
        r"[Jj]udgment\s*[Aa]mount\s*[:\-]?\s*\$\s*([\d,]+\.\d{2})",
        r"(?:[Aa]mount\s*(?:[Dd]ue|[Oo]wed)|sum\s*of)\s*[:\-]?\s*\$\s*([\d,]+\.\d{2})",
    ],
    "loan_type": [
        r"[Ll]oan\s*[Tt]ype\s*[:\-]?\s*([A-Za-z0-9 /\-]+)",
        r"\b(FHA|VA|USDA|Conventional|Adjustable[- ]Rate|Fixed[- ]Rate|Reverse Mortgage)\b",
    ],
    "legal_description": [
        r"(?:Legal\s*Description|[Ll]egally\s*described\s*as\s*follows|bounded\s+and\s+described\s+as\s+follows)\s*[:\-]?\s*(.+?)(?:\n\s*\n|Parcel\s*(?:No|Number|ID))",
    ],
    # Different firms label this differently: "Parcel Number: 010-068600"
    # (hyphenated), "PPN: 010-095598-00" (hyphenated PPN), "PPN:
    # 01010571100" (contiguous-digit PPN), or "Parcel ID Number:
    # 01009559800" (contiguous digits, extra "Number" word). All four
    # confirmed against real filings.
    "parcel_number": [
        r"Parcel\s*(?:ID\s*)?(?:Number|No\.?)\s*[:\-#]?\s*(\d{2,3}-\d{3,8}(?:-\d{1,3})?)",
        r"\bPPN\s*#?\s*[:\-]?\s*(\d{2,3}-\d{3,8}(?:-\d{1,3})?)",
        r"\bPPN\s*#?\s*[:\-]?\s*(\d{8,14})\b",
        r"\bParcel\s*ID\s*(?:Number|No\.?)?\s*[:\-#]?\s*(\d{8,14})\b",
    ],
}


# --------------------------------------------------------------------------
# Caption-based party extraction (plaintiff/defendant)
# --------------------------------------------------------------------------
# Ohio common-pleas complaint captions put each party's NAME before the
# "Plaintiff"/"Defendant" label, typically with a full mailing address
# squeezed in between, e.g.:
#
#   U.S. Bank National Association
#   2800 Tamarack Road
#   Owensboro, Kentucky 42301
#                                              Plaintiff
#   vs.                                        Case No. __________
#                                               Judge __________
#   Lisa A. Kinney, AKA Lisa Ann Kinney, AKA Lisa Kinney
#   915 South Hague Avenue
#   Columbus, OH 43204
#                                              Defendant(s).
#
# A generic "label THEN name" regex (which is what the FIELD_PATTERNS
# fallback above assumes) matches the label but not a name, since the
# name is on the *other* side of it. This walks the text directly
# instead: plaintiff is whatever sits just before the word "Plaintiff"
# (trimmed at the first digit, which starts the address); the primary
# defendant is the first real name-like word run after "vs." — found by
# skipping past known caption boilerplate ("Case No.", "Judge",
# "Complaint...") rather than assuming a fixed character count, since
# PDF text extraction doesn't reliably preserve line breaks.

# --------------------------------------------------------------------------
# Caption-based party extraction (plaintiff/defendant)
# --------------------------------------------------------------------------
# Ohio common-pleas complaint captions put each party's NAME before the
# "Plaintiff"/"Defendant" label, typically with a full mailing address
# squeezed in between. Two confirmed real layouts:
#
#   U.S. Bank National Association          <- Title Case firm
#   2800 Tamarack Road
#   Owensboro, Kentucky 42301
#                                              Plaintiff
#   vs.
#   Lisa A. Kinney, AKA Lisa Ann Kinney
#   915 South Hague Avenue
#
#   NEWREZ LLC D/B/A SHELLPOINT             <- ALL CAPS firm
#   MORTGAGE SERVICING
#   55 Beattie Place, Suite 500
#                                              Plaintiff,
#   vs.
#   COMPLAINT FOR FORECLOSURE
#   PPN: 010-095598-00
#   UNKNOWN HEIRS...OF STEVEN M. BURK, DECEASED
#   ADDRESS UNKNOWN
#   DOUG BURK
#   126 E. WATERLOO ST
#
# Different firms write party names in Title Case or ALL CAPS
# interchangeably, so letter-casing can't be used to tell a party's name
# apart from document boilerplate (an earlier version of this tried
# that and broke on ALL-CAPS filers). Instead this anchors on
# structural markers that are reliable regardless of casing: the
# court-header phrase ("...COUNTY, OHIO") for the plaintiff side, and
# the document title (already matched by FIELD_PATTERNS["doc_type"])
# for the defendant side — then cuts the name off at the first
# address-like text (a number followed by a word), which works
# regardless of how the name itself is styled.

def _clean_party_name(raw: str) -> str:
    """Trim a captured name+address(+boilerplate) blob down to just the
    name, cutting at whichever comes first:
    - a street address (digit followed by a word)
    - an "AKA" alias — the comma before it is optional, since some
      filers write "Kinney, AKA Lisa Ann Kinney" and others write
      "BROCKINGTON AKA SHAUNA TENILLE" with no comma at all
    - a "Case No"/"Judge" label bleeding in from a columnar caption
      layout that PDF text extraction linearized oddly (confirmed
      real: "U.S. BANK NATIONAL ASSOCIATION CASE NO: 2800 TAMARACK
      ROAD... JUDGE:" — the label sits between the name and its own
      address, not after it, so the address cutoff alone doesn't
      remove it)
    """
    raw = raw.strip()
    addr_match = re.search(r"\d{1,6}\s+[A-Za-z]", raw)
    aka_match = re.search(r",?\s*\b(?:AKA|aka)\b", raw)
    label_match = re.search(r"\bCASE\s*NO\.?\s*:?|\bJUDGE\s*:?", raw, re.IGNORECASE)
    cut_points = [m.start() for m in (addr_match, aka_match, label_match) if m]
    if cut_points:
        raw = raw[:min(cut_points)]
    return re.sub(r"\s+", " ", raw.strip(" ,.\n")) or None


def _first_defendant_with_real_address(text: str) -> Optional[str]:
    """Ohio foreclosures against an estate often list several potential
    defendants (unknown heirs, unknown spouses, etc.), many marked
    "ADDRESS UNKNOWN" — those aren't useful leads. This walks
    blank-line-separated blocks and returns the name of the first one
    that actually has a real street address, skipping "UNKNOWN SPOUSE
    OF..." entries (we want the named person, not their unknown
    spouse). Name cleanup (address/AKA/label cutoff) is shared with
    _clean_party_name so both sides of the caption get the same
    boundary handling."""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or re.search(r"ADDRESS\s+UNKNOWN", block, re.IGNORECASE):
            continue
        if not re.search(r"\d{1,6}\s+[A-Za-z]", block):
            continue
        name = _clean_party_name(block)
        if name and "UNKNOWN SPOUSE" not in name.upper():
            return name
    return None


DOC_TITLE_PATTERN = re.compile(
    r"\b(COMPLAINT(?:\s+(?:IN|FOR)\s+FORECLOSURE)?|NOTICE OF DEFAULT|LIS PENDENS|"
    r"JUDGMENT ENTRY(?:\s+(?:IN|OF)\s+FORECLOSURE)?|DECREE (?:IN|OF) FORECLOSURE|ORDER OF SALE)\b",
    re.IGNORECASE,
)


def extract_caption_names(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (plaintiff/grantee, primary_defendant/grantor) parsed from
    an Ohio-style complaint caption, or (None, None) if the text doesn't
    look like one."""
    grantee: Optional[str] = None
    grantor: Optional[str] = None

    # --- Plaintiff: text between the court-header phrase and "Plaintiff" ---
    # Real filings also carry the clerk's own e-filing stamp on every
    # page ("Franklin County Ohio Clerk of Courts of the Common
    # Pleas- 2026 Aug 18 11:03 AM-26CV007738"), which itself contains a
    # "County...Ohio"-shaped phrase and can appear ahead of the real
    # court-header line in extracted text. Taking the LAST such match
    # (closest to "Plaintiff") instead of the first avoids grabbing
    # stamp text as if it were part of the party name.
    plaintiff_match = re.search(r"(.{0,400}?)\bPlaintiff[s]?\b", text, re.IGNORECASE | re.DOTALL)
    if plaintiff_match:
        window = plaintiff_match.group(1)
        header_matches = list(re.finditer(r"COUNTY,?\s+OHIO\b", window, re.IGNORECASE))
        if header_matches:
            window = window[header_matches[-1].end():]
        grantee = _clean_party_name(window)

    # --- Defendant: text after the document title (and an optional PPN/
    # parcel-number line right after it), up to the first defendant that
    # actually has a real address ---
    doc_title_match = DOC_TITLE_PATTERN.search(text)
    if doc_title_match:
        remainder = text[doc_title_match.end():]
        ppn_match = re.match(
            r"\s*(?:PPN|Parcel\s*(?:ID)?\s*(?:Number|No\.?)?)\s*[:\-]?\s*[\d\-]{6,17}",
            remainder, re.IGNORECASE,
        )
        if ppn_match:
            remainder = remainder[ppn_match.end():]
        grantor = _first_defendant_with_real_address(remainder[:1500])
        if not grantor:
            # No block had a usable address (all "ADDRESS UNKNOWN", or
            # layout didn't match) — fall back to just the first
            # name-like chunk so there's still *something*.
            grantor = _clean_party_name(remainder[:200])
    else:
        # No recognizable document title at all — fall back to
        # whatever's right after "vs."
        vs_match = re.search(r"\bvs?\.?\b", text, re.IGNORECASE)
        if vs_match:
            grantor = _clean_party_name(text[vs_match.end():vs_match.end() + 200])

    return grantee, grantor
    if vs_match:
        remainder = text[vs_match.end():]
        start = _first_name_like_start(remainder)
        if start is not None:
            candidate = remainder[start:start + 150]
            grantor = _clean_party_name(candidate) or None

    return grantee, grantor


def parse_fields_from_text(text: str) -> dict[str, Optional[str]]:
    """Return {field_name: matched_value_or_None} for every field in
    FIELD_PATTERNS, searched across the combined text of all PDFs in a
    case bundle. Grantor/grantee are resolved primarily via
    extract_caption_names (which understands the real caption layout),
    falling back to the generic label-based patterns only if that
    doesn't find anything."""
    result: dict[str, Optional[str]] = {}
    if not text:
        return {field: None for field in FIELD_PATTERNS}

    for field, patterns in FIELD_PATTERNS.items():
        value = None
        for pattern in patterns:
            try:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            except re.error as exc:  # noqa: BLE001
                log.debug("Bad regex for field %s: %s", field, exc)
                continue
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" .,\n")
                break
        result[field] = value or None

    caption_grantee, caption_grantor = extract_caption_names(text)
    if caption_grantee:
        result["grantee"] = caption_grantee
    if caption_grantor:
        result["grantor"] = caption_grantor

    return result
