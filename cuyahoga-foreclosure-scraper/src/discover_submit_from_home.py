"""
One-off diagnostic: run the EXACT same search-submit code path the nightly
pipeline uses, from your own network, and dump full detail about the
response.

Why: every GitHub Actions cloud-runner test so far has come back with
submit_postback_raw_head == "0|error|500||" (an explicit ASP.NET AJAX
server-side error) on the search-submit postback specifically, even
though the page load and the foreclosure-type-select postback both work
fine from the same cloud runner. That matches a server erroring out only
for requests that actually execute a search, from cloud/datacenter IP
ranges - but that's unconfirmed; this script gets a clean comparison
point from your home network instead of guessing further.

Usage (PowerShell):
    cd cuyahoga-foreclosure-scraper\\src
    pip install requests beautifulsoup4
    python discover_submit_from_home.py

It prints a full report and also writes discover_submit_output.json next
to this script - upload that file (or paste the printed output) back.
"""

import json
import sys

from cuyahoga_clerk_search import CuyahogaDocketSession


def main():
    report = {}
    try:
        session = CuyahogaDocketSession()

        search_html = session.load_search_page()
        report["search_page_html_length"] = len(search_html)
        report["url_after_load"] = session.current_url

        type_select_html = session.select_foreclosure_search()
        report["type_select_html_length"] = len(type_select_html)

        html, was_async = session.submit_foreclosure_search(
            date_from="10/05/2026", date_to="10/12/2026"
        )
        report["submit_was_async"] = was_async
        report["submit_html_length"] = len(html)
        report["submit_html_head"] = html[:1500]
        report["submit_postback_status"] = session.last_postback_status
        report["submit_postback_delta_keys"] = session.last_postback_delta_keys
        report["submit_postback_raw_head"] = session.last_postback_raw_head
        report["current_url_after_submit"] = session.current_url

        from cuyahoga_clerk_search import parse_results_grid
        listings = parse_results_grid(html)
        report["listings_found"] = len(listings)
        if listings:
            report["first_listing"] = {
                "case_number": listings[0].case_number,
                "defendant": listings[0].defendant,
                "property_address": listings[0].property_address,
            }

    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(report, indent=2))
    with open("discover_submit_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("\nWrote discover_submit_output.json next to this script.")


if __name__ == "__main__":
    sys.exit(main())
