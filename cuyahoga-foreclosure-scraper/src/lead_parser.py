"""
Regex-based extraction from complaint PDF text, ported from the Franklin
County scraper.

NOTE: caption / loan_type / interest_rate wording has not yet been
double-checked against a real Cuyahoga complaint's extracted text (the
Case Summary tab already supplies the core fields, so this is a bonus
enrichment step only - see run_pipeline.py). Treat these patterns as a
starting point, not confirmed.
"""

import re
from dataclasses import dataclass
from typing import Optional

_AMOUNT_RE = re.compile(
    r"(?i)(?:sum of|amount of|principal (?:balance|sum) of)\s*\$?([\d,]+\.\d{2})"
)
# Ohio foreclosure complaints usually word this as "interest at the rate of
# 4.5% per annum" (or "bearing interest at 4.5%", "4.5 percent per annum"),
# so try the specific phrasings first, then a looser "a % figure shortly
# after the word interest" fallback. Wording is still unconfirmed against
# real Cuyahoga text - run_pipeline records samples when nothing matches.
_RATE_NUM = r"(\d{1,2}(?:\.\d{1,4})?)"
_PCT = r"\s*(?:%|percent\b)"
_INTEREST_RATE_PATTERNS = [
    re.compile(r"(?i)interest\s+(?:thereon\s+)?(?:at\s+)?(?:the\s+)?(?:annual\s+|contract\s+)?(?:rate\s+)?(?:of\s+)?" + _RATE_NUM + _PCT),
    re.compile(r"(?i)at\s+the\s+(?:annual\s+|contract\s+)?rate\s+of\s+" + _RATE_NUM + _PCT),
    re.compile(r"(?i)" + _RATE_NUM + _PCT + r"\s*(?:per\s+annum|per\s+year|annually)"),
    re.compile(r"(?i)interest[^.%]{0,80}?" + _RATE_NUM + _PCT),
]
_LOAN_TYPE_RE = re.compile(
    r"(?i)\b(conventional|FHA|VA|USDA|adjustable[- ]rate|fixed[- ]rate|balloon)\b"
)
_CASE_NUMBER_RE = re.compile(r"(?i)case\s*(?:no\.?|number)\s*[:\-]?\s*([A-Z0-9\-]+)")
_PARCEL_NUMBER_RE = re.compile(r"(?i)parcel\s*(?:no\.?|number)\s*[:\-]?\s*([\d\-]+)")
_FILING_DATE_RE = re.compile(r"(?i)filed\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})")

_CAPTION_SPLIT_RE = re.compile(r"(?i)\s+vs?\.?\s+")


@dataclass
class ParsedComplaintData:
    amount_owed: Optional[float] = None
    interest_rate: Optional[float] = None
    loan_type: str = ""
    case_number: str = ""
    parcel_number: str = ""
    filing_date: str = ""
    plaintiff: str = ""
    defendant: str = ""


def _first_match(pattern: "re.Pattern", text: str) -> Optional[str]:
    m = pattern.search(text)
    return m.group(1) if m else None


def parse_complaint_text(text: str) -> ParsedComplaintData:
    if not text:
        return ParsedComplaintData()

    amount_str = _first_match(_AMOUNT_RE, text)
    amount_owed = None
    if amount_str:
        try:
            amount_owed = float(amount_str.replace(",", ""))
        except ValueError:
            amount_owed = None

    interest_rate = None
    for pattern in _INTEREST_RATE_PATTERNS:
        rate_str = _first_match(pattern, text)
        if not rate_str:
            continue
        try:
            candidate = float(rate_str)
        except ValueError:
            continue
        if 0 < candidate < 30:  # sanity bound; skips stray figures
            interest_rate = candidate
            break

    loan_type_match = _LOAN_TYPE_RE.search(text)
    loan_type = loan_type_match.group(1) if loan_type_match else ""

    case_number = _first_match(_CASE_NUMBER_RE, text) or ""
    parcel_number = _first_match(_PARCEL_NUMBER_RE, text) or ""
    filing_date = _first_match(_FILING_DATE_RE, text) or ""

    plaintiff, defendant = "", ""
    caption_line = ""
    for line in text.splitlines():
        if _CAPTION_SPLIT_RE.search(line) and len(line.strip()) < 200:
            caption_line = line.strip()
            break
    if caption_line:
        parts = _CAPTION_SPLIT_RE.split(caption_line, maxsplit=1)
        if len(parts) == 2:
            plaintiff, defendant = parts[0].strip(), parts[1].strip()

    return ParsedComplaintData(
        amount_owed=amount_owed,
        interest_rate=interest_rate,
        loan_type=loan_type,
        case_number=case_number,
        parcel_number=parcel_number,
        filing_date=filing_date,
        plaintiff=plaintiff,
        defendant=defendant,
    )
