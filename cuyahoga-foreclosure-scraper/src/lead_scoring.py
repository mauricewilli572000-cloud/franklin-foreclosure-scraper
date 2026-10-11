"""0-100 lead scoring heuristic, shared shape with the Franklin scraper."""

from typing import List
from lead_schema import LeadRecord


def score_lead(lead: LeadRecord) -> int:
    score = 50

    if lead.mailing_address:
        score += 10
    if lead.amount_owed:
        if lead.amount_owed < 50000:
            score += 15
        elif lead.amount_owed < 150000:
            score += 10
        elif lead.amount_owed < 300000:
            score += 5
    if lead.interest_rate and lead.interest_rate >= 6.0:
        score += 5
    if lead.loan_type.lower() in ("adjustable-rate", "adjustable rate", "balloon"):
        score += 5
    if lead.property_address and lead.property_city:
        score += 5
    if not lead.parcel_number:
        score -= 10
    if not lead.defendant:
        score -= 10

    return max(0, min(100, score))


def apply_scores(leads: List[LeadRecord]) -> List[LeadRecord]:
    for lead in leads:
        lead.lead_score = score_lead(lead)
    return leads
