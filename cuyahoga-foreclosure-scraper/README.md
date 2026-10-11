# Cuyahoga County Foreclosure Scraper

Companion to the Franklin County E-Foreclosure scraper (lives in this same
repo, in `../scraper/`), targeting Cuyahoga County's Clerk of Courts
docket at `https://cpdocket.cp.cuyahogacounty.gov/Search.aspx`.

## Running locally

```bash
cd cuyahoga-foreclosure-scraper
pip install -r requirements.txt
cd src
python run_pipeline.py                      # full run (last 7 days)
python run_pipeline.py --date-from 09/01/2026 --date-to 10/01/2026
python run_pipeline.py --limit 20            # cap cases processed, for a quick test
```

## Automation + dashboard

`.github/workflows/scrape-cuyahoga.yml` (at the repo root) runs this
nightly at 05:00 UTC and commits:
- `cuyahoga-foreclosure-scraper/data/leads.json` — the full accumulated
  lead store (merged by case number across runs)
- `../dashboard/cuyahoga.json` — the Cuyahoga tab's data on the combined
  dashboard
- `../dashboard/cuyahoga_ghl_export.csv` — GoHighLevel-ready CSV, also
  linked from the dashboard tab

It's a separate, independent workflow from Franklin's `scrape.yml` - own
schedule, own concurrency group - so a failure in one never blocks or
breaks the other. It doesn't deploy GitHub Pages itself; Franklin's
`scrape.yml` already owns that (it uploads the whole shared `dashboard/`
folder), and this workflow is scheduled two hours earlier specifically so
fresh Cuyahoga data is already committed by the time that deploy step
runs.

## Status

- TOS acceptance: **confirmed and automated** (plain Yes/No button pair,
  `ctl00$SheetContentPlaceHolder$btnYes`).
- Foreclosure search type selection: **confirmed** — AJAX partial postback,
  event target `ctl00$SheetContentPlaceHolder$rbCivilForeclosure`.
- Foreclosure form field names: **confirmed** — Parcel Number, Case
  Year/Sequence, Filing Type, Starting/Ending Filing Date, Active Only,
  Exclude BR Cases, Street Number/Name, City, ZIP, Bankruptcy Stay, and
  the Submit/Clear buttons. All under the
  `ctl00$SheetContentPlaceHolder$foreclosureSearch$` prefix.
- The `Check.aspx` redirect after submitting a search: **confirmed NOT a
  CAPTCHA wall**. It's just an intermediate hop that redirects straight on
  to the real results page, `ForeclosureSearchResults.aspx`. (An earlier
  "looks like a CAPTCHA" flag was a false positive — its only signal was
  the word "challenge" appearing near "recaptcha" in page text, which
  turned out to just be a defendant's surname, "...CHALLENGER...", in a
  real result row. No actual reCAPTCHA widget was ever found.)
  `_async_postback()` now follows this hop automatically.
- Results grid: **confirmed** against a live run (9,626 rows over a 2-year
  date range, returned as a single response with **no pagination**, no
  matter the row count). Table id
  `SheetContentPlaceHolder_ctl00_gvForeclosureResults`, columns Case
  Defendant / Parcel Address / City / Zip / Case Number / Parcel / Status
  / Filed. `parse_results_grid()` is wired up to this exact structure.
- Case Number cells are ASP.NET `__doPostBack()` links, not plain URLs —
  there's no direct "case detail page" link to follow with a simple GET.
  `CuyahogaDocketSession.open_case_detail(listing)` replays that postback
  (confirmed to be a **plain synchronous postback**, not an AJAX delta,
  since `ForeclosureSearchResults.aspx` registers no UpdatePanels).
- Case detail page: **confirmed** — lands on
  `CV_CaseInformation_Summary.aspx?q=<token>`, its "Case Summary" tab.
  This tab is clean structured data (labeled `<span>` fields, not prose),
  including `Prayer Amount` — the actual dollar amount sued for.
  `parse_case_summary()` pulls Case Number, Case Title (split into
  Plaintiff/Defendant), Case Designation, Filing Date, Judge, Last
  Status(+Date), and Prayer Amount, all confirmed against a live run. This
  is now the **primary** source for each lead's core fields — reliable
  structured data beats regex-on-OCR-text, so `run_pipeline.py` builds
  each `LeadRecord` from the results-grid row (property/parcel) plus this
  Summary tab, and does **not** require a complaint PDF to produce a
  usable, scored lead.
- Other tabs on the case detail page (`Docket`, `Parties`, `Costs /
  Payments`, `Service`, `Property`, `All`) are also `__doPostBack()`
  links — confirmed targets are in `CASE_TAB_*` constants, and
  `CuyahogaDocketSession.click_case_tab(target)` replays them the same
  way as `open_case_detail()`.
- The `All` tab: **confirmed** — aggregates Service, Parties (+ attorney
  info), Docket, and Costs onto one page. Its docket table (id
  `SheetContentPlaceHolder_caseDocket_gvDocketInformation`,
  `parse_docket_table()`) has a "View Image" link per filing, format
  `DisplayImageList.aspx?q=<token>` — a plain navigable href, not a
  `__doPostBack()` link. The *original complaint's* own entry is
  identifiable by its docket description (confirmed real example: `"CASE
  FILED: COMPLAINT, PRELIMINARY JUDICIAL REPORT, SERVICE REQUEST, SERVICE
  REQUEST"`, docket type `SF`) — distinct from the many other entries
  (summons returns, "SUMS COMPLAINT(...) SENT BY..." mailing notices,
  etc.) that also have image links. `find_complaint_image_url()` picks
  this one out specifically.
- `DisplayImageList.aspx`: **confirmed** — it's *another* listing page,
  not a document. One docket entry bundles several documents together
  (confirmed real example: the complaint's entry actually packages
  COMPLAINT + PRELIMINARY JUDICIAL REPORT + two SERVICE REQUESTs), and
  this page (table id `SheetContentPlaceHolder_displayImage_gvImageList`,
  `parse_image_list()`) lists each one with its own "Document
  Description" and its own doc-viewer link:
  `i_DisplayDoc.aspx?i=<token>&id=<token>&cid=<token>&dt=<token>`.
  `find_complaint_doc_url()` picks out the row whose Document Description
  is exactly `"COMPLAINT"`.
- `i_DisplayDoc.aspx`: **confirmed** — a real PDF (`Content-Type:
  application/pdf`, starts with `%PDF-`, ~1MB for the one case tested),
  directly downloadable with no further wrapper layer. This is exactly
  what `pdf_text_extractor.extract_text()` expects, so the full chain —
  docket entry → image list → "COMPLAINT" row → PDF → OCR/text-extract →
  `interest_rate`/`loan_type` parsing — is now confirmed end-to-end with
  no remaining guesses anywhere in the pipeline.

## Status: search → complaint PDF fully confirmed end-to-end

Every step from search through the complaint PDF — the Check.aspx hop,
results-grid parsing, the case detail Summary tab, and the three-layer
path to the complaint document — has been verified against real live
data. `lead_parser.py`'s caption-parsing regex is still worth
double-checking against a real complaint's text (it was ported from the
Franklin County scraper, and loan_type/interest_rate wording may differ),
but that's a quality check, not a blocker.

- **REMAINING GAP**: `auditor_enrichment.py` — the parcel lookup that
  fills in `mailing_address` (the single most useful field for a
  direct-mail wholesaling workflow) — is still **entirely guessed**: its
  `FIELD_MAP` field names and `LAYER_ID` have never been run against the
  live ArcGIS service. Without this, every lead's `mailing_address` stays
  blank. `run_pipeline.py`'s enrichment step is now wrapped defensively
  (a failure here just leaves `mailing_address` blank for that lead
  rather than crashing the whole run), and `get_parcel_info()` /
  `get_parcel_info_by_address()` now also handle ArcGIS's
  application-level error responses (which come back as a 200 with an
  `"error"` body, not an HTTP error status) instead of crashing on those.

## Architecture

Same shape as the Franklin pipeline:
- `lead_schema.py` — shared `LeadRecord`
- `cuyahoga_clerk_search.py` — site-specific search/session handling
  (search, Check.aspx hop, results grid, case-detail postback — all
  confirmed end-to-end against live runs)
- `pdf_text_extractor.py` — hybrid pdfplumber + pytesseract OCR
- `lead_parser.py` — regex extraction from complaint text
- `auditor_enrichment.py` — Cuyahoga Fiscal Officer parcel data via live
  ArcGIS REST service (not a monthly CSV like Franklin's Auditor)
- `lead_scoring.py` — 0-100 lead scoring heuristics
- `ghl_export.py` — GoHighLevel-ready CSV export
- `run_pipeline.py` — orchestrator: search → open each case detail → find
  + download complaint PDF → OCR/text-extract → parse → enrich → score →
  accumulate (JSON, merge by key) → export to GHL CSV + writes
  `docs/data.json` for the dashboard
- `.github/workflows/nightly-cuyahoga.yml` — nightly CI, same pattern as
  Franklin's own nightly workflow, but as its own independent schedule/job
  so neither scraper can block or break the other

## Daily automation + dashboard

- **Nightly run**: `.github/workflows/nightly-cuyahoga.yml` runs
  `run_pipeline.py` every night on a GitHub-hosted runner (which — unlike
  this sandbox — has real internet access to the live site), then commits
  the updated `data/leads.json` and `docs/data.json` back to the repo. It's
  a separate workflow file from Franklin's, so a failure in one never
  affects the other. A hard-coded `OCR_TIMEOUT_SECONDS` (60s) wraps the
  Tesseract OCR step in a background thread with a timeout, since Franklin's
  nightly run has hit Tesseract hanging indefinitely on GitHub's runners —
  if that happens here, this lead just loses its bonus `interest_rate`/
  `loan_type` fields rather than stalling the whole run.
- **Dashboard**: `dashboard/index.html` is a self-contained static page
  with a tab per county (Franklin / Cuyahoga), reading each county's own
  `docs/data.json` — stat tiles (total leads, leads with a mailing
  address, average amount owed, average score), a "top cities" bar chart,
  and a leads table sorted by score. It assumes it will be served from the
  **repo root** via GitHub Pages, with each county's project folder as a
  sibling (`franklin-foreclosure-scraper/docs/data.json` and
  `cuyahoga-foreclosure-scraper/docs/data.json`) — move/rename if the real
  repo layout differs, and double check this against whatever Franklin's
  existing dashboard already does before wiring both up, so nothing
  collides.

### Discovery scripts (`src/discover_*.py`)

Standalone scripts used to reverse-engineer the site against real live
responses, since this sandbox has no network access to
`cpdocket.cp.cuyahogacounty.gov`. Each one is safe to re-run if the site
ever changes shape:
- `discover_foreclosure_form.py` — dumps the foreclosure search form's
  fields
- `discover_search_results.py` — submits a 2-year search, dumps whatever
  comes back (superseded by the fix in `discover_check_page.py`, kept for
  reference)
- `discover_check_page.py` — submits a real search and follows the
  Check.aspx redirect properly, to confirm it's not a CAPTCHA wall
- `discover_case_detail.py` — opens one real case's detail page, then
  clicks through to its `All` tab (docket, parties, property, costs)
- `discover_complaint_image.py` — finds a case's complaint docket entry
  and fetches its `DisplayImageList.aspx` link (superseded by
  `discover_complaint_doc.py` below, kept for reference)
- `discover_complaint_doc.py` — goes one layer further: finds the
  specific "COMPLAINT" row in the image list and fetches its
  `i_DisplayDoc.aspx` link, to find out what *that* actually returns
- `discover_parcel_fields.py` — grabs a real parcel number from a live
  search, lists every layer Cuyahoga's ArcGIS parcel service actually
  has, and dumps the real field names/values for each — the last piece
  needed to confirm `auditor_enrichment.py`'s `FIELD_MAP`/`LAYER_ID`
