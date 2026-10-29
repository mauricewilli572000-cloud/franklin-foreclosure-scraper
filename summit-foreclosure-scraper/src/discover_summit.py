"""Run once on your computer; send back the data\\debug_summit folder (zipped).

    python discover_summit.py

Loads https://newcivilfilings.summitoh.net/ ONCE in a visible browser, records how the table is
loaded (network calls / websockets), saves the rendered page, ticks Foreclosure, and clicks "Get Docs"
on ONE foreclosure row to see where documents come from. It makes a handful of requests, nothing more.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://newcivilfilings.summitoh.net/"
OUT = Path(__file__).resolve().parent.parent / "data" / "debug_summit"
OUT.mkdir(parents=True, exist_ok=True)
log, ws_log, new_pages = [], [], []


def on_resp(r):
    entry = {"method": r.request.method, "url": r.url, "status": r.status,
             "type": r.headers.get("content-type", "")}
    try:
        if any(k in entry["type"] for k in ("json", "text/plain", "xml", "pdf")) or r.request.resource_type in ("xhr", "fetch"):
            body = r.body()
            entry["bytes"] = len(body)
            name = f"resp_{len(log):03d}" + (".pdf" if "pdf" in entry["type"] else ".txt")
            (OUT / name).write_bytes(body[:3_000_000])
            entry["file"] = name
    except Exception as e:
        entry["note"] = str(e)[:100]
    log.append(entry)


def snap(page, name):
    try:
        (OUT / f"{name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        (OUT / f"{name}.txt").write_text(page.inner_text("body"), encoding="utf-8")
    except Exception as e:
        print("snapshot failed:", e)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    page = ctx.new_page()
    page.on("response", on_resp)
    page.on("websocket", lambda ws: ws_log.append(ws.url))
    ctx.on("page", lambda pg: new_pages.append(pg.url))

    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(6000)
    snap(page, "01_loaded")

    # table structure
    summary = {"rows": page.locator("tr").count(), "checkboxes": page.locator("input[type=checkbox]").count(),
               "buttons": [b.strip() for b in page.locator("button, a").all_inner_texts()][:60]}
    print(summary)

    # tick Foreclosure only (if there is such a checkbox)
    try:
        page.get_by_text("Foreclosure", exact=False).first.click()
        page.wait_for_timeout(2500)
        snap(page, "02_foreclosure_filter")
    except Exception as e:
        print("could not click Foreclosure filter:", e)

    # click Get Docs on the first foreclosure row
    doc_result = {}
    try:
        row = page.locator("tr", has_text="Foreclosure").first
        doc_result["row_text"] = row.inner_text()
        btn = row.get_by_text("Get Docs").first
        try:
            with page.expect_download(timeout=8000) as dl:
                btn.click()
            d = dl.value
            d.save_as(str(OUT / ("doc_" + d.suggested_filename)))
            doc_result["download"] = d.suggested_filename
        except Exception:
            page.wait_for_timeout(4000)
        doc_result["new_pages"] = new_pages[:]
        snap(page, "03_after_get_docs")
    except Exception as e:
        doc_result["error"] = str(e)[:200]
    print(doc_result)

    (OUT / "network_log.json").write_text(json.dumps(
        {"summary": summary, "doc_result": doc_result, "websockets": ws_log, "responses": log}, indent=2), encoding="utf-8")
    browser.close()
print(f"Done. Zip and send: {OUT.resolve()}")
