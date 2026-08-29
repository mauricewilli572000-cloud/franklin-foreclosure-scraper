# Installing the Franklin County Lead Scraper

This walks through getting the project from the zip file to a working
GitHub Actions pipeline with a live dashboard. Since you're newer to
Git/GitHub, every command is spelled out — copy/paste them one at a time
into a terminal.

---

## 0. What you need first

- A **GitHub account** (you already have one for the other Franklin County scraper).
- **Git** installed. Check with:
  ```
  git --version
  ```
  If that errors, install it from https://git-scm.com/downloads and re-open your terminal.
- **Python 3.11+** installed. Check with:
  ```
  python3 --version
  ```
  If missing, install from https://www.python.org/downloads/.

---

## 1. Unzip the project

Unzip `franklin-county-scraper.zip` somewhere sensible, e.g. your
Documents folder. Then open a terminal and move into it:

```bash
cd path/to/franklin-county-scraper
```

(On Windows, use PowerShell or Git Bash and `cd` the same way.)

---

## 2. Test it locally BEFORE pushing to GitHub

This catches selector/dependency problems on your machine, where it's
much faster to iterate than waiting on CI.

**a) Create a virtual environment and install dependencies:**

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r scraper/requirements.txt
python -m playwright install --with-deps chromium
```

**b) Install Tesseract OCR** (needed for scanned/image-only PDF pages):

- **Mac:** `brew install tesseract`
- **Windows:** install from https://github.com/UB-Mannheim/tesseract/wiki, then make sure `tesseract.exe` is on your PATH
- **Linux:** `sudo apt-get install tesseract-ocr`

Verify it's found:
```bash
tesseract --version
```

**c) Run the scraper:**

```bash
cd scraper
python fetch.py
```

Watch the log output. On a first real run against the live portal, expect
this to fail or come back with 0 records — the clerk-portal selectors and
the document-download selector are best-effort guesses (see the README's
"Things to verify" section) until you fix them against the real site.
That's the next step.

---

## 3. Fix the selectors against the live site

1. Open https://clerknewfiling.franklincountyohio.gov/ in Chrome.
2. Search for a foreclosure case manually so you can see the real search
   form and results grid.
3. Right-click the search box → **Inspect**. Note the actual `id`,
   `name`, or `placeholder` attribute.
4. Do the same for the date-range fields, the Search button, and the
   results table/rows.
5. Open `scraper/fetch.py`, find the `CLERK_SELECTORS` dictionary near
   the top, and update each entry to match what you found.
6. Click into one case's detail page and find the control that downloads
   its documents as a ZIP. Inspect that element too, and update
   `CLERK_SELECTORS["document_zip_link"]`.
7. Re-run `python fetch.py` locally and check `dashboard/records.json` —
   you should start seeing real records with populated fields.

If a field (interest rate, amount, loan type) keeps coming back empty,
open one of the downloaded PDFs and compare its actual wording to the
patterns in `scraper/doc_parser.py`'s `FIELD_PATTERNS` dict, then adjust.

---

## 4. Create the GitHub repository

**a)** Go to https://github.com/new
- Repository name: `franklin-county-scraper` (or whatever you like)
- Keep it **Public** (required for free GitHub Pages) or **Private** if
  you have GitHub Pro/Team (Pages works on private repos there too)
- Don't initialize with a README (you already have one) — leave those
  checkboxes unchecked
- Click **Create repository**

**b)** Back in your terminal, from the project folder:

```bash
git init
git add .
git commit -m "Initial commit: Franklin County lead scraper"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/franklin-county-scraper.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your actual GitHub username. GitHub will
prompt you to sign in (browser popup or a personal access token,
depending on how your Git is configured).

---

## 5. Give the workflow permission to commit results back

By default, GitHub Actions can't push commits to your repo. Turn that on:

1. On GitHub, go to your repo → **Settings** → **Actions** → **General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions**
4. Click **Save**

---

## 6. Turn on GitHub Pages for the dashboard

1. Repo → **Settings** → **Pages**
2. Under **Build and deployment → Source**, select **GitHub Actions**
   (not "Deploy from a branch")
3. That's it — the workflow itself handles the actual deploy step.

---

## 7. Run it for the first time

1. Repo → **Actions** tab
2. Click **Franklin County Lead Scrape** in the left sidebar
3. Click **Run workflow** → **Run workflow** (this is the
   `workflow_dispatch` trigger — no need to wait for the 7am UTC cron)
4. Click into the running job and watch the logs live. This will take a
   few minutes the first time (installing Chromium + Tesseract, running
   the scrape).

If it fails, the logs will show exactly which step — most likely a
selector issue if you skipped step 3, or a permissions issue if step 5
was missed.

---

## 8. Check the results

- **Dashboard:** Repo → Settings → Pages will show your live URL
  (something like `https://your-username.github.io/franklin-county-scraper/`)
  once the first successful deploy finishes.
- **Raw data:** `dashboard/records.json` and `data/records.json` in the
  repo will be updated and committed automatically.
- **GHL import file:** `dashboard/ghl_export.csv` — download this
  straight from GitHub (or fetch it via the raw file URL) and import it
  into GoHighLevel.

---

## 9. Ongoing operation

Once steps 1–7 are done, the workflow runs itself automatically every
day at 07:00 UTC (~3:00 AM Eastern) via the cron schedule already in
`.github/workflows/scrape.yml`. You don't need to do anything unless:

- The clerk portal's HTML structure changes (selectors break — check
  Actions logs for warnings like "Could not fill selector")
- The Auditor moves/renames the bulk parcel file in a way the
  directory-listing discovery can't find
- You want to widen the lookback window — edit the `--lookback-days`
  default or the cron schedule in `scrape.yml`

Check the **Actions** tab occasionally (or set up email notifications
under your GitHub notification settings for failed workflow runs) so you
notice if a nightly run starts failing.
