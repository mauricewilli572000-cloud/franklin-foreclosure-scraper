import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import lead_builder
import run_pipeline as rp
import summit_feed as feed
from enrichment import ParcelIndex

D = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
HTML = f"""<table><tr><th>Action</th><th>Case Name</th><th>Date Added</th><th>Case Category</th><th>Status</th></tr>
<tr><td><button>Get Docs</button></td><td>WELLS FARGO BANK, N.A. -vs- JOHN Q PUBLIC, ET AL</td><td>{D} 9:14 AM</td><td>Foreclosure</td><td>Pending Review</td></tr>
<tr><td><button>Get Docs</button></td><td>ACME LLC -vs- BOB BUILDER</td><td>{D} 8:01 AM</td><td>Breach of Contract</td><td>Filing Complete</td></tr>
<tr><td><button>Get Docs</button></td><td>MIDFIRST BANK -vs- MARY ROE, UNKNOWN SPOUSE OF MARY ROE</td><td>{D} 7:30 AM</td><td>Foreclosure</td><td>Filing Complete</td></tr></table>"""


def test_rows_to_leads():
    leads = [lead_builder.build_lead(r) for r in feed.parse_rows(HTML)]
    assert [bool(l) for l in leads] == [True, False, True]
    a = leads[0]
    assert a.county == "Summit" and a.lead_type == "Foreclosure" and a.case_number.startswith("SUM-")
    assert a.owner_name == "John Q Public" and a.plaintiff == "Wells Fargo Bank, N.A."
    assert "Pending clerk review" in a.flags and a.lead_score >= 60
    assert len(a.filing_date) == 10 and a.filing_date[2] == "/"        # MM/DD/YYYY like Cuyahoga
    assert leads[2].owner_name == "Mary Roe"


def test_pipeline_writes_dashboard_files(tmp_path, monkeypatch):
    dash, data = tmp_path / "dashboard", tmp_path / "data"
    monkeypatch.setattr(rp, "SHARED_DASHBOARD_DIR", str(dash))
    monkeypatch.setattr(rp, "DATA_DIR", str(data))
    monkeypatch.setattr(rp, "LEADS_JSON_PATH", str(data / "leads.json"))
    monkeypatch.setattr(rp, "GHL_CSV_PATH", str(data / "ghl.csv"))
    monkeypatch.setattr(rp, "DASHBOARD_DATA_PATH", str(dash / "summit.json"))
    monkeypatch.setattr(rp, "DASHBOARD_CSV_PATH", str(dash / "summit_ghl_export.csv"))
    new = [l for l in (lead_builder.build_lead(r) for r in feed.parse_rows(HTML)) if l]
    leads = rp.merge(new, None)
    rp._write_all(leads)
    out = json.loads((dash / "summit.json").read_text())
    assert out["county"] == "Summit" and out["total_leads"] == 2 and out["leads_with_property_address"] == 0
    assert out["leads"][0]["flags"] is not None and "added_date" in out["leads"][0]
    assert (dash / "summit_ghl_export.csv").read_text(encoding="utf-8-sig").startswith("First Name,Last Name")
    # second run: same feed -> no duplicates, added_date kept
    again = rp.merge(new, None)
    assert len(again) == 2


def test_address_added_later_from_parcel_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "LEADS_JSON_PATH", str(tmp_path / "leads.json"))
    new = [l for l in (lead_builder.build_lead(r) for r in feed.parse_rows(HTML)) if l]
    (tmp_path / "leads.json").write_text(json.dumps([{**l.to_dict(), "key": l.key()} for l in new]))
    p = tmp_path / "p.csv"
    p.write_text("PARCEL,OWNER,SITUS,MAILAD1,MAILAD2\n5,PUBLIC JOHN Q,10 MAPLE AVE,10 MAPLE AVE,AKRON OH 44301\n")
    leads = rp.merge([], ParcelIndex(str(p)))
    hit = [l for l in leads if l.property_address]
    assert len(hit) == 1 and hit[0].property_address == "10 MAPLE AVE" and "Address from name match" in hit[0].flags
