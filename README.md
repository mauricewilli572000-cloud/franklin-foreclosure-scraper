# Franklin County, OH — Motivated Seller Lead Scraper (E-Foreclosures)

Nightly pipeline that pulls E-Foreclosure filings from the Franklin County
Clerk of Courts new-filing portal, enriches owners with mailing/property
address from the Auditor's bulk parcel data, scores each lead 0-100 on
motivated-seller signals, and publishes the results as a dashboard on
GitHub Pages plus a GoHighLevel-ready CSV.

## Layout

```
scraper/fetch.py            # main scraper (Playwright + requests/BeautifulSoup + dbfread)
scraper/requirements.txt
dashboard/index.html        # static dashboard, reads dashboard/records.json
dashboard/records.json      # latest output (also mirrored to data/records.json)
dashboard/ghl_export.csv    # regenerated each run
data/records.json           # same payload as dashboard/records.json, kept for
                             # anyone consuming the data outside the dashboard
.github/workflows/scrape.yml
```

## Running locally

```bash
cd scraper
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python fetch.py                    # full run
python fetch.py --lookback-days 14 # wider window
python fetch.py --skip-parcels     # skip Auditor address enrichment (faster iteration)
```

## How the document-reading step works

The results grid on the clerk portal only exposes a few fields (case
number, parties, filed date). Interest rate, amount owed, loan type, and
the full legal description live in the filed PDFs themselves, which are
served as a per-case ZIP bundle. So for every case found in the grid,
`fetch.py` now:

1. Opens the case's detail page in a new Playwright tab and clicks the
   documents-download control, capturing the resulting ZIP via
   `page.expect_download()`.
2. Pulls every PDF out of that ZIP (`doc_parser.extract_pdfs_from_zip`).
3. Extracts text per page with `pdfplumber`. Any page whose extracted
   text is too short to be real (a scan with no text layer — common for
   older/stamped filings) is rasterized with PyMuPDF and OCR'd with
   Tesseract instead (`doc_parser.extract_text_hybrid`).
4. Regex-parses the combined text for case number, doc type, filed date,
   grantor, grantee, interest rate, amount owed, loan type, and legal
   description (`doc_parser.parse_fields_from_text`), and merges whatever
   it finds into the grid-sourced record — document values win where
   present, grid values are kept as a fallback.

This runs with a concurrency limit of 3 cases at a time
(`DOCUMENT_ENRICHMENT_CONCURRENCY`) so it doesn't hammer the portal or
peg the CI runner's CPU with OCR work. A case whose ZIP fails to download,
or whose PDFs won't parse, is logged and skipped — the record still comes
through with whatever the grid gave it.

I tested the extraction and regex logic end-to-end against synthetic PDFs
(both a normal text-layer PDF and an image-only/scanned page) and both
extract cleanly. The regex patterns in `doc_parser.FIELD_PATTERNS` are
written against common Ohio foreclosure-filing phrasing, but real filings
from different bank-counsel firms format things differently — pull a
handful of actual downloaded PDFs early on and tune the patterns there if
fields come back empty.

## ⚠️ Things to verify against the live site before relying on this in production

This was built without the ability to execute JavaScript against the live
clerk portal, so three pieces are best-effort and should be checked once
against the real site before trusting the output:

1. **Clerk portal search/results selectors** — `clerknewfiling.franklincountyohio.gov`
   is a client-rendered SPA (the raw HTML is just a loading shell). All the
   CSS/ARIA selectors it needs live in `CLERK_SELECTORS` at the top of
   `scraper/fetch.py`. Open the site in a real browser, use devtools
   "Inspect Element" on the search box, date fields, and results grid, and
   correct the selectors there. The row parser (`parse_result_row`) also
   guesses column order from the flattened row text via regex — once you
   can see the real grid, it's worth replacing that with direct
   `row.locator("td").nth(i)` lookups for reliability.
2. **Document download control** — `CLERK_SELECTORS["document_zip_link"]`
   guesses at a "Download All" / ZIP link on each case's detail page.
   Open one real case, find the actual download control, and lock in its
   selector — this is the piece most likely to need a real selector
   rather than a guess, since download UIs vary a lot.
3. **Auditor bulk parcel file** — `find_parcel_shapefile_url()` scrapes the
   Auditor's `GIS_Shapefiles/CurrentExtracts/` directory listing for a
   `*.zip` containing "parcel" rather than hardcoding a filename, since the
   Auditor rotates these periodically. If you already know the exact URL,
   set `PARCEL_SHAPEFILE_ZIP_URL` in `fetch.py` to skip discovery. Column
   names inside the DBF (`OWNER`/`OWN1`, `SITE_ADDR`/`SITEADDR`, etc.) are
   also resolved case-insensitively from a candidate list — if the real
   file uses different names, add them to the `*_COLUMNS` tuples near the
   top of the parcel section.

Nothing in the pipeline raises on a single bad row, bad case, or bad PDF —
a failure at any of these points is logged and the run continues.

## GitHub Actions

`.github/workflows/scrape.yml` runs daily at 07:00 UTC and on manual
dispatch. It installs Chromium via Playwright, runs `scraper/fetch.py`,
commits any changed `records.json`/CSV files back to the repo, and deploys
`dashboard/` to GitHub Pages. Enable Pages under
**Settings → Pages → Source: GitHub Actions** once this workflow has run
at least once.

## Scoring

Base score 30, then:
- +10 per motivated-seller flag detected
- +20 bonus if both "Lis pendens" and "Pre-foreclosure" flags are present
- +15 if amount owed > $100k, else +10 if > $50k
- +5 if the filing is new since the last run ("New this week")
- +5 if a property/mailing address was matched

Score is clamped to 0–100.

Flags: Lis pendens, Pre-foreclosure, Judgment lien, Tax lien, Mechanic lien,
Probate / estate, LLC / corp owner, New this week.
