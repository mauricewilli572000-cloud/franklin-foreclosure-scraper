"""GoHighLevel-compatible CSV export, shared shape with Franklin scraper."""

import csv
import re
from typing import List
from lead_schema import LeadRecord


_COMPANY_WORDS = {
    "LLC", "INC", "INC.", "CORP", "CORP.", "CO", "CO.", "LTD", "LP", "LLP",
    "TRUST", "TRUSTEE", "TRUSTEES", "BANK", "ASSOCIATION", "ASSN", "PARTNERS",
    "PROPERTIES", "HOLDINGS", "ENTERPRISES", "INVESTMENTS", "ESTATE", "OF",
    "CITY", "COUNTY", "COMPANY", "FUND", "MORTGAGE", "HOMES", "GROUP",
}
# Text after a comma that is a legal tail, not a first name
# (e.g. "SMITH JOHN, ET AL", "DOE JANE, UNKNOWN SPOUSE").
_TAIL_STARTS = ("ET ", "AKA", "FKA", "UNKNOWN", "SUCCESSOR", "TRUSTEE", "A ", "AN ", "DECEASED")


_ET_TAIL_RE = re.compile(r"[,\s]+ET\s*(AL|UX|VIR)\.?$", re.IGNORECASE)
_NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def _split_name(owner_name: str):
    """Return (first, last) for a mail-merge. The county writes people as
    "LAST, FIRST MIDDLE" ("DUDLEY, SANDRA"), so a comma means flip it.
    Businesses and legal names go entirely in Last Name rather than being
    split at the first space; names with no comma keep "FIRST ... LAST"."""
    owner_name = " ".join((owner_name or "").split())
    # Drop trailing legal tails: "ROBERTS ET AL", "X, ET AL.", "X ET UX".
    owner_name = _ET_TAIL_RE.sub("", owner_name).strip(" ,")
    if not owner_name:
        return "", ""

    words = {w.strip(",.").upper() for w in owner_name.split()}
    if words & _COMPANY_WORDS:
        return "", owner_name

    if "," in owner_name:
        last, _, first = owner_name.partition(",")
        last, first = last.strip(), first.strip()
        if first and not first.upper().startswith(_TAIL_STARTS):
            return first, last
        # "SMITH JOHN, UNKNOWN SPOUSE" style: drop the tail and fall through.
        owner_name = last

    parts = owner_name.split()
    if len(parts) == 1:
        return parts[0], ""
    # No comma means "FIRST [MIDDLE] LAST": the last word is the surname
    # (plus a generational suffix like JR/III), everything before it is the
    # first name, so middle names/initials stay out of Last Name.
    n_last = 2 if len(parts) > 2 and parts[-1].strip(".").upper() in _NAME_SUFFIXES else 1
    return " ".join(parts[:-n_last]), " ".join(parts[-n_last:])


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
