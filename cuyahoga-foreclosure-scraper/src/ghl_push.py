"""
Push new leads to GoHighLevel through an Inbound Webhook workflow trigger.

Setup (in GHL): Automation -> Workflows -> Create -> trigger "Inbound
Webhook" -> copy its URL -> add a "Create/Update Contact" action that maps
the fields below. Then give the URL to the pipeline as the environment
variable GHL_WEBHOOK_URL (a GitHub Actions secret in CI). With no URL set,
this module does nothing.

Each lead is sent once: keys of leads already pushed are kept in
data/ghl_sent.json, and a lead is only recorded as sent after the webhook
answers with a 2xx status. A failure on one lead never stops the others or
the rest of the pipeline.
"""

import json
import os
import time
from typing import Dict, List

import requests

from ghl_export import _split_name
from lead_schema import LeadRecord

SENT_FILENAME = "ghl_sent.json"
REQUEST_DELAY_SECONDS = 0.4  # stay well under GHL's webhook rate limits


def _load_sent(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_sent(path: str, sent: set) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(sent), f, indent=2)


def lead_to_payload(lead: LeadRecord) -> Dict[str, object]:
    first, last = _split_name(lead.owner_name or lead.defendant)
    # A business has no first name; GHL wants one of firstName/companyName.
    company = last if (not first and last) else ""
    return {
        "firstName": first,
        "lastName": "" if company else last,
        "companyName": company,
        "address1": lead.property_address,
        "city": lead.property_city,
        "state": lead.property_state,
        "postalCode": lead.property_zip,
        "county": lead.county,
        "caseNumber": lead.case_number,
        "parcelNumber": lead.parcel_number,
        "leadType": lead.lead_type,
        "filingDate": lead.filing_date,
        "plaintiff": lead.plaintiff,
        "amountOwed": lead.amount_owed if lead.amount_owed is not None else "",
        "interestRate": lead.interest_rate if lead.interest_rate is not None else "",
        "loanType": lead.loan_type,
        "leadScore": lead.lead_score,
        "mailingAddress": lead.mailing_address,
        "sourceUrl": lead.source_url,
        "tags": ["Foreclosure", f"{lead.county} County"],
    }


def push_new_leads(leads: List[LeadRecord], data_dir: str, webhook_url: str = "") -> Dict[str, object]:
    """Send leads not yet pushed. Returns a small stats dict for the debug file."""
    webhook_url = webhook_url or os.environ.get("GHL_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return {"enabled": False}

    sent_path = os.path.join(data_dir, SENT_FILENAME)
    sent = _load_sent(sent_path)
    stats: Dict[str, object] = {
        "enabled": True, "attempted": 0, "sent": 0, "failed": 0,
        "already_sent": 0, "sample_errors": [],
    }

    for lead in leads:
        key = lead.key()
        if key in sent:
            stats["already_sent"] += 1
            continue
        stats["attempted"] += 1
        try:
            resp = requests.post(webhook_url, json=lead_to_payload(lead), timeout=30)
            if 200 <= resp.status_code < 300:
                sent.add(key)
                stats["sent"] += 1
                _save_sent(sent_path, sent)  # persist as we go, in case of a crash
            else:
                stats["failed"] += 1
                if len(stats["sample_errors"]) < 3:
                    stats["sample_errors"].append(f"{lead.case_number}: HTTP {resp.status_code} {resp.text[:120]}")
        except Exception as exc:
            stats["failed"] += 1
            if len(stats["sample_errors"]) < 3:
                stats["sample_errors"].append(f"{lead.case_number}: {type(exc).__name__}: {exc}")
        time.sleep(REQUEST_DELAY_SECONDS)

    return stats
