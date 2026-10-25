"""
Cuyahoga County foreclosure lead pipeline orchestrator.

search -> results grid -> for each case: open case detail (Case Summary
tab = primary source for core fields) -> [bonus-only] docket -> image
list -> complaint PDF -> OCR/text-extract -> parse interest_rate/loan_type
-> enrich with Auditor parcel data -> score -> accumulate (merge by key)
-> export GHL CSV + dashboard data.json

A LeadRecord is built primarily from the results-grid row plus
parse_case_summary() - it does NOT require a complaint PDF to be usable.
The PDF chain below is wrapped in try/except and only ever fills in
*bonus* fields (interest_rate, loan_type) that are not already available
elsewhere - a failure anywhere in that chain never blocks lead creation.
"""

import os
import json
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests

from cuyahoga_clerk_search import (
    CuyahogaDocketSession,
    CaseListing,
    CASE_TAB_ALL,
    parse_docket_table,
    find_complaint_image_url,
    parse_image_list,
    find_complaint_doc_url,
)
from lead_schema import LeadRecord
from lead_parser import parse_complaint_text
import pdf_text_extractor
import auditor_enrichment
from lead_scoring import apply_scores
from ghl_export import export_to_ghl_csv
import ghl_push

COUNTY = "Cuyahoga"

# Pause between cases so a full run isn't a burst of hundreds of requests;
# the court site answers heavy traffic with its Access Denied page.
CASE_DELAY_SECONDS = 1.5

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(PROJECT_DIR)

DATA_DIR = os.path.join(PROJECT_DIR, "data")
LEADS_JSON_PATH = os.path.join(DATA_DIR, "leads.json")
GHL_CSV_PATH = os.path.join(DATA_DIR, "ghl_export.csv")

# This project lives as a sibling folder inside the same repo as the
# Franklin County scraper (mauricewilli572000-cloud/franklin-foreclosure-scraper).
# Franklin's existing GitHub Actions workflow is the one that deploys
# dashboard/ to GitHub Pages (actions/upload-pages-artifact + deploy-pages) -
# we don't touch that workflow, we just make sure our dashboard-facing
# files land in that same shared dashboard/ folder before it runs, so the
# combined dashboard's Cuyahoga tab has something fresh to read.
SHARED_DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")
DASHBOARD_DATA_PATH = os.path.join(SHARED_DASHBOARD_DIR, "cuyahoga.json")
DASHBOARD_CSV_PATH = os.path.join(SHARED_DASHBOARD_DIR, "cuyahoga_ghl_export.csv")

# OCR can hang indefinitely on some CI runners even though it works fine
# locally (confirmed issue in the Franklin County scraper's nightly run).
# Bonus-field enrichment is not worth risking the whole pipeline over, so
# it gets a hard wall-clock timeout.
OCR_TIMEOUT_SECONDS = 60


def _extract_text_with_timeout(pdf_path: str, timeout_seconds: int = OCR_TIMEOUT_SECONDS) -> str:
    """Run pdf_text_extractor.extract_text() in a thread with a timeout,
    so a Tesseract hang can't stall the whole nightly run. Returns ""
    if it times out or raises."""
    result: Dict[str, str] = {}

    def _worker():
        try:
            result["text"] = pdf_text_extractor.extract_text(pdf_path)
        except Exception:
            result["text"] = ""

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        # Thread is still stuck in native Tesseract code; we can't kill it
        # cleanly, but we can stop waiting on it and move on. It'll be
        # reclaimed when the process exits.
        return ""

    return result.get("text", "")


def _load_existing_leads() -> Dict[str, dict]:
    if not os.path.exists(LEADS_JSON_PATH):
        return {}
    try:
        with open(LEADS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item["key"]: item for item in data}
    except Exception:
        return {}


def _save_leads(leads_by_key: Dict[str, dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LEADS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(list(leads_by_key.values()), f, indent=2)


_COMPLAINT_STATS: Dict[str, object] = {
    "attempts": 0,
    "no_docket_entries": 0,
    "no_complaint_image_url": 0,
    "no_complaint_doc_url": 0,
    "not_a_pdf": 0,
    # Cases with a single document: the "image list" address returns the PDF itself.
    "pdf_returned_directly": 0,
    "no_text_extracted": 0,
    "text_extracted": 0,
    "interest_rate_found": 0,
    "loan_type_found": 0,
    "amount_filled_from_complaint": 0,
    "errors": 0,
    "sample_errors": [],
    # Short excerpts of extracted text around the word "interest" for leads
    # where no rate parsed - shows the real wording so the pattern can be fixed.
    "no_rate_text_samples": [],
    # What the image list held for cases where no complaint document was found.
    "no_doc_url_samples": [],
    # Complaint text near "$" for leads still without an amount.
    "no_amount_text_samples": [],
}


def _enrich_with_complaint_pdf(session: CuyahogaDocketSession, lead: LeadRecord) -> None:
    """Best-effort bonus enrichment. Never raises - any failure just
    leaves interest_rate/loan_type unset for this lead, but is counted in
    _COMPLAINT_STATS so last_run_debug.json shows which step lost it."""
    _COMPLAINT_STATS["attempts"] += 1
    try:
        all_html = session.click_case_tab(CASE_TAB_ALL)
        docket_entries = parse_docket_table(all_html)
        if not docket_entries:
            _COMPLAINT_STATS["no_docket_entries"] += 1
            return
        image_list_url = find_complaint_image_url(docket_entries)
        if not image_list_url:
            _COMPLAINT_STATS["no_complaint_image_url"] += 1
            return

        resp = session.session.get(image_list_url, headers={"Referer": session.current_url})
        resp.raise_for_status()
        if resp.content[:5] == b"%PDF-" or "pdf" in resp.headers.get("Content-Type", "").lower():
            # Single-document case: the server hands back the PDF itself
            # rather than a list page (~half of cases in a real run).
            _COMPLAINT_STATS["pdf_returned_directly"] += 1
            pdf_bytes = resp.content
        else:
            image_entries = parse_image_list(resp.text)
            doc_url = find_complaint_doc_url(image_entries)
            if not doc_url:
                _COMPLAINT_STATS["no_complaint_doc_url"] += 1
                samples = _COMPLAINT_STATS["no_doc_url_samples"]
                if len(samples) < 8:
                    # Show what the image list actually contained so the finder
                    # can be taught the real labels.
                    sample = {
                        "case": lead.case_number,
                        "plaintiff": lead.plaintiff,
                        "amount_owed": lead.amount_owed,
                        "image_entries": [
                            {"desc": e.description, "doc_desc": e.document_description,
                             "has_url": bool(e.doc_url)}
                            for e in image_entries[:8]
                        ],
                    }
                    if not image_entries:
                        # Empty list: either the case has no online documents or
                        # the page isn't in the layout parse_image_list expects.
                        # Record enough of the page to tell which.
                        from bs4 import BeautifulSoup
                        page = BeautifulSoup(resp.text, "html.parser")
                        sample["image_list_url"] = image_list_url
                        sample["table_ids"] = [t.get("id") for t in page.find_all("table") if t.get("id")][:8]
                        sample["page_text_head"] = " ".join(page.get_text(" ", strip=True).split())[:400]
                    samples.append(sample)
                return

            pdf_resp = session.session.get(doc_url, headers={"Referer": image_list_url})
            pdf_resp.raise_for_status()
            if "pdf" not in pdf_resp.headers.get("Content-Type", "").lower():
                _COMPLAINT_STATS["not_a_pdf"] += 1
                return

            pdf_bytes = pdf_resp.content

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            text = _extract_text_with_timeout(tmp_path)
            if not (text or "").strip():
                _COMPLAINT_STATS["no_text_extracted"] += 1
            else:
                _COMPLAINT_STATS["text_extracted"] += 1
            parsed = parse_complaint_text(text)
            # The court summary's prayer amount is 0/blank for some cases; the
            # complaint text usually states the amount due.
            if not lead.amount_owed and parsed.amount_owed:
                lead.amount_owed = parsed.amount_owed
                _COMPLAINT_STATS["amount_filled_from_complaint"] += 1
            elif not lead.amount_owed and text and text.strip():
                amt_samples = _COMPLAINT_STATS["no_amount_text_samples"]
                if len(amt_samples) < 25:
                    import re as _re
                    # Show every dollar figure with its surrounding words so the
                    # wording for the amount due is visible, not just the first $.
                    snippets = [
                        " ".join(text[max(0, m.start() - 90): m.end() + 40].split())
                        for m in _re.finditer(r"\$\s*[\d,]+(?:\.\d{2})?", text)
                    ][:6]
                    amt_samples.append({
                        "case": lead.case_number,
                        "dollar_snippets": snippets or ["(no $ in text)"],
                    })
            if not lead.interest_rate and parsed.interest_rate:
                lead.interest_rate = parsed.interest_rate
            if not lead.loan_type and parsed.loan_type:
                lead.loan_type = parsed.loan_type
            if lead.interest_rate:
                _COMPLAINT_STATS["interest_rate_found"] += 1
            elif text and text.strip():
                samples = _COMPLAINT_STATS["no_rate_text_samples"]
                if len(samples) < 4:
                    idx = text.lower().find("interest")
                    start = max(0, idx - 150) if idx >= 0 else 0
                    samples.append({
                        "case": lead.case_number,
                        "text_length": len(text),
                        "around_interest": text[start:start + 500],
                    })
            if lead.loan_type:
                _COMPLAINT_STATS["loan_type_found"] += 1
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        _COMPLAINT_STATS["errors"] += 1
        samples = _COMPLAINT_STATS["sample_errors"]
        if len(samples) < 3:
            samples.append(f"{lead.case_number}: {type(exc).__name__}: {exc}")
        return


_AUDITOR_STATS: Dict[str, object] = {
    "attempts": 0,
    "successes": 0,
    "no_match_by_parcel": 0,
    "no_match_by_address": 0,
    "errors": 0,
    "sample_errors": [],
}


def _enrich_with_auditor(lead: LeadRecord) -> None:
    """Best-effort parcel enrichment. Never raises - but records what
    happened in _AUDITOR_STATS so last_run_debug.json can show *why*
    mailing_address didn't populate for a given batch, instead of only
    whether it did."""
    _AUDITOR_STATS["attempts"] += 1
    try:
        info = {}
        if lead.parcel_number:
            info = auditor_enrichment.get_parcel_info(lead.parcel_number)
            if not info:
                _AUDITOR_STATS["no_match_by_parcel"] += 1
        if not info and lead.property_address:
            street_parts = lead.property_address.split(None, 1)
            street_number = street_parts[0] if street_parts else ""
            street_name = street_parts[1] if len(street_parts) > 1 else ""
            info = auditor_enrichment.get_parcel_info_by_address(
                street_number, street_name, lead.property_city or ""
            )
            if not info:
                _AUDITOR_STATS["no_match_by_address"] += 1

        if info.get("owner_name") and not lead.owner_name:
            lead.owner_name = info["owner_name"]
        if info.get("mailing_address"):
            lead.mailing_address = info["mailing_address"]
            _AUDITOR_STATS["successes"] += 1
    except Exception as exc:
        _AUDITOR_STATS["errors"] += 1
        samples = _AUDITOR_STATS["sample_errors"]
        if len(samples) < 3:
            samples.append(f"{lead.case_number} ({lead.parcel_number}): {type(exc).__name__}: {exc}")
        return


def build_lead_from_case(session: CuyahogaDocketSession, listing: CaseListing) -> Optional[LeadRecord]:
    try:
        detail_html = session.open_case_detail(listing)
    except Exception:
        return None

    from cuyahoga_clerk_search import parse_case_summary
    summary = parse_case_summary(detail_html)

    lead = LeadRecord(
        county=COUNTY,
        case_number=listing.case_number or summary.case_number,
        defendant=listing.defendant or summary.defendant,
        plaintiff=summary.plaintiff,
        property_address=listing.property_address,
        property_city=listing.property_city,
        property_zip=listing.property_zip,
        parcel_number=listing.parcel_number,
        filing_date=listing.filed_date or summary.filing_date,
        amount_owed=summary.prayer_amount,
        source_url=session.current_url,
    )

    # Bonus-only enrichment - never blocks lead creation on failure.
    _enrich_with_complaint_pdf(session, lead)
    _enrich_with_auditor(lead)

    return lead


def _write_dashboard_data(leads: List[LeadRecord]) -> None:
    os.makedirs(SHARED_DASHBOARD_DIR, exist_ok=True)

    total = len(leads)
    with_mailing = sum(1 for l in leads if l.mailing_address)
    with_amount = [l for l in leads if l.amount_owed]
    avg_amount = round(sum(l.amount_owed for l in with_amount) / len(with_amount), 2) if with_amount else None
    avg_score = round(sum(l.lead_score for l in leads) / total, 1) if total else 0

    by_city: Dict[str, int] = {}
    for l in leads:
        city = l.property_city or "Unknown"
        by_city[city] = by_city.get(city, 0) + 1

    payload = {
        "county": COUNTY,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_leads": total,
        "leads_with_mailing_address": with_mailing,
        "average_amount_owed": avg_amount,
        "average_lead_score": avg_score,
        "leads_by_city": sorted(by_city.items(), key=lambda kv: -kv[1])[:25],
        "leads": [l.to_dict() for l in sorted(leads, key=lambda l: -l.lead_score)],
    }

    with open(DASHBOARD_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


DEBUG_PATH = os.path.join(DATA_DIR, "last_run_debug.json")


def _merge_debug(**fields) -> None:
    """Add fields to the debug file written earlier in the run."""
    try:
        with open(DEBUG_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        payload.update(fields)
        with open(DEBUG_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _write_debug(**fields) -> None:
    """Always-written diagnostic file - committed every run regardless of
    how many leads came out, so a quiet run (0 listings, or listings found
    but every case detail fetch failing) is visible without needing to
    pull CI logs."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {"generated_at": datetime.utcnow().isoformat() + "Z", **fields}
    with open(DEBUG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def run(date_from: Optional[str] = None, date_to: Optional[str] = None, limit: Optional[int] = None) -> List[LeadRecord]:
    if date_to is None:
        date_to = datetime.now().strftime("%m/%d/%Y")
    if date_from is None:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")

    try:
        session = CuyahogaDocketSession()
        search_html = session.load_search_page()
        type_select_html = session.select_foreclosure_search()
        html, was_async = session.submit_foreclosure_search(date_from=date_from, date_to=date_to)

        from cuyahoga_clerk_search import parse_results_grid
        listings = parse_results_grid(html)
        if "Denied.aspx" in (session.current_url or ""):
            raise RuntimeError(
                "The court site returned its Access Denied page (Denied.aspx) "
                "instead of search results - it is likely rate-limiting this "
                "IP after heavy use. Wait a few hours and run again."
            )
    except Exception as exc:
        _write_debug(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    listings_found = len(listings)
    search_debug_fields = dict(
        search_page_html_length=len(search_html),
        type_select_html_length=len(type_select_html),
        submit_was_async=was_async,
        submit_html_length=len(html),
        submit_html_head=html[:1500],
        submit_postback_status=session.last_postback_status,
        submit_postback_delta_keys=session.last_postback_delta_keys,
        submit_postback_raw_head=session.last_postback_raw_head,
        current_url_after_submit=session.current_url,
    )

    if limit:
        listings = listings[:limit]

    existing = _load_existing_leads()
    new_leads: List[LeadRecord] = []
    case_detail_failures = 0

    blocked_mid_run = False
    for i, listing in enumerate(listings):
        if i:
            time.sleep(CASE_DELAY_SECONDS)
        lead = build_lead_from_case(session, listing)
        if lead is not None:
            new_leads.append(lead)
        else:
            case_detail_failures += 1
        if "Denied.aspx" in (session.current_url or ""):
            # Blocked partway: keep what was collected, stop hammering the site.
            blocked_mid_run = True
            break

    for lead in new_leads:
        existing[lead.key()] = lead.to_dict()
        existing[lead.key()]["key"] = lead.key()

    all_leads = [LeadRecord(**{k: v for k, v in item.items() if k != "key"}) for item in existing.values()]

    # Backfill: leads saved by earlier runs (before the countywide parcel
    # source) that never got a mailing address are retried each run.
    new_keys = {l.key() for l in new_leads}
    backfilled = 0
    for lead, item in zip(all_leads, existing.values()):
        if lead.mailing_address or lead.key() in new_keys:
            continue
        _enrich_with_auditor(lead)
        if lead.mailing_address:
            backfilled += 1
            item["mailing_address"] = lead.mailing_address
            if lead.owner_name:
                item["owner_name"] = lead.owner_name
    _AUDITOR_STATS["backfilled_existing_leads"] = backfilled

    apply_scores(all_leads)

    for lead, item in zip(all_leads, existing.values()):
        item["lead_score"] = lead.lead_score
        item["key"] = lead.key()

    _write_debug(
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        listings_found=listings_found,
        listings_processed=len(listings),
        case_detail_failures=case_detail_failures,
        new_leads_this_run=len(new_leads),
        total_leads_on_file=len(all_leads),
        auditor_enrichment_stats=_AUDITOR_STATS,
        complaint_pdf_stats=_COMPLAINT_STATS,
        blocked_mid_run=blocked_mid_run,
        **search_debug_fields,
    )

    _save_leads(existing)
    os.makedirs(DATA_DIR, exist_ok=True)
    export_to_ghl_csv(all_leads, GHL_CSV_PATH)
    os.makedirs(SHARED_DASHBOARD_DIR, exist_ok=True)
    export_to_ghl_csv(all_leads, DASHBOARD_CSV_PATH)
    _write_dashboard_data(all_leads)

    # Optional: push new leads to GoHighLevel (does nothing unless the
    # GHL_WEBHOOK_URL environment variable is set). Never fails the run.
    try:
        ghl_stats = ghl_push.push_new_leads(all_leads, DATA_DIR)
    except Exception as exc:
        ghl_stats = {"enabled": True, "error": f"{type(exc).__name__}: {exc}"}
    _merge_debug(ghl_push=ghl_stats)

    return all_leads


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the Cuyahoga foreclosure lead pipeline")
    parser.add_argument("--date-from", dest="date_from", default=None, help="MM/DD/YYYY")
    parser.add_argument("--date-to", dest="date_to", default=None, help="MM/DD/YYYY")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of cases processed (useful for testing)")
    args = parser.parse_args()

    leads = run(date_from=args.date_from, date_to=args.date_to, limit=args.limit)
    print(f"Processed {len(leads)} total leads. "
          f"Output: {LEADS_JSON_PATH}, {GHL_CSV_PATH}, {DASHBOARD_DATA_PATH}, {DASHBOARD_CSV_PATH}")
