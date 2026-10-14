"""
One-off diagnostic: test auditor_enrichment.get_parcel_info() against
real parcel numbers from an actual completed pipeline run (data/leads.json),
rather than trusting the module's own "CONFIRMED" docstring at face value -
we just found two real bugs in cuyahoga_clerk_search.py sitting under a
docstring that said "Nothing here is a guess," so this gets an independent
live check instead of taking the auditor_enrichment.py docstring's word
for it.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_mailing_address.py

Prints the result for each parcel in data/leads.json (or a hardcoded
fallback list if that file isn't present/empty), and writes
discover_mailing_address_output.json next to this script.
"""

import json
import os
import sys

import auditor_enrichment

FALLBACK_PARCELS = [
    "106-18-016",
    "581-16-313",
    "373-27-052",
    "649-02-067",
    "814-12-023",
]


def main():
    leads_path = os.path.join("..", "data", "leads.json")
    parcels = []
    if os.path.exists(leads_path):
        try:
            with open(leads_path, "r", encoding="utf-8") as f:
                leads = json.load(f)
            parcels = [l.get("parcel_number") for l in leads if l.get("parcel_number")]
        except Exception:
            pass
    if not parcels:
        parcels = FALLBACK_PARCELS

    report = {"parcels_tested": parcels, "results": {}}
    for parcel in parcels:
        try:
            info = auditor_enrichment.get_parcel_info(parcel)
            report["results"][parcel] = info or {"EMPTY": True}
        except Exception as exc:
            report["results"][parcel] = {"error": f"{type(exc).__name__}: {exc}"}

    # Also try one address-based lookup, using the first lead's address if we have it.
    if os.path.exists(leads_path):
        try:
            with open(leads_path, "r", encoding="utf-8") as f:
                leads = json.load(f)
            if leads:
                addr = leads[0].get("property_address", "")
                parts = addr.split(" ", 1)
                street_number = parts[0] if parts else ""
                street_name = parts[1] if len(parts) > 1 else ""
                by_addr = auditor_enrichment.get_parcel_info_by_address(street_number, street_name)
                report["address_lookup_test"] = {
                    "input": {"street_number": street_number, "street_name": street_name},
                    "result": by_addr or {"EMPTY": True},
                }
        except Exception as exc:
            report["address_lookup_test"] = {"error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(report, indent=2))
    with open("discover_mailing_address_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_mailing_address_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
