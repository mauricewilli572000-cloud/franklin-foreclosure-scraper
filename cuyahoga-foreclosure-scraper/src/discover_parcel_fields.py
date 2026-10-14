"""
One-off diagnostic: find the real ArcGIS layer id and field names for
Cuyahoga's parcel data, so auditor_enrichment.py's LAYER_ID/FIELD_MAP
(currently placeholder guesses - PARCELPIN, OWNER, MAILADDR, etc.) can be
corrected to whatever the live service actually calls these fields.
Until this is confirmed, every lead's mailing_address stays blank.

The original ARCGIS_BASE guess ("/arcgis/rest/services/...") was a 404.
A web search (this sandbox has no direct network path to
gis.cuyahogacounty.gov to confirm further) turned up several candidate
services at "/server/rest/services/..." instead - this checks all of
them in one pass rather than guessing one at a time:
  - MyPlace/MyPLACE_Tax_Parcels  (what auditor_enrichment.py now points
    at - name matches the original guess closest)
  - Open_Data_Parcels            (public open-data services often have
    the cleanest, best-documented field names)
  - Combined_Parcels

Uses a real parcel number pulled from the first live Cuyahoga nightly
run (106-18-016, 7705 Melrose Avenue, Cleveland - case CV-26-148146) as
the sample to query.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_parcel_fields.py

Prints every candidate service's layers' real field names + sample
values, and writes the full result to
discover_parcel_fields_output.json - upload that back.
"""

import json
import sys

import auditor_enrichment
from auditor_enrichment import discover_fields

SAMPLE_PARCEL_NUMBER = "106-18-016"

CANDIDATE_SERVICES = {
    "MyPlace_Tax_Parcels": "https://gis.cuyahogacounty.gov/server/rest/services/MyPlace/MyPLACE_Tax_Parcels/MapServer",
    "Open_Data_Parcels": "https://gis.cuyahogacounty.gov/server/rest/services/Open_Data_Parcels/MapServer",
    "Combined_Parcels": "https://gis.cuyahogacounty.gov/server/rest/services/Combined_Parcels/MapServer",
}


def main():
    all_results = {}
    original_base = auditor_enrichment.ARCGIS_BASE
    try:
        for name, base_url in CANDIDATE_SERVICES.items():
            auditor_enrichment.ARCGIS_BASE = base_url
            try:
                all_results[name] = discover_fields(SAMPLE_PARCEL_NUMBER)
            except Exception as exc:
                all_results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        auditor_enrichment.ARCGIS_BASE = original_base

    print(json.dumps(all_results, indent=2)[:8000])
    with open("discover_parcel_fields_output.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print("\nWrote discover_parcel_fields_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
