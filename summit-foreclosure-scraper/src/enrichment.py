"""Auditor parcel-file enrichment: owner, mailing address, parcel id, absentee flag.

Point --parcel-csv at the Summit County Auditor's bulk parcel export (or any CSV).
Column names are auto-detected; see CANDIDATES. Matching key is house number +
street name (directions / suffixes dropped), the approach that took the Columbus
scraper from ~0% to ~63% owner matches.
"""
import csv
import re
from collections import defaultdict
from typing import Dict, Optional, Tuple

CANDIDATES = {
    "owner": ["OWNER", "OWNER1", "OWNER_NAME", "OWNERNAME", "NAME1", "OWN1"],
    "situs": ["SITUS", "SITE_ADDRESS", "SITEADDRESS", "STADDR", "PROPERTY_ADDRESS", "PROP_ADDRESS", "LOCATION", "ADDRESS"],
    "mail1": ["MAILAD1", "MAIL_ADDRESS", "MAILING_ADDRESS", "MAIL1", "OWNER_ADDRESS", "MAILADDR"],
    "mail2": ["MAILAD2", "MAILAD3", "MAILAD4", "MAIL_CITYSTZIP", "MAIL2", "MAILING_CITYSTATEZIP"],
    "parcel": ["PARCEL", "PARCELID", "PARCEL_ID", "PARCEL_NO", "PIN", "PPN", "PARCELNUMBER"],
}
EXTRA = ["LANDUSE", "PCLASS", "HOMSTD", "TRANDT", "PRICE", "YEARBLT"]

DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST"}
SUFFIX = {"ST", "STREET", "AVE", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "LN", "LANE", "CT", "COURT", "BLVD",
          "BOULEVARD", "WAY", "WY", "PL", "PLACE", "CIR", "CIRCLE", "PKWY", "TRL", "TR", "TRAIL", "TER",
          "HWY", "PIKE", "SQ", "BL", "CR", "LOOP", "ALY", "ALLEY"}
ORD = {"FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7",
       "EIGHTH": "8", "NINTH": "9", "TENTH": "10", "ELEVENTH": "11", "TWELFTH": "12", "THIRTEENTH": "13",
       "FOURTEENTH": "14", "FIFTEENTH": "15", "SIXTEENTH": "16", "SEVENTEENTH": "17", "EIGHTEENTH": "18",
       "NINETEENTH": "19", "TWENTIETH": "20"}


MULTI_PREFIX = {"COUNTY", "STATE", "TOWNSHIP", "TWP", "US", "SR", "CR", "ROUTE", "RT", "FORT", "MOUNT", "MT",
                "PORT", "NEW", "OLD", "LAKE", "SAINT", "ST", "VAN", "DE", "LA"}


def addr_key(addr: str) -> Optional[Tuple[str, str]]:
    a = (addr or "").upper().split(",")[0]          # drop ", City, OH zip" when present
    a = re.sub(r"[.#]", " ", a)
    a = re.split(r"\b(APT|UNIT|LOT|STE|SUITE)\b", a)[0]
    m = re.match(r"\s*(\d+)(?:\s*-\s*\d+)?\s+(.*)", a)
    if not m:
        return None
    toks = [ORD.get(t, re.sub(r"(?<=\d)(ST|ND|RD|TH)$", "", t)) for t in m.group(2).split()]
    toks = [t for t in toks if t not in DIRS and t not in SUFFIX]
    if not toks:
        return None
    # Mailing strings may carry an un-delimited city ("88 OAK DR SIDNEY OH 45365"), so the key is the
    # first street word, or two words for multi-word prefixes (COUNTY 25A, FORT LORAMIE ...).
    n = 2 if toks[0] in MULTI_PREFIX and len(toks) > 1 else 1
    return (m.group(1), " ".join(toks[:n]))


def _pick(cols, names):
    up = {c.upper(): c for c in cols}
    return next((up[n] for n in names if n in up), None)


def name_tokens(n: str) -> set:
    return {t for t in re.sub(r"[^A-Z ]", " ", (n or "").upper()).split() if len(t) > 1 and t not in {"AND", "ET", "AL", "TRUSTEE", "TR", "JR", "SR"}}


class ParcelIndex:
    def __init__(self, path: str):
        self.by_addr: Dict[Tuple[str, str], dict] = {}
        self.by_surname = defaultdict(list)
        self.by_pid: Dict[str, dict] = {}
        self.count = 0
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames or []
            c = {k: _pick(cols, v) for k, v in CANDIDATES.items()}
            if not c["situs"] or not c["owner"]:
                raise ValueError(f"Parcel CSV needs owner + situs columns; found {cols[:15]}")
            extras = [x for x in EXTRA if x in cols]
            for row in rd:
                self.count += 1
                k = addr_key(row.get(c["situs"], ""))
                rec = {
                    "owner": (row.get(c["owner"]) or "").strip(),
                    "situs": (row.get(c["situs"]) or "").strip(),
                    "mailing": " ".join(filter(None, [(row.get(c["mail1"]) or "").strip() if c["mail1"] else "",
                                                       (row.get(c["mail2"]) or "").strip() if c["mail2"] else ""])),
                    "parcel": (row.get(c["parcel"]) or "").strip() if c["parcel"] else "",
                    "extra": {x: row.get(x) for x in extras},
                }
                pid = re.sub(r"\D", "", rec["parcel"])
                if pid:
                    self.by_pid.setdefault(pid, rec)
                if not k:
                    continue
                self.by_addr.setdefault(k, rec)
                for tok in name_tokens(rec["owner"]):
                    self.by_surname[tok].append(k)

    def by_parcel_id(self, pid: str) -> Optional[dict]:
        return self.by_pid.get(re.sub(r"\D", "", pid or "")) if pid else None

    def by_address(self, addr: str) -> Optional[dict]:
        k = addr_key(addr)
        return self.by_addr.get(k) if k else None

    def by_owner(self, names) -> Optional[dict]:
        """Find a parcel owned by any of `names` (used for divorce leads that have no address)."""
        for n in names:
            toks = name_tokens(n)
            if len(toks) < 2:
                continue
            cand = None
            for tok in toks:
                keys = set(self.by_surname.get(tok, []))
                cand = keys if cand is None else cand & keys
            if cand and len(cand) == 1:
                return self.by_addr[next(iter(cand))]
        return None


def is_absentee(property_addr: str, mailing: str) -> Optional[bool]:
    p, m = addr_key(property_addr), addr_key(mailing)
    if not p or not (mailing or "").strip():
        return None
    if not m:                 # PO Box / non-street mailing address => owner lives elsewhere
        return True
    return p != m
