"""
Cuyahoga County Fiscal Officer parcel lookup, via the live ArcGIS REST
service (unlike Franklin's monthly bulk Auditor CSV, Cuyahoga has no
downloadable bulk file for this - it's a live query service).

STATUS: REMAINING GAP. FIELD_MAP and LAYER_ID below are placeholder
guesses and have never been confirmed against the real service. Run
discover_parcel_fields.py against a real parcel number to find the
actual layer and field names, then update FIELD_MAP/LAYER_ID here.

Until confirmed, every lead's mailing_address stays blank - run_pipeline.py
wraps this entire enrichment step defensively so a failure here never
blocks or crashes a run; it just leaves mailing_address blank for that lead.
"""

from typing import Optional, Dict
import requests

ARCGIS_BASE = (
    "https://gis.cuyahogacounty.gov/arcgis/rest/services/"
    "MyPLACE/MyPLACE_Tax_Parcels/MapServer"
)

# UNCONFIRMED - placeholder guesses, pending discover_parcel_fields.py run.
LAYER_ID = 0
FIELD_MAP = {
    "parcel_number": "PARCELPIN",
    "owner_name": "OWNER",
    "mailing_address": "MAILADDR",
    "mailing_city": "MAILCITY",
    "mailing_state": "MAILSTATE",
    "mailing_zip": "MAILZIP",
}


def discover_fields(sample_parcel_number: str) -> dict:
    """
    Diagnostic helper: query every layer on the MapServer and dump the
    real field names/values for a known parcel number, so FIELD_MAP and
    LAYER_ID above can be corrected once and for all. Intended to be run
    from discover_parcel_fields.py, not from the normal pipeline.
    """
    service_resp = requests.get(f"{ARCGIS_BASE}?f=json", timeout=30)
    service_resp.raise_for_status()
    service_info = service_resp.json()

    results = {"service_layers": service_info.get("layers", []), "layer_results": {}}

    for layer in service_info.get("layers", []):
        layer_id = layer.get("id")
        query_url = f"{ARCGIS_BASE}/{layer_id}/query"
        params = {
            "where": f"1=1",
            "outFields": "*",
            "f": "json",
            "resultRecordCount": 5,
        }
        try:
            resp = requests.get(query_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                results["layer_results"][layer_id] = {"error": data["error"]}
                continue
            features = data.get("features", [])
            results["layer_results"][layer_id] = {
                "field_names": [f.get("name") for f in data.get("fields", [])],
                "sample_attributes": [feat.get("attributes") for feat in features],
            }
        except Exception as exc:
            results["layer_results"][layer_id] = {"error": str(exc)}

    return results


def _query_arcgis(where_clause: str) -> Optional[dict]:
    query_url = f"{ARCGIS_BASE}/{LAYER_ID}/query"
    params = {
        "where": where_clause,
        "outFields": "*",
        "f": "json",
        "resultRecordCount": 1,
    }
    resp = requests.get(query_url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # ArcGIS returns application-level errors as a 200 with an "error" body,
    # not an HTTP error status - handle that explicitly rather than
    # crashing on a malformed/missing "features" key.
    if "error" in data:
        return None

    features = data.get("features", [])
    if not features:
        return None
    return features[0].get("attributes", {})


def get_parcel_info(parcel_number: str) -> Dict[str, str]:
    if not parcel_number:
        return {}
    clean = parcel_number.replace("-", "").replace(" ", "")
    where = f"{FIELD_MAP['parcel_number']} = '{clean}'"
    try:
        attrs = _query_arcgis(where)
    except Exception:
        attrs = None

    if not attrs:
        return {}

    return {
        "owner_name": attrs.get(FIELD_MAP["owner_name"], "") or "",
        "mailing_address": " ".join(filter(None, [
            str(attrs.get(FIELD_MAP["mailing_address"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_city"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_state"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_zip"], "") or ""),
        ])).strip(),
    }


def get_parcel_info_by_address(street_number: str, street_name: str) -> Dict[str, str]:
    if not street_name:
        return {}
    where = f"UPPER(SITEADDR) LIKE UPPER('%{street_number} {street_name}%')"
    try:
        attrs = _query_arcgis(where)
    except Exception:
        attrs = None

    if not attrs:
        return {}

    return {
        "owner_name": attrs.get(FIELD_MAP["owner_name"], "") or "",
        "mailing_address": " ".join(filter(None, [
            str(attrs.get(FIELD_MAP["mailing_address"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_city"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_state"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_zip"], "") or ""),
        ])).strip(),
    }
