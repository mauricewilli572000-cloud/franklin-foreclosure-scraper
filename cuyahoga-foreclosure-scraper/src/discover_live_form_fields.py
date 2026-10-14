"""
One-off diagnostic: dump every actual input/select field name currently
present on the live foreclosure search panel, and compare it against the
field names cuyahoga_clerk_search.py has hardcoded.

Why: the search-submit postback is coming back as an explicit ASP.NET
AJAX server error ("0|error|500||") from BOTH a GitHub Actions cloud
runner AND a home network - so it isn't IP-based. That points at
something about the live form itself no longer matching what the code
sends (a renamed/removed/added field, a changed dropdown value, a
different date format expectation, etc.) since this was first confirmed
working.

Usage (PowerShell), from cuyahoga-foreclosure-scraper\\src:
    python discover_live_form_fields.py

Prints every <input>/<select>/<textarea> name+value found in the
foreclosure panel's HTML, flags any hardcoded field name this script
doesn't find on the live page, and writes the raw panel HTML to
discover_live_form.html and the full report to
discover_live_form_fields.json - upload either/both back.
"""

import json
import sys

from bs4 import BeautifulSoup

from cuyahoga_clerk_search import CuyahogaDocketSession

HARDCODED_FIELD_NAMES = {
    "FIELD_PARCEL_NBR": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtParcelNbr",
    "FIELD_CASE_YEAR": "ctl00$SheetContentPlaceHolder$foreclosureSearch$ddlCaseYear",
    "FIELD_CASE_SEQUENCE": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtCaseSequence",
    "FIELD_FILING_TYPE": "ctl00$SheetContentPlaceHolder$foreclosureSearch$ddlFilingType",
    "FIELD_FROM_DATE": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtFromDate",
    "FIELD_TO_DATE": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtToDate",
    "FIELD_ACTIVE_ONLY": "ctl00$SheetContentPlaceHolder$foreclosureSearch$cbStatus",
    "FIELD_EXCLUDE_BR": "ctl00$SheetContentPlaceHolder$foreclosureSearch$cbBRCases",
    "FIELD_STREET_NBR": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtStreetNbr",
    "FIELD_STREET_NAME": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtStreetName",
    "FIELD_CITY": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtCity",
    "FIELD_ZIP": "ctl00$SheetContentPlaceHolder$foreclosureSearch$txtZip",
    "FIELD_BANKRUPTCY_STAY": "ctl00$SheetContentPlaceHolder$foreclosureSearch$cbBankruptcyStay",
    "FIELD_SUBMIT": "ctl00$SheetContentPlaceHolder$foreclosureSearch$btnSubmit",
    "FIELD_CLEAR": "ctl00$SheetContentPlaceHolder$foreclosureSearch$btnClear",
}


def main():
    report = {}
    session = CuyahogaDocketSession()
    search_html = session.load_search_page()
    report["search_page_html_length"] = len(search_html)

    type_select_html = session.select_foreclosure_search()
    report["type_select_html_length"] = len(type_select_html)

    with open("discover_live_form.html", "w", encoding="utf-8") as f:
        f.write(type_select_html)

    soup = BeautifulSoup(type_select_html, "html.parser")
    live_fields = {}
    for tag in soup.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            options = [
                {"value": o.get("value", o.text.strip()), "text": o.text.strip()}
                for o in tag.find_all("option")
            ]
            live_fields[name] = {"tag": "select", "options": options}
        else:
            live_fields[name] = {
                "tag": tag.name,
                "type": tag.get("type", ""),
                "value": tag.get("value", ""),
            }

    report["live_field_names"] = sorted(live_fields.keys())
    report["live_fields_detail"] = live_fields

    missing = {}
    for const_name, field_name in HARDCODED_FIELD_NAMES.items():
        if field_name not in live_fields:
            missing[const_name] = field_name
    report["hardcoded_fields_NOT_found_on_live_page"] = missing

    extra_foreclosure_fields = [
        n for n in live_fields
        if "foreclosureSearch" in n and n not in HARDCODED_FIELD_NAMES.values()
    ]
    report["live_foreclosure_fields_NOT_in_our_code"] = extra_foreclosure_fields

    print(json.dumps(report, indent=2))
    with open("discover_live_form_fields.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_live_form_fields.json and discover_live_form.html next to this script.")


if __name__ == "__main__":
    sys.exit(main())
