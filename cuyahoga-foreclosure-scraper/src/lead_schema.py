"""Shared lead record schema, mirrored from the Franklin County scraper."""

from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class LeadRecord:
    county: str
    case_number: str
    doc_number: str = ""
    owner_name: str = ""
    plaintiff: str = ""
    defendant: str = ""
    property_address: str = ""
    property_city: str = ""
    property_state: str = "OH"
    property_zip: str = ""
    parcel_number: str = ""
    mailing_address: str = ""
    lead_type: str = "Foreclosure"
    filing_date: str = ""
    amount_owed: Optional[float] = None
    interest_rate: Optional[float] = None
    loan_type: str = ""
    lead_score: int = 0
    source_url: str = ""

    def key(self) -> str:
        """Stable de-dup key for merging across runs."""
        return f"{self.county}:{self.case_number}".strip().upper()

    def to_dict(self) -> dict:
        return asdict(self)
