"""GoHighLevel-compatible CSV export, shared shape with Franklin scraper."""

import csv
from typing import List
from lead_schema import LeadRecord


def _split_name(owner_name: str):
    owner_name = (owner_name or "").strip()
    if not owner_name:
        return "", ""
    parts = owner_name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def export_to_ghl_csv(leads: List[LeadRecord], out_path: str) -> str:
    fieldnames = [
        "First Name", "Last Name", "Address", "City", "State", "Postal Code",
        "Case Number", "Parcel Number", "Lead Type", "Filing Date",
        "Amount Owed", "Interest Rate", "Loan Type", "Lead Score",
        "Mailing Address", "Source URL", "County",
    ]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for lead in leads:
            first, last = _split_name(lead.owner_name or lead.defendant)
            writer.writerow({
                "First Name": first,
                "Last Name": last,
                "Address": lead.property_address,
                "City": lead.property_city,
                "State": lead.property_state,
                "Postal Code": lead.property_zip,
                "Case Number": lead.case_number,
                "Parcel Number": lead.parcel_number,
                "Lead Type": lead.lead_type,
                "Filing Date": lead.filing_date,
                "Amount Owed": lead.amount_owed if lead.amount_owed is not None else "",
                "Interest Rate": lead.interest_rate if lead.interest_rate is not None else "",
                "Loan Type": lead.loan_type,
                "Lead Score": lead.lead_score,
                "Mailing Address": lead.mailing_address,
                "Source URL": lead.source_url,
                "County": lead.county,
            })
    return out_path
