"""0-100 lead score for Summit feed leads (same idea as the Franklin/Cuyahoga heuristics).
The feed carries no amount/rate, so freshness and contactability drive the score."""
from datetime import date, datetime

from lead_schema import LeadRecord


def _age_days(filing_date: str):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return (date.today() - datetime.strptime(filing_date, fmt).date()).days
        except ValueError:
            pass
    return None


def score_lead(lead: LeadRecord) -> int:
    score = 50
    age = _age_days(lead.filing_date)
    if age is not None:
        if age <= 3:
            score += 15
        elif age <= 14:
            score += 8
        elif age <= 45:
            score += 3
    if lead.property_address:
        score += 12
    if lead.mailing_address:
        score += 8
    if lead.amount_owed:
        score += 10 if lead.amount_owed < 150000 else 5
    if lead.interest_rate and lead.interest_rate >= 6.0:
        score += 5
    if "Tax lien" in lead.flags or "Judgment lien" in lead.flags:
        score += 4
    if not lead.defendant:
        score -= 10
    return max(0, min(100, score))
