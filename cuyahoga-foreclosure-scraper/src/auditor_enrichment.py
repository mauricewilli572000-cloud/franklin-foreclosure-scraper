"""
Cuyahoga County Fiscal Officer parcel lookup, via the live ArcGIS REST
service (unlike Franklin's monthly bulk Auditor CSV, Cuyahoga has no
downloadable bulk file for this - it's a live query service).

STATUS: CONFIRMED against the live service (2026-10-13) via
discover_parcel_fields.py, run against a real parcel number
(106-18-016, from case CV-26-148146) across three candidate services.
Open_Data_Parcels layer 0 is the right one - clean, well-documented
field names, and returns real owner/mailing data (confirmed sample:
parcelpin "12131022" -> deeded_owner "WOODHILL PARTNERS, INC., A",
mail_name "Woodhill Partners Inc", mail_addr_street "2417 WOODHILL RD",
mail_city "Cleveland", mail_state "OH", mail_zip "44104-2401").
MyPlace/MyPLACE_Tax_Parcels (the original guess) turned out to be a
parcel-geometry/filing-history service with no owner or mailing
fields at all - wrong service despite the closest name match.

parcelpin on this service has no hyphens (e.g. "10618016" for
"106-18-016"), matching get_parcel_info()'s existing hyphen-stripping.
"""

from typing import Optional, Dict
import requests

ARCGIS_BASE = (
    "https://gis.cuyahogacounty.gov/server/rest/services/"
    "Open_Data_Parcels/MapServer"
)

LAYER_ID = 0
FIELD_MAP = {
    "parcel_number": "parcelpin",
    "owner_name": "deeded_owner",
    "mail_name": "mail_name",
    "mailing_address": "mail_addr_street",
    "mailing_unit": "mail_unit",
    "mailing_city": "mail_city",
    "mailing_state": "mail_state",
    "mailing_zip": "mail_zip",
    "property_address_all": "par_addr_all",
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


def _attrs_to_result(attrs: dict) -> Dict[str, str]:
    return {
        # deeded_owner is the legal title-of-record name (e.g. "CRAWFORD,
        # MARY ANN"); mail_name is the addressee name as it should appear
        # on an envelope (e.g. "MARY ANN CRAWFORD") - prefer mail_name
        # when present since this feeds a direct-mail workflow, falling
        # back to deeded_owner otherwise.
        "owner_name": (
            attrs.get(FIELD_MAP["mail_name"], "")
            or attrs.get(FIELD_MAP["owner_name"], "")
            or ""
        ),
        "mailing_address": " ".join(filter(None, [
            str(attrs.get(FIELD_MAP["mailing_address"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_unit"], "") or "").strip(),
            str(attrs.get(FIELD_MAP["mailing_city"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_state"], "") or ""),
            str(attrs.get(FIELD_MAP["mailing_zip"], "") or ""),
        ])).strip(),
    }


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
    return _attrs_to_result(attrs)


def get_parcel_info_by_address(street_number: str, street_name: str) -> Dict[str, str]:
    if not street_name:
        return {}
    # par_addr_all is a single combined "STREET, CITY, OH, ZIP" field
    # (confirmed real example: "2417  WOODHILL RD, CLEVELAND, OH, 44104")
    # - there's no standalone site-address field on this service, so
    # match against that instead of a nonexistent SITEADDR column.
    where = (
        f"UPPER({FIELD_MAP['property_address_all']}) LIKE "
        f"UPPER('%{street_number} {street_name}%')"
    )
    try:
        attrs = _query_arcgis(where)
    except Exception:
        attrs = None

    if not attrs:
        return {}
    return _attrs_to_result(attrs)
