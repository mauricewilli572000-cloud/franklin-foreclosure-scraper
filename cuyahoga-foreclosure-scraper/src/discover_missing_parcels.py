"""
One-off diagnostic: 4 of the first 5 real leads from the nightly run got
no match at all against Open_Data_Parcels (not an error - the query ran
fine, just zero features back), while the 5th (106-18-016) matched
cleanly. Query each of the 4 missing ones a few different ways to find
out why: exact match on the cleaned parcelpin, a LIKE wildcard in case
of a leading-zero/format mismatch, and the property-address fallback.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_missing_parcels.py

Writes discover_missing_parcels_output.json - upload that back.
"""

import json
import sys

import requests

import auditor_enrichment as ae

CASES = [
    {"case": "CV-26-148160", "parcel": "581-16-313", "address": "146 KIMROSE LANE", "city": "BROADVIEW HEIGHTS"},
    {"case": "CV-26-148163", "parcel": "373-27-052", "address": "7391 GRANT BLVD", "city": "CLEVELAND"},
    {"case": "CV-26-148169", "parcel": "649-02-067", "address": "1817 SAGAMORE RD", "city": "EUCLID"},
    {"case": "CV-26-148181", "parcel": "814-12-023", "address": "214 JOHN STREET", "city": "BEDFORD"},
]


def query(where_clause, result_count=3):
    url = f"{ae.ARCGIS_BASE}/{ae.LAYER_ID}/query"
    params = {"where": where_clause, "outFields": "*", "f": "json", "resultRecordCount": result_count}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    report = {}
    for c in CASES:
        clean = c["parcel"].replace("-", "").replace(" ", "")
        entry = {"input_parcel": c["parcel"], "cleaned": clean}

        try:
            exact = query(f"parcelpin = '{clean}'")
            entry["exact_match_count"] = len(exact.get("features", []))
        except Exception as exc:
            entry["exact_match_error"] = str(exc)

        try:
            wildcard = query(f"parcelpin LIKE '%{clean}%'")
            entry["wildcard_match_count"] = len(wildcard.get("features", []))
            entry["wildcard_sample"] = [f["attributes"].get("parcelpin") for f in wildcard.get("features", [])]
        except Exception as exc:
            entry["wildcard_match_error"] = str(exc)

        try:
            street_number, street_name = c["address"].split(" ", 1)
            addr = query(f"UPPER(par_addr_all) LIKE UPPER('%{street_number} {street_name}%')")
            entry["address_match_count"] = len(addr.get("features", []))
            entry["address_sample"] = [
                {"parcelpin": f["attributes"].get("parcelpin"), "par_addr_all": f["attributes"].get("par_addr_all")}
                for f in addr.get("features", [])
            ]
        except Exception as exc:
            entry["address_match_error"] = str(exc)

        try:
            city_addr = query(f"UPPER(par_city) = UPPER('{c['city']}')", result_count=1)
            entry["city_has_any_rows"] = len(city_addr.get("features", [])) > 0
        except Exception as exc:
            entry["city_check_error"] = str(exc)

        report[c["case"]] = entry

    print(json.dumps(report, indent=2))
    with open("discover_missing_parcels_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_missing_parcels_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
