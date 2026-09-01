#!/usr/bin/env python3
"""
Franklin County, Ohio — Motivated Seller Lead Scraper
=======================================================

Pipeline
--------
1. Playwright drives the Franklin County Clerk of Courts "New Filing" portal
   to pull every currently-listed E-Foreclosure case filing. There's no
   date-window cutoff — cases already captured in a prior run (tracked
   by doc_num) are skipped before their documents are even downloaded,
   so the lead list accumulates over time without ever re-processing or
   duplicating a case.
2. requests + BeautifulSoup download the Auditor's bulk parcel data
   (GIS parcel shapefile bundle, which ships a .dbf attribute table) and
   dbfread parses it into an in-memory owner-name -> parcel lookup.
3. Each clerk filing is matched to a parcel record by owner name (three
   name-format variants are tried), which supplies property address and
   mailing address.
4. Each record is flagged for motivated-seller signals and scored 0-100.
5. Results are written to dashboard/records.json and data/records.json,
   and a GoHighLevel-ready CSV export is written alongside them.

Design notes
------------
* The clerk portal (clerknewfiling.franklincountyohio.gov) is a
  JavaScript single-page app — the raw HTML is just a loading shell, so
  static requests/BeautifulSoup cannot read it. All CSS/ARIA selectors
  used against it live in the CLERK_SELECTORS dict below so they can be
  fixed in one place after inspecting the live DOM in a browser
  (right-click -> Inspect on the search box, results grid, etc.). Every
  Playwright interaction is wrapped so a selector miss is logged and
  skipped rather than crashing the whole run.
* The Auditor's bulk parcel file location is discovered dynamically by
  scraping the CurrentExtracts directory listing for a *.zip whose name
  contains "parcel", instead of hardcoding a filename that changes over
  time. If you already know the exact URL, set PARCEL_SHAPEFILE_ZIP_URL
  to skip discovery.
* Nothing in the per-record pipeline is allowed to raise: any exception
  while processing a single filing is caught, logged, and that record is
  either passed through with the fields it does have or dropped — the
  run always completes and writes whatever it collected.
"""

from __future__ import annotations

import asyncio
import csv
import functools
import io
import json
import logging
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover
    async_playwright = None
    PlaywrightTimeoutError = Exception

try:
    import doc_parser  # local module: scraper/doc_parser.py
except ImportError:  # pragma: no cover - allows running fetch.py from repo root too
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import doc_parser


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CLERK_PORTAL_URL = "https://clerknewfiling.franklincountyohio.gov/"

# Auditor bulk parcel data. NOTE: this is a plain CSV, not a DBF/shapefile
# — the Auditor's GIS_Shapefiles bundle only has boundary geometry with
# NO owner/tax data attached (confirmed against their own layer
# metadata). The actual bulk ownership/address extract is published
# monthly as Parcel_CSV/{year}/{month}/Parcel.csv. We discover the most
# recent one at runtime; set PARCEL_CSV_URL to bypass discovery.
PARCEL_CSV_URL: Optional[str] = None
PARCEL_CSV_INDEX_URL = "https://apps.franklincountyauditor.com/Parcel_CSV/"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATHS = [
    REPO_ROOT / "dashboard" / "records.json",
    REPO_ROOT / "data" / "records.json",
]
GHL_CSV_PATH = REPO_ROOT / "dashboard" / "ghl_export.csv"
CACHE_DIR = REPO_ROOT / "scraper" / ".cache"

REQUEST_TIMEOUT = 60
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FranklinLeadBot/1.0"
)

LEAD_TYPES = {
    "efc": "E-Foreclosure",
}

FLAG_DEFINITIONS = [
    "Lis pendens",
    "Pre-foreclosure",
    "Judgment lien",
    "Tax lien",
    "Mechanic lien",
    "Probate / estate",
    "LLC / corp owner",
    "New this week",
]

# Keyword -> flag mapping. Matched (case-insensitively) against doc_type
# and the filing description text pulled from the clerk portal.
FLAG_KEYWORDS = {
    "Lis pendens": ("lis pendens",),
    "Pre-foreclosure": ("notice of default", "pre-foreclosure", "preforeclosure"),
    "Judgment lien": ("judgment lien", "judgement lien", "certificate of judgment", "cert of judgment"),
    "Tax lien": ("tax lien", "delinquent tax", "tax certificate"),
    "Mechanic lien": ("mechanic's lien", "mechanics lien", "mechanic lien"),
    "Probate / estate": ("probate", "estate of", "decedent", "administrator of the estate"),
}

ENTITY_SUFFIXES = (
    " llc", " l.l.c", " inc", " inc.", " incorporated", " corp", " corp.",
    " corporation", " ltd", " ltd.", " lp", " l.p.", " co.", " company",
    " trust", " trustee", " bank", " na", " n.a.",
)

# --- Playwright selectors -------------------------------------------------
# NOTE: verify/adjust these against the live DOM. clerknewfiling.franklin-
# countyohio.gov renders client-side, so open it in a real browser, use
# devtools "Inspect Element" on the search form and results grid, and
# update the selectors below. They are centralized here on purpose.
CLERK_SELECTORS = {
    # The portal (https://clerknewfiling.franklincountyohio.gov/) has no
    # search/filter UI — it's a plain listing of recent civil-case
    # submissions. Each row is just a submission ID linking to that
    # case's documents. There is nothing to type a case type, keyword,
    # or date range into, so we don't try. Every submission gets its
    # documents downloaded and read, and filtering to E-Foreclosures
    # within the lookback window happens afterward, based on what the
    # documents actually say (see filter_to_confirmed_foreclosures below).
    "results_table_rows": "table tbody tr, div[role='row']",
    "next_page_button": "button[aria-label*='Next' i], a[aria-label*='Next' i]",
    "loading_indicator": "text=Loading",
}

# How many cases to download+OCR concurrently. Each one opens its own
# browser tab and can involve several seconds of OCR, so keep this modest
# to avoid hammering the clerk portal or the CI runner's CPU.
DOCUMENT_ENRICHMENT_CONCURRENCY = 3
DOCUMENT_DOWNLOAD_TIMEOUT_MS = 30_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("franklin_scraper")


# --------------------------------------------------------------------------
# Retry helpers
# --------------------------------------------------------------------------

def retry_sync(times: int = 3, delay: float = 2.0, exceptions: tuple = (Exception,)):
    """Retry a synchronous function up to `times` attempts with backoff."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    log.warning(
                        "%s attempt %d/%d failed: %s", fn.__name__, attempt, times, exc
                    )
                    if attempt < times:
                        time.sleep(delay * attempt)
            log.error("%s failed after %d attempts", fn.__name__, times)
            raise last_exc

        return wrapper

    return decorator


def retry_async(times: int = 3, delay: float = 2.0, exceptions: tuple = (Exception,)):
    """Retry an async function up to `times` attempts with backoff."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    log.warning(
                        "%s attempt %d/%d failed: %s", fn.__name__, attempt, times, exc
                    )
                    if attempt < times:
                        await asyncio.sleep(delay * attempt)
            log.error("%s failed after %d attempts", fn.__name__, times)
            raise last_exc

        return wrapper

    return decorator


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class RawFiling:
    """A single filing as scraped from the clerk portal, before enrichment."""

    doc_num: str = ""
    doc_type: str = ""
    filed: str = ""            # ISO date string, e.g. "2026-08-10"
    grantor: str = ""          # property owner / defendant
    grantee: str = ""          # plaintiff / bank / servicer
    legal: str = ""            # legal description text
    amount: Optional[float] = None
    case_number: str = ""
    loan_type: str = ""
    interest_rate: Optional[float] = None
    clerk_url: str = ""
    description: str = ""      # free text used for flag keyword matching
    parcel_number: str = ""    # e.g. "010-068600" — exact key into Auditor Parcel_CSV
    category: str = ""         # grid's "Case Category" column, e.g. "E-Foreclosures"


@dataclass
class LeadRecord:
    doc_num: str
    doc_type: str
    filed: str
    cat: str
    cat_label: str
    owner: str
    grantee: str
    amount: Optional[float]
    legal: str
    prop_address: str = ""
    prop_city: str = ""
    prop_state: str = "OH"
    prop_zip: str = ""
    mail_address: str = ""
    mail_city: str = ""
    mail_state: str = ""
    mail_zip: str = ""
    clerk_url: str = ""
    flags: list = field(default_factory=list)
    score: int = 30
    interest_rate: Optional[float] = None
    case_number: str = ""
    loan_type: str = ""


# --------------------------------------------------------------------------
# Name normalization / matching
# --------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip()).upper()


def name_variants(raw_name: str) -> set[str]:
    """Generate FIRST LAST / LAST FIRST / LAST, FIRST variants for matching."""
    name = clean_name(raw_name)
    variants: set[str] = set()
    if not name:
        return variants

    variants.add(name)

    if "," in name:
        last, _, first = name.partition(",")
        last, first = last.strip(), first.strip()
        if last and first:
            variants.add(f"{last}, {first}")
            variants.add(f"{last} {first}")
            variants.add(f"{first} {last}")
    else:
        parts = name.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            variants.add(f"{first} {last}")
            variants.add(f"{last} {first}")
            variants.add(f"{last}, {first}")

    return variants


def is_entity_owner(raw_name: str) -> bool:
    lowered = f" {(raw_name or '').strip().lower()} "
    return any(suffix + " " in lowered or lowered.endswith(suffix) for suffix in ENTITY_SUFFIXES)


# --------------------------------------------------------------------------
# Auditor bulk parcel data (Parcel_CSV monthly extract)
# --------------------------------------------------------------------------
# IMPORTANT DEVIATION FROM THE ORIGINAL SPEC: the Auditor's GIS shapefile
# bundle (GIS_Shapefiles/CurrentExtracts) is geometry-only — its .dbf has
# no owner or address data at all (confirmed against Esri's own layer
# description for this dataset: "NO associated CAMA... or parcel
# information"). The real bulk ownership/address extract the Auditor
# publishes is a plain CSV, updated monthly, at
# Parcel_CSV/{year}/{month}/Parcel.csv. So this reads CSV via the
# standard library `csv` module instead of dbfread/DBF.

def get_field(record: dict, candidates: tuple) -> str:
    """Return the first non-empty value among candidate column names,
    matched case-insensitively (works for both DBF-style dict records and
    csv.DictReader rows)."""
    for key in candidates:
        for actual_key in record.keys():
            if actual_key.upper() == key.upper():
                val = record.get(actual_key)
                if val not in (None, ""):
                    return str(val).strip()
    return ""


# Confirmed against a real Franklin County Parcel_CSV row (checked
# 2026-08): owner is NAME1; property/site address is STADDR / USPS_CITY
# / STATE / ZIPCODE as clean separate columns; the mailing address is
# MAILAD3 (street) + MAILAD4, where MAILAD4 is "CITY STATE ZIP" squashed
# into a single combined string (parsed apart in split_city_state_zip).
# OWNER_ADD1/OWNER_ADD2 is an older-style duplicate 2-line mailing
# address, kept only as a fallback. The generic candidates after the
# confirmed name are kept in case the Auditor changes the header again —
# build_owner_lookup logs the real header if none of them match.
OWNER_COLUMNS = ("NAME1", "OWNER", "OWN1", "OWNERNAME", "OWNER_NAME", "OWNER1")
SITE_ADDR_COLUMNS = ("STADDR", "SITE_ADDR", "SITEADDR", "SITE_ADDRE", "PROPADDR", "SITEADDRESS")
SITE_CITY_COLUMNS = ("USPS_CITY", "SITE_CITY", "SITECITY", "PROPCITY", "SITECITYNAME")
SITE_ZIP_COLUMNS = ("ZIPCODE", "SITE_ZIP", "SITEZIP", "PROPZIP", "SITEZIPCODE")
MAIL_STREET_COLUMNS = ("MAILAD3", "OWNER_ADD1", "ADDR_1", "MAILADR1", "MAIL_ADDR1", "MAILADDR")
MAIL_CITY_STATE_ZIP_COLUMNS = ("MAILAD4", "OWNER_ADD2")
PARCEL_ID_COLUMNS = ("PARCEL ID", "PARCELID", "PARCEL_ID", "PARCELNO", "PARCEL NO")


def normalize_parcel_id(raw: str) -> str:
    """Parcel IDs show up in different formats depending on the filing
    law firm — "Parcel Number: 010-068600" (hyphenated) vs "PPN:
    01010571100" (no hyphens) both refer to the same parcel. Strip down
    to digits only so formatting differences (hyphens, spaces, a
    trailing "-00") don't prevent an otherwise-exact match."""
    return re.sub(r"[^\d]", "", raw or "")


def split_city_state_zip(text: str) -> tuple[str, str, str]:
    """Parcel_CSV packs mailing city/state/zip into one field, e.g.
    "COLUMBUS OH 43215-1486". Split it back into three parts."""
    text = (text or "").strip()
    if not text:
        return "", "", ""
    match = re.match(r"^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    return text, "", ""


@retry_sync(times=3, delay=3.0, exceptions=(requests.RequestException,))
def _list_directory_entries(session: requests.Session, index_url: str) -> list[str]:
    """Return the link text/hrefs from a plain Apache/IIS-style directory
    listing page (these Auditor folders have no JS, just <a href> links)."""
    resp = session.get(index_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    entries = []
    for a in soup.find_all("a", href=True):
        name = a["href"].strip("/").split("/")[-1]
        if name and name not in ("..", "."):
            entries.append(name)
    return entries


@retry_sync(times=3, delay=3.0, exceptions=(requests.RequestException,))
def find_parcel_csv_url(session: requests.Session) -> str:
    """Walk Parcel_CSV/{year}/{month}/ (newest first) looking for a
    Parcel.csv, since the Auditor doesn't expose a single "latest"
    pointer. Checks at most the last 2 years x last 6 months touched
    to bound how many directory requests this can make."""
    if PARCEL_CSV_URL:
        return PARCEL_CSV_URL

    years = sorted(
        (e for e in _list_directory_entries(session, PARCEL_CSV_INDEX_URL) if re.fullmatch(r"\d{4}", e)),
        reverse=True,
    )
    if not years:
        raise RuntimeError(f"No year folders found under {PARCEL_CSV_INDEX_URL}")

    checked = 0
    for year in years[:2]:
        year_url = f"{PARCEL_CSV_INDEX_URL}{year}/"
        try:
            months = sorted(
                (e for e in _list_directory_entries(session, year_url) if re.fullmatch(r"\d{1,2}", e)),
                key=lambda m: int(m),
                reverse=True,
            )
        except requests.RequestException as exc:
            log.debug("Could not list %s: %s", year_url, exc)
            continue

        for month in months[:6]:
            month_url = f"{year_url}{month}/"
            checked += 1
            try:
                files = _list_directory_entries(session, month_url)
            except requests.RequestException as exc:
                log.debug("Could not list %s: %s", month_url, exc)
                continue
            for name in files:
                if name.lower() == "parcel.csv":
                    return f"{month_url}{name}"

    raise RuntimeError(
        f"Could not find a Parcel.csv under {PARCEL_CSV_INDEX_URL} after checking "
        f"{checked} year/month folders. Set PARCEL_CSV_URL manually if you know "
        "the current path (browse the URL above in a browser to check)."
    )


@retry_sync(times=3, delay=5.0, exceptions=(requests.RequestException,))
def download_parcel_csv(session: requests.Session, url: str) -> Path:
    """Stream the parcel CSV straight to disk — this is a whole-county
    extract and can be very large, so it's never held fully in memory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / "Parcel.csv"
    log.info("Downloading Auditor parcel CSV from %s", url)
    with session.get(url, timeout=REQUEST_TIMEOUT * 6, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    log.info("Downloaded parcel CSV to %s (%.1f MB)", dest, dest.stat().st_size / 1_000_000)
    return dest


@dataclass
class ParcelLookup:
    """Two ways to find a parcel: by exact parcel ID (from a document's
    "Parcel Number: 010-068600" line — precise, when we have it) or by
    owner-name variant (fuzzy, always available as a fallback)."""
    by_parcel_id: dict[str, dict]
    by_name: dict[str, dict]

    def __bool__(self) -> bool:
        return bool(self.by_parcel_id or self.by_name)


def build_owner_lookup(csv_path: Path) -> ParcelLookup:
    """Build both lookups in a single streaming pass over the CSV — by
    parcel ID (exact, preferred) and by owner-name variant (fallback for
    filings whose documents didn't yield a parcel number). If none of
    the expected owner-name columns are found, the real header is
    logged so OWNER_COLUMNS can be corrected against actual data."""
    log.info("Parsing parcel CSV: %s", csv_path)
    by_name: dict[str, dict] = {}
    by_parcel_id: dict[str, dict] = {}
    count = 0

    # utf-8-sig strips a leading byte-order-mark if present (Franklin
    # County's export has one) instead of it leaking into the first
    # column's name as garbled characters.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError("Parcel CSV appears to be empty or has no header row.")

        header_upper = {h.upper().strip() for h in reader.fieldnames}
        if not any(c.upper() in header_upper for c in OWNER_COLUMNS):
            log.warning(
                "None of the expected owner-name columns %s were found. "
                "Actual CSV header: %s — update OWNER_COLUMNS in fetch.py to match.",
                OWNER_COLUMNS, reader.fieldnames,
            )

        for row in reader:
            try:
                owner_raw = get_field(row, OWNER_COLUMNS)
                if not owner_raw:
                    continue

                mail_street = get_field(row, MAIL_STREET_COLUMNS)
                mail_city_state_zip = get_field(row, MAIL_CITY_STATE_ZIP_COLUMNS)
                mail_city, mail_state, mail_zip = split_city_state_zip(mail_city_state_zip)

                parcel = {
                    "owner_raw": owner_raw,
                    "prop_address": get_field(row, SITE_ADDR_COLUMNS),
                    "prop_city": get_field(row, SITE_CITY_COLUMNS),
                    "prop_zip": get_field(row, SITE_ZIP_COLUMNS),
                    "mail_address": mail_street,
                    "mail_city": mail_city,
                    "mail_state": mail_state or "OH",
                    "mail_zip": mail_zip,
                }

                for variant in name_variants(owner_raw):
                    by_name.setdefault(variant, parcel)

                parcel_id_raw = get_field(row, PARCEL_ID_COLUMNS)
                if parcel_id_raw:
                    by_parcel_id[normalize_parcel_id(parcel_id_raw)] = parcel

                count += 1
            except Exception as exc:  # noqa: BLE001 - never let one bad row kill the load
                log.debug("Skipping malformed parcel row: %s", exc)
                continue

    log.info(
        "Built parcel lookup from %d records (%d name-variant keys, %d parcel IDs)",
        count, len(by_name), len(by_parcel_id),
    )
    return ParcelLookup(by_parcel_id=by_parcel_id, by_name=by_name)


def load_parcel_lookup() -> ParcelLookup:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        url = find_parcel_csv_url(session)
        csv_path = download_parcel_csv(session, url)
        return build_owner_lookup(csv_path)
    except Exception as exc:  # noqa: BLE001
        log.error("Parcel data unavailable, continuing without address enrichment: %s", exc)
        return ParcelLookup(by_parcel_id={}, by_name={})


def match_parcel(owner_name: str, parcel_number: str, lookup: ParcelLookup) -> Optional[dict]:
    """Prefer an exact parcel-ID match (from a document's "Parcel
    Number:" or "PPN:" line) over fuzzy name matching, since the ID is
    unambiguous and the name isn't."""
    if not lookup:
        return None

    if parcel_number:
        normalized = normalize_parcel_id(parcel_number)
        hit = lookup.by_parcel_id.get(normalized)
        if hit:
            return hit

        # Some filings' "PPN:" includes a trailing 2-digit card/building
        # suffix (commonly "00" for the primary structure) that the
        # Auditor's own parcel ID doesn't carry — if the exact string
        # didn't match, try again without a trailing "00".
        if len(normalized) > 2 and normalized.endswith("00"):
            hit = lookup.by_parcel_id.get(normalized[:-2])
            if hit:
                return hit

    if owner_name:
        for variant in name_variants(owner_name):
            if variant in lookup.by_name:
                return lookup.by_name[variant]
    return None


# --------------------------------------------------------------------------
# Clerk portal scraping (Playwright, async)
# --------------------------------------------------------------------------

async def safe_fill(page, selector: str, value: str) -> bool:
    try:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=8000)
        await locator.fill(value)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fill selector %r: %s", selector, exc)
        return False


async def safe_click(page, selector: str) -> bool:
    try:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=8000)
        await locator.click()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not click selector %r: %s", selector, exc)
        return False


def parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.]", "", text)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_rate(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(\d+(\.\d+)?)\s*%", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_date(text: str) -> str:
    """Best-effort parse of common clerk-portal date formats to ISO 8601."""
    text = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text  # leave as-is if unparseable; downstream filters will drop it


@retry_async(times=3, delay=4.0, exceptions=(Exception,))
async def scrape_efc_filings(known_doc_nums: Optional[set[str]] = None) -> list[RawFiling]:
    """Drive the clerk portal to pull E-Foreclosure filings. Selectors
    are centralized in CLERK_SELECTORS — verify them against the live
    DOM if the site markup changes.

    `known_doc_nums` (already-captured cases from a prior run) are
    filtered out immediately after the grid-level category check and
    *before* any document is downloaded — there's no reason to spend
    time re-downloading and re-OCRing a case's documents when we
    already have its data."""
    if async_playwright is None:
        raise RuntimeError("playwright is not installed; run `python -m playwright install`.")
    known_doc_nums = known_doc_nums or set()

    filings: list[RawFiling] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            # These matter specifically in containerized CI environments
            # (like GitHub Actions runners) — without them, Chromium can
            # silently hang rather than error, which is much harder to
            # diagnose than an outright crash. Harmless locally too.
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(user_agent=USER_AGENT, accept_downloads=True)
        page = await context.new_page()

        try:
            log.info("Loading clerk portal: %s", CLERK_PORTAL_URL)
            await page.goto(CLERK_PORTAL_URL, wait_until="networkidle", timeout=45000)

            # The portal is a client-rendered SPA; give it a moment past
            # networkidle for framework hydration before we probe for
            # results. There's no search/filter form to fill in — this
            # page just lists recent submissions directly.
            await page.wait_for_timeout(1500)

            page_num = 1
            max_pages = 25  # hard safety cap so a pagination-loop bug can't run forever
            while page_num <= max_pages:
                rows = page.locator(CLERK_SELECTORS["results_table_rows"])
                row_count = await rows.count()
                log.info("Page %d: found %d result rows", page_num, row_count)
                if row_count == 0:
                    break

                for i in range(row_count):
                    try:
                        row = rows.nth(i)
                        row_text = (await row.inner_text()).strip()
                        if not row_text:
                            continue

                        filing = await parse_result_row(row, row_text)
                        if filing is not None:
                            filings.append(filing)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Skipping unreadable result row %d: %s", i, exc)
                        continue

                has_next = await page.locator(CLERK_SELECTORS["next_page_button"]).count() > 0
                if not has_next:
                    break
                advanced = await safe_click(page, CLERK_SELECTORS["next_page_button"])
                if not advanced:
                    break
                await page.wait_for_timeout(1200)
                page_num += 1

            log.info("Grid returned %d total submissions across all case categories", len(filings))

            # Diagnostic: if a row's own text clearly says "foreclos" but our
            # category regex didn't extract it, the regex doesn't match the
            # real DOM formatting — log the raw text so it can be fixed
            # against ground truth instead of another guess.
            missed = [f for f in filings if "foreclos" in f.description.lower() and "foreclos" not in f.category.lower()]
            if missed:
                log.warning(
                    "%d row(s) mention 'foreclos' but category regex extracted %r — "
                    "raw row text for the first one: %r",
                    len(missed), missed[0].category, missed[0].description[:400],
                )
            elif filings:
                log.warning(
                    "No row mentions 'foreclos' at all in its text — raw row text sample: %r",
                    filings[0].description[:400],
                )

            total_before_category_filter = len(filings)
            filings = [f for f in filings if "foreclos" in f.category.lower()]
            log.info(
                "%d/%d submissions are in an E-Foreclosures category (grid-level filter, before "
                "any documents are downloaded)",
                len(filings), total_before_category_filter,
            )

            already_captured = [f for f in filings if f.doc_num and f.doc_num in known_doc_nums]
            filings = [f for f in filings if not (f.doc_num and f.doc_num in known_doc_nums)]
            if already_captured:
                log.info(
                    "Skipping document download for %d E-Foreclosure filing(s) already "
                    "captured in a prior run (doc_nums: %s)",
                    len(already_captured),
                    ", ".join(f.doc_num for f in already_captured[:10])
                    + (", ..." if len(already_captured) > 10 else ""),
                )

            if filings:
                log.info(
                    "Downloading & reading case documents for %d E-Foreclosure filings "
                    "(concurrency=%d)...",
                    len(filings), DOCUMENT_ENRICHMENT_CONCURRENCY,
                )
                semaphore = asyncio.Semaphore(DOCUMENT_ENRICHMENT_CONCURRENCY)
                filings = await asyncio.gather(
                    *[enrich_filing_with_documents_bounded(context, f, semaphore) for f in filings]
                )
                filings = filter_to_confirmed_foreclosures(filings)
                with_parcel_num = sum(1 for f in filings if f.parcel_number)
                log.info(
                    "%d/%d foreclosure filings had a parcel number extracted from their documents",
                    with_parcel_num, len(filings),
                )
                if with_parcel_num < len(filings):
                    sample = next((f for f in filings if not f.parcel_number), None)
                    if sample:
                        log.info(
                            "Sample filing WITHOUT a parcel number (doc_num=%s) — description snippet: %s",
                            sample.doc_num, (sample.description or "")[:500].replace("\n", " "),
                        )

        finally:
            await context.close()
            await browser.close()

    log.info("Clerk portal scrape complete: %d raw filings collected", len(filings))
    return filings


DOCUMENT_DOWNLOAD_RETRIES = 3
DOCUMENT_DOWNLOAD_RETRY_DELAY = 5.0  # seconds between attempts


async def _download_case_documents_zip_once(context, case_url: str, timeout_ms: int) -> bytes:
    """Single attempt at triggering a case's document bundle download.
    Raises on any failure — retries live in the wrapper below.

    On this portal, navigating directly to a case's document URL
    (e.g. .../api/submissions/{id}/documents) immediately starts a file
    download rather than rendering a page — Playwright surfaces that as
    a "Download is starting" error from page.goto() itself, which is
    expected here, not a real failure. We start listening for the
    download event *before* navigating so it's still captured even
    though the navigation "fails" in that specific way.

    Some submissions' document endpoints return a server error (HTTP
    500, confirmed against a live case) instead of a file. That doesn't
    raise on its own — page.goto() returns normally with an error-status
    Response — so without an explicit check, this would otherwise sit
    waiting for a download event that was never going to arrive until
    the full timeout expired. Checking the response status lets a
    genuinely broken case fail immediately instead of waiting it out.

    Every major step is logged at DEBUG-visible-as-INFO level right now
    on purpose — a prior run showed every single case hanging silently
    all the way to the hard outer ceiling with zero per-attempt warnings
    logged, meaning the hang is happening somewhere Playwright's own
    timeouts aren't catching. This instrumentation exists to find out
    exactly which line that is; strip it back down once that's known.
    """
    case_id = case_url.rstrip("/").rsplit("/", 2)[-2] if "/" in case_url else case_url
    log.info("[%s] opening new page...", case_id)
    page = await context.new_page()
    log.info("[%s] new page opened, starting goto+expect_download...", case_id)
    try:
        async with page.expect_download(timeout=timeout_ms) as download_info:
            response = None
            try:
                log.info("[%s] calling page.goto...", case_id)
                response = await page.goto(case_url, timeout=min(timeout_ms, 30000))
                log.info("[%s] page.goto returned normally, status=%s", case_id, getattr(response, "status", None))
            except Exception as nav_exc:  # noqa: BLE001
                log.info("[%s] page.goto raised: %s", case_id, nav_exc)
                if "download is starting" not in str(nav_exc).lower():
                    raise
                # else: expected — the download event below still fires.

            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"Server returned HTTP {response.status} instead of a document download"
                )

            log.info("[%s] waiting on download_info.value...", case_id)
        download = await download_info.value
        log.info("[%s] download event resolved, reading file path...", case_id)
        tmp_path = await download.path()
        if tmp_path is None:
            raise RuntimeError("Download did not complete (no file path returned)")
        log.info("[%s] download complete: %s", case_id, tmp_path)
        return Path(tmp_path).read_bytes()
    finally:
        await page.close()


async def download_case_documents_zip(context, case_url: str) -> Optional[bytes]:
    """Trigger a case's document bundle download and return the raw ZIP
    bytes (or None if every attempt fails — this must never raise, since
    one bad case shouldn't stop the whole run). Retries a few times with
    a short delay first, since a single 30-second timeout on a slow or
    momentarily busy server was previously enough to drop a filing
    entirely on its only attempt. Later attempts get progressively more
    time, in case the case is just slow to prepare rather than broken."""
    if not case_url or case_url == CLERK_PORTAL_URL:
        return None

    last_exc: Optional[Exception] = None
    for attempt in range(1, DOCUMENT_DOWNLOAD_RETRIES + 1):
        timeout_ms = DOCUMENT_DOWNLOAD_TIMEOUT_MS + (attempt - 1) * 15_000
        try:
            return await _download_case_documents_zip_once(context, case_url, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning(
                "Document download attempt %d/%d failed for %s (timeout=%ds): %s",
                attempt, DOCUMENT_DOWNLOAD_RETRIES, case_url, timeout_ms // 1000, exc,
            )
            if attempt < DOCUMENT_DOWNLOAD_RETRIES:
                await asyncio.sleep(DOCUMENT_DOWNLOAD_RETRY_DELAY)

    log.error(
        "Giving up on case documents for %s after %d attempts: %s",
        case_url, DOCUMENT_DOWNLOAD_RETRIES, last_exc,
    )
    return None


PER_CASE_HARD_TIMEOUT_SECONDS = 240  # absolute ceiling per case, regardless of cause


async def enrich_filing_with_documents_bounded(
    context, filing: RawFiling, semaphore: asyncio.Semaphore
) -> RawFiling:
    """Wraps enrich_filing_with_documents with a hard, unconditional
    timeout. The download step already has its own retry/timeout logic,
    but that only helps if Playwright's own timeout mechanism actually
    fires — in some environments (confirmed: GitHub Actions' containerized
    runners) something in the underlying browser automation can hang
    indefinitely instead of erroring out cleanly. Since concurrency is
    limited by a semaphore, even one such stuck case can permanently
    occupy a concurrency slot and eventually stall the entire batch —
    this is a last-resort safety net that guarantees forward progress no
    matter what's wrong underneath."""
    try:
        return await asyncio.wait_for(
            enrich_filing_with_documents(context, filing, semaphore),
            timeout=PER_CASE_HARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.error(
            "Case %s exceeded the hard %ds per-case ceiling (likely stuck at a lower level "
            "than our own retry logic) — skipping it and moving on",
            filing.doc_num, PER_CASE_HARD_TIMEOUT_SECONDS,
        )
        return filing


async def enrich_filing_with_documents(
    context, filing: RawFiling, semaphore: asyncio.Semaphore
) -> RawFiling:
    """Download a case's PDF bundle, extract/OCR the text, and fill in
    whatever fields the results grid didn't have (interest rate, amount
    owed, loan type, full legal description, and often grantor/grantee).
    Grid-sourced values are kept as a fallback wherever the documents
    don't yield a cleaner match."""
    async with semaphore:
        try:
            zip_bytes = await download_case_documents_zip(context, filing.clerk_url)
            if not zip_bytes:
                return filing

            pdfs = doc_parser.extract_pdfs_from_zip(zip_bytes)
            if not pdfs:
                log.warning("No PDFs found in case bundle for %s", filing.doc_num)
                return filing

            combined_text_parts = []
            for name, pdf_bytes in pdfs:
                # pdfplumber/pytesseract are CPU-bound and blocking —
                # run them off the event loop.
                text = await asyncio.to_thread(doc_parser.extract_text_hybrid, pdf_bytes, name)
                if text:
                    combined_text_parts.append(text)
            combined_text = "\n\n".join(combined_text_parts)

            if not combined_text.strip():
                log.warning("Extracted no readable text from documents for %s", filing.doc_num)
                return filing

            parsed = doc_parser.parse_fields_from_text(combined_text)

            filing.doc_type = parsed.get("doc_type") or filing.doc_type
            filing.filed = parsed.get("filed") or filing.filed
            filing.grantor = parsed.get("grantor") or filing.grantor
            filing.grantee = parsed.get("grantee") or filing.grantee
            filing.case_number = parsed.get("case_number") or filing.case_number
            filing.loan_type = parsed.get("loan_type") or filing.loan_type
            filing.legal = parsed.get("legal_description") or filing.legal
            filing.parcel_number = parsed.get("parcel_number") or filing.parcel_number

            if parsed.get("interest_rate"):
                try:
                    filing.interest_rate = float(parsed["interest_rate"])
                except ValueError:
                    pass

            if parsed.get("amount"):
                try:
                    filing.amount = float(parsed["amount"].replace(",", ""))
                except ValueError:
                    pass

            # Keep the raw text (truncated) around for flag-keyword
            # matching, since signals like "Lis Pendens" or "Notice of
            # Default" often only appear in the document body, not the
            # grid row.
            filing.description = (filing.description + "\n" + combined_text[:6000]).strip()

        except Exception as exc:  # noqa: BLE001
            log.error("Document enrichment failed for %s: %s", filing.doc_num, exc)

        return filing


FORECLOSURE_KEYWORDS = (
    "foreclosure", "lis pendens", "notice of default", "decree of foreclosure",
    "judgment entry in foreclosure", "order of sale", "mortgage foreclosure",
)


def is_foreclosure_filing(filing: RawFiling) -> bool:
    if filing.doc_type:
        # doc_type comes from the actual extracted document title (e.g.
        # "COMPLAINT FOR FORECLOSURE") when document parsing succeeded —
        # trust that structural signal over a loose full-text scan,
        # since a large, unrelated filing (a workers'-comp appeal, a
        # commercial dispute, etc.) can otherwise trip a keyword match
        # just by mentioning "foreclosure" once somewhere in its body.
        return any(keyword in filing.doc_type.lower() for keyword in FORECLOSURE_KEYWORDS)
    # No doc_type was extracted at all (documents unreadable or the
    # download failed) — fall back to whatever text we do have, which
    # is at least better than assuming every unread filing is a match.
    return any(keyword in (filing.description or "").lower() for keyword in FORECLOSURE_KEYWORDS)


def filter_to_confirmed_foreclosures(filings: list[RawFiling]) -> list[RawFiling]:
    """The clerk portal has no search/filter UI — every submission has to
    be read before we know whether it's actually an E-Foreclosure, using
    whatever doc_type/description the documents (or, failing that, the
    grid row text) actually gave us. No date-window filtering here —
    every E-Foreclosure the grid currently lists gets kept; de-duping
    against already-captured doc_nums (done earlier, before documents
    are even downloaded) is what prevents re-processing the same case
    across runs, not a lookback cutoff."""
    before = len(filings)
    foreclosures = [f for f in filings if is_foreclosure_filing(f)]
    log.info(
        "%d/%d submissions matched E-Foreclosure keywords after reading documents",
        len(foreclosures), before,
    )
    return foreclosures


async def parse_result_row(row, row_text: str) -> Optional[RawFiling]:
    """Turn one results-grid row into a RawFiling.

    Confirmed real format (2026-08, verified against live output): rows
    are tab-delimited into 5 fields:
        doc_num \\t "PARTY1 -VS- PARTY2 ET AL" \\t date \\t category \\t status
    e.g. "28216451\\tLAKEVIEW LOAN SERVICING LLC -VS- MARY E HOLMES ET AL
    \\t8/18/26\\tE-Foreclosures\\tAwaiting Approval". If that split doesn't
    yield enough fields (DOM formatting changed), falls back to a looser
    whitespace split so the row still gets *something* rather than being
    silently dropped.
    """
    href = ""
    try:
        link = row.locator("a[href]").first
        if await link.count() > 0:
            href = await link.get_attribute("href") or ""
            if href and not href.startswith("http"):
                href = urljoin(CLERK_PORTAL_URL, href)
    except Exception:  # noqa: BLE001
        pass

    doc_num_match = re.search(r"\b(\d{2}[A-Z]{2}\d{5,8}|\d{8,12})\b", row_text)
    date_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", row_text)
    amount_match = re.search(r"\$[\d,]+(?:\.\d{2})?", row_text)

    if not doc_num_match and not date_match:
        # Doesn't look like a data row (could be a header row) — skip.
        return None

    fields = [f.strip() for f in row_text.split("\t") if f.strip()]
    if len(fields) < 4:
        # Fallback for a DOM format that isn't tab-delimited after all.
        fields = [f.strip() for f in re.split(r"\t|\s{2,}", row_text) if f.strip()]

    category = ""
    for field in fields:
        if re.match(r"^[A-Z]-[A-Za-z]", field):
            category = field
            break

    grantee, grantor = "", ""
    parties_field = next(
        (f for f in fields if re.search(r"-\s*VS\s*-|\bvs\.?\b", f, re.IGNORECASE)), ""
    )
    if parties_field:
        parts = re.split(r"-\s*VS\s*-|\bvs\.?\b", parties_field, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            grantee = parts[0].strip(" -")
            grantor = re.sub(
                r"\b(ET AL\.?|ET UX\.?|ET VIR\.?)\s*$", "", parts[1].strip(" -"), flags=re.IGNORECASE
            ).strip()

    return RawFiling(
        doc_num=doc_num_match.group(1) if doc_num_match else (fields[0] if fields else ""),
        doc_type="",  # deliberately blank — real classification uses `category`
                      # (the grid's own Case Category column) below, confirmed
                      # further by document content in filter_to_confirmed_foreclosures
        filed=parse_date(date_match.group(1)) if date_match else "",
        grantor=grantor,
        grantee=grantee,
        legal="",
        amount=parse_money(amount_match.group(0)) if amount_match else None,
        case_number=doc_num_match.group(1) if doc_num_match else "",
        loan_type="",
        interest_rate=parse_rate(row_text),
        clerk_url=href or CLERK_PORTAL_URL,
        description=row_text,
        category=category,
    )


# --------------------------------------------------------------------------
# Flagging + scoring
# --------------------------------------------------------------------------

def detect_flags(filing: RawFiling, is_new_this_week: bool) -> list[str]:
    haystack = f"{filing.doc_type} {filing.description}".lower()
    flags = [flag for flag, keywords in FLAG_KEYWORDS.items() if any(k in haystack for k in keywords)]

    if is_entity_owner(filing.grantor):
        flags.append("LLC / corp owner")

    if is_new_this_week:
        flags.append("New this week")

    # Preserve canonical ordering for readability in the UI.
    ordered = [f for f in FLAG_DEFINITIONS if f in flags]
    return ordered


def calculate_score(flags: list[str], amount: Optional[float], has_address: bool) -> int:
    score = 30
    score += 10 * len(flags)

    if "Lis pendens" in flags and "Pre-foreclosure" in flags:
        score += 20

    if amount is not None:
        if amount > 100_000:
            score += 15
        elif amount > 50_000:
            score += 10

    if "New this week" in flags:
        score += 5

    if has_address:
        score += 5

    return max(0, min(100, score))


# --------------------------------------------------------------------------
# Record assembly
# --------------------------------------------------------------------------

def load_existing_records() -> dict[str, dict]:
    """Load previously captured records (from a prior run) keyed by
    doc_num, so this run can skip re-downloading/re-reading documents
    for cases already captured, and so the final output can be merged
    (accumulated) rather than overwritten each time — a case that ages
    off the "unapproved" grid stays in the lead list instead of
    disappearing."""
    for path in OUTPUT_PATHS:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return {r.get("doc_num", ""): r for r in data.get("records", []) if r.get("doc_num")}
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read existing records at %s: %s", path, exc)
    return {}


def load_existing_doc_nums() -> set[str]:
    return set(load_existing_records().keys())


def build_records(filings: list[RawFiling], parcel_lookup: ParcelLookup) -> list[LeadRecord]:
    """Build LeadRecords for newly-processed filings. `filings` here is
    always the set of genuinely new cases — anything already captured in
    a prior run was filtered out earlier (in scrape_efc_filings, before
    documents were even downloaded), so every record built here is, by
    construction, new."""
    records: list[LeadRecord] = []

    for filing in filings:
        try:
            parcel = match_parcel(filing.grantor, filing.parcel_number, parcel_lookup)
            has_address = bool(parcel and (parcel.get("prop_address") or parcel.get("mail_address")))

            flags = detect_flags(filing, is_new_this_week=True)
            score = calculate_score(flags, filing.amount, has_address)

            record = LeadRecord(
                doc_num=filing.doc_num or "UNKNOWN",
                doc_type=filing.doc_type or "Foreclosure",
                filed=filing.filed,
                cat="efc",
                cat_label=LEAD_TYPES["efc"],
                owner=filing.grantor,
                grantee=filing.grantee,
                amount=filing.amount,
                legal=filing.legal,
                clerk_url=filing.clerk_url,
                flags=flags,
                score=score,
                interest_rate=filing.interest_rate,
                case_number=filing.case_number,
                loan_type=filing.loan_type,
            )

            if parcel:
                record.prop_address = parcel.get("prop_address", "")
                record.prop_city = parcel.get("prop_city", "")
                record.prop_zip = parcel.get("prop_zip", "")
                record.mail_address = parcel.get("mail_address", "") or parcel.get("prop_address", "")
                record.mail_city = parcel.get("mail_city", "") or parcel.get("prop_city", "")
                record.mail_state = parcel.get("mail_state", "OH")
                record.mail_zip = parcel.get("mail_zip", "") or parcel.get("prop_zip", "")

            records.append(record)
        except Exception as exc:  # noqa: BLE001 - one bad filing must never kill the run
            log.error("Skipping unprocessable filing %r: %s", getattr(filing, "doc_num", "?"), exc)
            continue

    records.sort(key=lambda r: r.score, reverse=True)
    return records


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_outputs(new_records: list[LeadRecord]) -> dict:
    """Merge this run's newly-captured records with whatever was already
    on disk (keyed by doc_num) instead of overwriting — a case that ages
    off the clerk portal's "unapproved" grid stays in the lead list, and
    a case already captured never gets reprocessed or duplicated."""
    existing_by_doc_num = load_existing_records()
    for record in new_records:
        existing_by_doc_num[record.doc_num] = asdict(record)

    merged = sorted(existing_by_doc_num.values(), key=lambda r: r.get("score", 0), reverse=True)

    now = datetime.now(timezone.utc)
    payload = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "source": "Franklin County Clerk of Courts + Auditor Parcel Data",
        "new_this_run": len(new_records),
        "total": len(merged),
        "with_address": sum(1 for r in merged if r.get("prop_address") or r.get("mail_address")),
        "records": merged,
    }

    for path in OUTPUT_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        log.info(
            "Wrote %d total records (%d new this run) to %s",
            len(merged), len(new_records), path,
        )

    return payload


# --------------------------------------------------------------------------
# GoHighLevel CSV export
# --------------------------------------------------------------------------

GHL_HEADERS = [
    "First Name", "Last Name", "Mailing Address", "Mailing City", "Mailing State",
    "Mailing Zip", "Property Address", "Property City", "Property State", "Property Zip",
    "Lead Type", "Document Type", "Date Filed", "Document Number", "Amount/Debt Owed",
    "Seller Score", "Motivated Seller Flags", "Source", "Public Records URL",
]


def split_owner_name(owner: str) -> tuple[str, str]:
    """Best-effort First/Last split for GHL import. Entity owners (LLC,
    Inc, etc.) are placed entirely in Last Name so contact records aren't
    mangled."""
    owner = (owner or "").strip()
    if not owner:
        return "", ""
    if is_entity_owner(owner):
        return "", owner

    if "," in owner:
        last, _, first = owner.partition(",")
        return first.strip().title(), last.strip().title()

    parts = owner.split()
    if len(parts) == 1:
        return "", parts[0].title()
    return " ".join(p.title() for p in parts[:-1]), parts[-1].title()


def export_ghl_csv(records: list[LeadRecord], path: Path = GHL_CSV_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(GHL_HEADERS)
        for r in records:
            try:
                first, last = split_owner_name(r.owner)
                writer.writerow([
                    first, last,
                    r.mail_address, r.mail_city, r.mail_state, r.mail_zip,
                    r.prop_address, r.prop_city, r.prop_state, r.prop_zip,
                    r.cat_label, r.doc_type, r.filed, r.doc_num,
                    f"{r.amount:.2f}" if r.amount is not None else "",
                    r.score, "; ".join(r.flags),
                    "Franklin County Clerk of Courts", r.clerk_url,
                ])
            except Exception as exc:  # noqa: BLE001
                log.error("Skipping GHL export row for %r: %s", r.doc_num, exc)
                continue
    log.info("Wrote GHL export CSV to %s", path)
    return path


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

async def run(skip_parcels: bool = False) -> dict:
    log.info("=== Franklin County motivated-seller scraper starting ===")

    parcel_lookup: ParcelLookup = ParcelLookup(by_parcel_id={}, by_name={})
    if not skip_parcels:
        parcel_lookup = load_parcel_lookup()
    else:
        log.info("Skipping Auditor parcel data (--skip-parcels)")

    known_doc_nums = load_existing_doc_nums()
    log.info("%d case(s) already captured from prior runs", len(known_doc_nums))

    try:
        filings = await scrape_efc_filings(known_doc_nums)
    except Exception as exc:  # noqa: BLE001
        log.error("Clerk portal scrape failed after retries: %s", exc)
        filings = []

    new_records = build_records(filings, parcel_lookup)
    payload = write_outputs(new_records)

    # Export the full accumulated lead list (matching records.json), not
    # just this run's new additions — re-importing the same leads into
    # GoHighLevel repeatedly is usually harmless since GHL dedupes
    # contacts on import, but worth knowing if your workflow assumes
    # otherwise.
    full_records = [LeadRecord(**r) for r in payload["records"]]
    export_ghl_csv(full_records)

    log.info(
        "=== Done: %d new this run, %d total leads, %d with an address ===",
        payload["new_this_run"], payload["total"], payload["with_address"],
    )
    return payload


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Franklin County motivated-seller lead scraper")
    parser.add_argument("--skip-parcels", action="store_true", help="Skip Auditor address enrichment")
    args = parser.parse_args()

    try:
        asyncio.run(run(skip_parcels=args.skip_parcels))
    except Exception as exc:  # noqa: BLE001 - top-level safety net; never let CI step hard-fail silently
        log.error("Fatal error in scraper run: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
