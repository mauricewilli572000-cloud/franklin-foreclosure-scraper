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

COUNTY = "Cuyahoga"

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


def _enrich_with_complaint_pdf(session: CuyahogaDocketSession, lead: LeadRecord) -> None:
    """Best-effort bonus enrichment. Never raises - any failure just
    leaves interest_rate/loan_type unset for this lead."""
    try:
        all_html = session.click_case_tab(CASE_TAB_ALL)
        docket_entries = parse_docket_table(all_html)
        image_list_url = find_complaint_image_url(docket_entries)
        if not image_list_url:
            return

        resp = session.session.get(image_list_url, headers={"Referer": session.current_url})
        resp.raise_for_status()
        image_entries = parse_image_list(resp.text)
        doc_url = find_complaint_doc_url(image_entries)
        if not doc_url:
            return

        pdf_resp = session.session.get(doc_url, headers={"Referer": image_list_url})
        pdf_resp.raise_for_status()
        if "pdf" not in pdf_resp.headers.get("Content-Type", "").lower():
            return

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_resp.content)
            tmp_path = tmp.name

        try:
            text = _extract_text_with_timeout(tmp_path)
            parsed = parse_complaint_text(text)
            if not lead.interest_rate and parsed.interest_rate:
                lead.interest_rate = parsed.interest_rate
            if not lead.loan_type and parsed.loan_type:
                lead.loan_type = parsed.loan_type
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        return


def _enrich_with_auditor(lead: LeadRecord) -> None:
    """Best-effort parcel enrichment. Never raises."""
    try:
        info = {}
        if lead.parcel_number:
            info = auditor_enrichment.get_parcel_info(lead.parcel_number)
        if not info and lead.property_address:
            street_parts = lead.property_address.split(" ", 1)
            street_number = street_parts[0] if street_parts else ""
            street_name = street_parts[1] if len(street_parts) > 1 else ""
            info = auditor_enrichment.get_parcel_info_by_address(street_number, street_name)

        if info.get("owner_name") and not lead.owner_name:
            lead.owner_name = info["owner_name"]
        if info.get("mailing_address"):
            lead.mailing_address = info["mailing_address"]
    except Exception:
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

    for listing in listings:
        lead = build_lead_from_case(session, listing)
        if lead is not None:
            new_leads.append(lead)
        else:
            case_detail_failures += 1

    for lead in new_leads:
        existing[lead.key()] = lead.to_dict()
        existing[lead.key()]["key"] = lead.key()

    all_leads = [LeadRecord(**{k: v for k, v in item.items() if k != "key"}) for item in existing.values()]
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
        **search_debug_fields,
    )

    _save_leads(existing)
    os.makedirs(DATA_DIR, exist_ok=True)
    export_to_ghl_csv(all_leads, GHL_CSV_PATH)
    os.makedirs(SHARED_DASHBOARD_DIR, exist_ok=True)
    export_to_ghl_csv(all_leads, DASHBOARD_CSV_PATH)
    _write_dashboard_data(all_leads)

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
