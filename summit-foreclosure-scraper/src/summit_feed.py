"""Summit County Clerk 'new civil filings' feed: https://newcivilfilings.summitoh.net/

The page is JavaScript-rendered, so Playwright loads it and we read the rendered table. Rows show:
Action ("Get Docs") | Case Name (PLAINTIFF -vs- DEFENDANT) | Date Added | Case Category | Status.
No case number is shown, so a lead is keyed on a hash of case name + date added.

STATUS: parse_rows() is written from the page text only. After discover_summit.py output arrives,
tighten selectors here and implement fetch_docs() (the "Get Docs" action).
"""
import hashlib
import re
from typing import List

from bs4 import BeautifulSoup

import os

import config
import parsers

LAST_FEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "last_feed.html")

HEADERS = {"case_name": r"case name", "date_added": r"date added", "category": r"categor", "status": r"status"}


def parse_rows(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows, colmap = [], None
    for tr in soup.find_all("tr"):
        cells = [parsers.clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        if any(re.search(r"case name", c, re.I) for c in cells):          # header row
            colmap = {k: next((i for i, c in enumerate(cells) if re.search(p, c, re.I)), None) for k, p in HEADERS.items()}
            continue
        name_idx = next((i for i, c in enumerate(cells) if re.search(r"\s-?vs\.?-?\s", c, re.I)), None)
        if name_idx is None:
            continue
        g = lambda k, off: cells[colmap[k]] if colmap and colmap.get(k) is not None and colmap[k] < len(cells) \
            else (cells[name_idx + off] if name_idx + off < len(cells) else "")
        added = g("date_added", 1)
        rows.append({
            "case_name": cells[name_idx],
            "date_added_raw": added,
            "date_added": parsers.parse_date(added),
            "category": g("category", 2),
            "review_status": g("status", 3),
        })
    return rows


def split_case_name(name: str):
    """'WELLS FARGO BANK, N.A. -vs- JOHN SMITH, ET AL' -> (plaintiff, [defendants])."""
    return parsers.split_caption(re.sub(r"\s-?vs\.?-?\s", " vs. ", parsers.clean(name), flags=re.I))


def lead_key(case_name: str, date_added_raw: str) -> str:
    return hashlib.sha1(f"{parsers.clean(case_name).lower()}|{parsers.clean(date_added_raw)}".encode()).hexdigest()[:12]


class SummitFeed:
    def __init__(self, headless=True):
        self.headless = headless

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch(headless=self.headless)
        self.page = self._b.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        self.page.set_default_timeout(30000)
        return self

    def __exit__(self, *a):
        for o in (self._b, self._pw):
            try:
                o.close() if hasattr(o, "close") else o.stop()
            except Exception:
                pass

    def fetch(self) -> List[dict]:
        self.page.goto(config.FEED_URL, wait_until="networkidle")
        try:
            self.page.wait_for_function("document.body.innerText.toLowerCase().includes('-vs-')", timeout=30000)
        except Exception:
            pass
        os.makedirs(os.path.dirname(LAST_FEED), exist_ok=True)
        open(LAST_FEED, "w", encoding="utf-8").write(self.page.content())   # for debugging
        return parse_rows(self.page.content())

    def fetch_docs(self, row: dict):
        """TODO after discover_summit.py: click 'Get Docs' for this row and return the complaint PDF bytes/URL."""
        raise NotImplementedError("document step not built yet")
