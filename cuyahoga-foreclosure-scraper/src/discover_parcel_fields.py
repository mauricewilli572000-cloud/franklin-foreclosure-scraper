"""
One-off diagnostic: find the real ArcGIS layer id and field names for the
Cuyahoga Fiscal Officer's parcel service, so auditor_enrichment.py's
LAYER_ID/FIELD_MAP (currently placeholder guesses - PARCELPIN, OWNER,
MAILADDR, etc.) can be corrected to whatever the live service actually
calls these fields. Until this is confirmed, every lead's
mailing_address stays blank.

Uses a real parcel number pulled from the first live Cuyahoga nightly
run (106-18-016, 7705 Melrose Avenue, Cleveland - case CV-26-148146) as
the sample to query.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_parcel_fields.py

Prints every layer's real field names + a sample record's values, and
writes the full result to discover_parcel_fields_output.json - upload
that back.
"""

import json
import sys

from auditor_enrichment import discover_fields

SAMPLE_PARCEL_NUMBER = "106-18-016"


def main():
    try:
        result = discover_fields(SAMPLE_PARCEL_NUMBER)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, indent=2)[:8000])
    with open("discover_parcel_fields_output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print("\nWrote discover_parcel_fields_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
