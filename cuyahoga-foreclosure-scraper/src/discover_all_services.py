"""
One-off diagnostic: list every ArcGIS service actually available on
gis.cuyahogacounty.gov's server, instead of guessing service names one
at a time.

Why: auditor_enrichment.py's Open_Data_Parcels layer works, but only for
parcels in a low numeric prefix range (confirmed: every parcel number
with prefix <= 143 in a real 93-lead batch got a mailing address match,
every one with prefix >= 204 got zero matches, no overlap at all) -
that prefix boundary lines up with Cuyahoga's taxing-district numbering,
where low prefixes are the City of Cleveland and higher ones are the
other municipalities in the county. That strongly suggests
Open_Data_Parcels is Cleveland-only, not countywide.

A guessed "Combined_Parcels" MapServer URL came back with an empty
layer list (not an explicit error) - ambiguous: either that name is
simply wrong, or something else is off. Rather than guess again, this
hits the server's own services directory listing, which enumerates the
real names.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_all_services.py

Writes discover_all_services_output.json - upload that back (or just
paste the printed list of names).
"""

import json
import sys

import requests

SERVER_ROOT = "https://gis.cuyahogacounty.gov/server/rest/services"


def list_folder(folder_url):
    resp = requests.get(folder_url, params={"f": "json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    report = {}
    root = list_folder(SERVER_ROOT)
    report["root"] = root

    # The root listing typically has "folders" (subdirectories of
    # services) and "services" (services directly at the root). Recurse
    # one level into each folder too, since county GIS servers commonly
    # organize parcel data under a folder rather than the root.
    report["folder_contents"] = {}
    for folder in root.get("folders", []):
        try:
            folder_url = f"{SERVER_ROOT}/{folder}"
            report["folder_contents"][folder] = list_folder(folder_url)
        except Exception as exc:
            report["folder_contents"][folder] = {"error": f"{type(exc).__name__}: {exc}"}

    # Flatten every (name, type) pair found anywhere, for a quick scan.
    all_services = []
    for svc in root.get("services", []):
        all_services.append(svc)
    for folder, contents in report["folder_contents"].items():
        for svc in contents.get("services", []) if isinstance(contents, dict) else []:
            all_services.append(svc)
    report["all_services_flat"] = all_services

    # Highlight anything with "parcel" in the name - the ones worth
    # drilling into next.
    report["parcel_related_services"] = [
        s for s in all_services if "parcel" in json.dumps(s).lower()
    ]

    print(json.dumps(report, indent=2))
    with open("discover_all_services_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_all_services_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
