"""Feed row -> LeadRecord (pure, offline-testable)."""
import re
from typing import Optional

import config
import parsers
import summit_feed
from enrichment import ParcelIndex, is_absentee
from lead_schema import LeadRecord
from lead_scoring import score_lead

COUNTY = "Summit"


def _mdY(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else ""


def match_address(lead: LeadRecord, parcels: ParcelIndex) -> None:
    """Fill property/mailing from the parcel file by owner name (unique matches only)."""
    if lead.property_address or not parcels:
        return
    _, defendants = summit_feed.split_case_name(lead.case_name)
    owners = parsers.dedupe_names([d for d in defendants if not parsers.is_non_owner(d)])
    parcel = parcels.by_owner(owners) if owners else None
    if parcel:
        lead.property_address = parcel["situs"]
        lead.mailing_address = parcel["mailing"]
        lead.parcel_number = parcel["parcel"]
        if "Address from name match" not in lead.flags:
            lead.flags.append("Address from name match")


def build_lead(row: dict, parcels: Optional[ParcelIndex] = None, categories=None) -> Optional[LeadRecord]:
    cats = [c.lower() for c in (categories or config.FEED_CATEGORIES)]
    if not any(c in row["category"].lower() for c in cats):
        return None
    plaintiff, defendants = summit_feed.split_case_name(row["case_name"])
    owners = parsers.dedupe_names([d for d in defendants if not parsers.is_non_owner(d)])
    key = summit_feed.lead_key(row["case_name"], row["date_added_raw"])
    lead = LeadRecord(
        county=COUNTY, case_number="SUM-" + key[:8].upper(), doc_number="",
        owner_name=" & ".join(owners[:3]), plaintiff=parsers.pretty_name(plaintiff),
        defendant=", ".join(parsers.pretty_name(d) for d in defendants),
        lead_type="Foreclosure", filing_date=_mdY(row["date_added"]),
        source_url=config.FEED_URL, case_name=row["case_name"], review_status=row["review_status"],
    )
    if "pending" in row["review_status"].lower():
        lead.flags.append("Pending clerk review")
    if not owners:
        lead.flags.append("No individual owner named")
    match_address(lead, parcels)
    lead.lead_score = score_lead(lead)
    return lead
