# Summit County foreclosure scraper

Sibling of `cuyahoga-foreclosure-scraper/` in this repo. Same shape: own workflow (`scrape-summit.yml`, 05:30 UTC),
own data (`data/leads.json`), and two files in the shared `dashboard/` folder: `summit.json` and `summit_ghl_export.csv`.

Source: Summit County Clerk "new civil filings" feed (https://newcivilfilings.summitoh.net/), JavaScript-rendered,
so it uses Playwright. Rows within a day or two of filing; the Clerk may still reject some ("Pending clerk review" flag).

The feed has no case number and no address:
- `case_number` is `SUM-` + a hash of the case name and time added.
- Owner = defendants; property/mailing address come from `data/summit_parcels.csv` (Summit County Fiscal Officer export)
  by unique owner-name match, flagged "Address from name match". Leads without an address still show on the dashboard
  but stay out of the GHL CSV (same rule as Franklin).

## Status
Built from the page text only. Run `src/discover_summit.py` once and send `data/debug_summit/` back so the row parser can
be confirmed and the "Get Docs" step (complaint PDF -> address, amount, rate, loan type) can be added.

## Run locally
    pip install -r requirements.txt
    python -m playwright install chromium
    python -m pytest -q tests
    cd src && python run_pipeline.py --headed
