"""
One-off diagnostic: test the two most promising countywide-sounding
parcel services found via discover_all_services.py against real
parcel numbers that FAILED against Open_Data_Parcels (confirmed
Cleveland-only: a clean parcel-prefix split, <=143 matches, >=204
doesn't, zero overlap, across a real 93-lead batch).

Candidates, in order of promise:
  - CCFO/Current_Year_Appraisal_Parcels - CCFO is the County Fiscal
    Officer (the actual assessor's office); "appraisal parcels" records
    always carry owner-of-record + mailing address for tax billing.
  - CCGIS/Parcel_RP_CAMA_Table - "CAMA" (Computer-Assisted Mass
    Appraisal) is the standard term for an assessor's owner/valuation
    database; being a plain table (not a spatial layer) suggests it's
    lean and field-rich rather than geometry-focused.
  - MyPlace/MyPLACE_Consolidated - untested sibling of the already-ruled-
    out MyPLACE_Tax_Parcels (which had no owner/mailing fields at all),
    worth a quick check since "Consolidated" might combine more.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_countywide_parcels.py

Writes discover_countywide_parcels_output.json - upload that back.
"""

import json
import sys

import requests

SERVER = "https://gis.cuyahogacounty.gov/server/rest/services"

CANDIDATES = {
    "CCFO_Current_Year_Appraisal_Parcels": f"{SERVER}/CCFO/Current_Year_Appraisal_Parcels/MapServer",
    "CCGIS_Parcel_RP_CAMA_Table": f"{SERVER}/CCGIS/Parcel_RP_CAMA_Table/MapServer",
    "MyPlace_Consolidated": f"{SERVER}/MyPlace/MyPLACE_Consolidated/MapServer",
}

# Real parcels that got ZERO match against Open_Data_Parcels (suburbs -
# Broadview Heights, Cleveland-but-still-missed, Euclid, Bedford), plus
# one that DID match (Cleveland), as a sanity control.
TEST_PARCELS = {
    "miss_broadview_heights": "581-16-313",
    "miss_euclid": "649-02-067",
    "miss_bedford": "814-12-023",
    "control_cleveland_hit": "106-18-016",
}


def list_layers(base_url):
    resp = requests.get(f"{base_url}?f=json", timeout=30)
    resp.raise_for_status()
    return resp.json()


def query_layer(base_url, layer_id, where, result_count=3):
    url = f"{base_url}/{layer_id}/query"
    params = {"where": where, "outFields": "*", "f": "json", "resultRecordCount": result_count}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def guess_parcel_field(field_names):
    """Best-effort guess at which field holds the parcel number, to build
    a where clause without knowing the exact name up front."""
    candidates = ["PARCELPIN", "PARCEL_PIN", "PARCELNO", "PARCEL_NO", "PPN",
                  "PARCELNUMBER", "PARCEL_NUMBER", "TAXPIN", "PIN"]
    upper_names = {n.upper(): n for n in field_names}
    for c in candidates:
        if c in upper_names:
            return upper_names[c]
    # fallback: anything containing PARCEL
    for n in field_names:
        if "PARCEL" in n.upper():
            return n
    return None


def main():
    report = {}
    for label, base_url in CANDIDATES.items():
        entry = {"base_url": base_url}
        try:
            service_info = list_layers(base_url)
            if "error" in service_info:
                entry["service_error"] = service_info["error"]
                report[label] = entry
                continue
            layers = service_info.get("layers", [])
            entry["layers"] = [{"id": l.get("id"), "name": l.get("name")} for l in layers]

            layer_tests = {}
            for layer in layers:
                layer_id = layer.get("id")
                layer_name = layer.get("name")
                layer_entry = {"name": layer_name}
                try:
                    sample = query_layer(base_url, layer_id, "1=1", result_count=2)
                    if "error" in sample:
                        layer_entry["error"] = sample["error"]
                        layer_tests[layer_id] = layer_entry
                        continue
                    field_names = [f.get("name") for f in sample.get("fields", [])]
                    layer_entry["field_names"] = field_names
                    layer_entry["sample_attributes"] = [
                        f.get("attributes") for f in sample.get("features", [])
                    ]

                    parcel_field = guess_parcel_field(field_names)
                    layer_entry["guessed_parcel_field"] = parcel_field
                    if parcel_field:
                        parcel_lookups = {}
                        for test_label, parcel in TEST_PARCELS.items():
                            clean = parcel.replace("-", "").replace(" ", "")
                            try:
                                res = query_layer(base_url, layer_id, f"{parcel_field} = '{clean}'", result_count=2)
                                feats = res.get("features", [])
                                parcel_lookups[test_label] = {
                                    "parcel": parcel,
                                    "match_count": len(feats),
                                    "sample": [f.get("attributes") for f in feats[:1]],
                                }
                            except Exception as exc:
                                parcel_lookups[test_label] = {"error": f"{type(exc).__name__}: {exc}"}
                        layer_entry["parcel_lookups"] = parcel_lookups
                except Exception as exc:
                    layer_entry["error"] = f"{type(exc).__name__}: {exc}"
                layer_tests[layer_id] = layer_entry

            entry["layer_tests"] = layer_tests
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report[label] = entry

    print(json.dumps(report, indent=2)[:10000])
    with open("discover_countywide_parcels_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_countywide_parcels_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
