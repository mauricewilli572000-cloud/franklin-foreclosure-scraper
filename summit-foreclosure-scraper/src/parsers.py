"""Pure text/HTML parsing — no network. Everything here is unit-tested offline."""
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

import config

# ------------------------------------------------------------------ basics
def clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def normalize_case_number(s: str) -> str:
    m = re.search(config.CASE_NUMBER_RE, (s or "").upper())
    return re.sub(r"\s", "", m.group(0)) if m else clean(s).upper()


def parse_date(s: str) -> str:
    s = clean(s)
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    m = re.search(r"([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})", s)
    if m:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


# --------------------------------------------------------- results table
HEADER_MAP = [
    ("case_number", r"case\s*(no|num|#)|^case$|case number"),
    ("caption", r"style|caption|case title|title|parties|description"),
    ("case_type", r"case\s*type|type"),
    ("filed_date", r"filed|file date|filing|open"),
    ("status", r"status"),
    ("judge", r"judge"),
]


def _map_headers(headers: List[str]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    used = set()
    for i, h in enumerate(headers):
        for key, pat in HEADER_MAP:
            if key not in used and re.search(pat, h.lower()):
                out[i] = key
                used.add(key)
                break
    return out


def parse_results_html(html: str, base_url: str = "") -> List[dict]:
    """Return one dict per result row: case_number, caption, case_type, filed_date, status, detail_url."""
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    rows_out: List[dict] = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if len(trs) < 2:
            continue
        header_cells = trs[0].find_all(["th", "td"])
        headers = [clean(c.get_text(" ")) for c in header_cells]
        hmap = _map_headers(headers)
        for tr in trs[1:]:
            cells = tr.find_all("td")
            if not cells:
                continue
            texts = [clean(c.get_text(" ")) for c in cells]
            rec = {"case_number": "", "caption": "", "case_type": "", "filed_date": "",
                   "status": "", "judge": "", "detail_url": ""}
            for i, t in enumerate(texts):
                key = hmap.get(i)
                if key == "filed_date":
                    rec[key] = parse_date(t)
                elif key:
                    rec[key] = t
            if not rec["case_number"]:
                for t in texts:
                    m = re.search(config.CASE_NUMBER_RE, t.upper())
                    if m:
                        rec["case_number"] = normalize_case_number(m.group(0))
                        break
            else:
                rec["case_number"] = normalize_case_number(rec["case_number"])
            if not rec["case_number"]:
                continue
            a = tr.find("a", href=True)
            if a:
                rec["detail_url"] = urljoin(base_url, a["href"])
                rec["onclick"] = a.get("onclick", "")
            if not rec["filed_date"]:
                for t in texts:
                    d = parse_date(t)
                    if d:
                        rec["filed_date"] = d
                        break
            rows_out.append(rec)
        if rows_out:
            break       # first table that yields case rows wins
    return rows_out


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


# --------------------------------------------------------------- case detail
ROLE_RE = re.compile(
    r"\b(plaintiff|defendant|petitioner|respondent|appellant|appellee|third.party)\b", re.I)


def parse_detail_html(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    text = clean(soup.get_text(" "))
    out = {"case_number": "", "case_type": "", "filed_date": "", "status": "",
           "caption": "", "parties": [], "docket": [], "text": text, "fields": {}}

    # label/value pairs from tables, definition lists and "Label: value" strings
    fields: Dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if len(cells) == 2 and cells[0].endswith(":"):
            fields[cells[0].rstrip(":").lower()] = cells[1]
        elif len(cells) == 2 and len(cells[0]) < 40 and cells[0] and not re.search(r"\d{1,2}/\d{1,2}/\d", cells[0]):
            fields.setdefault(cells[0].lower(), cells[1])
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            fields[clean(dt.get_text()).rstrip(":").lower()] = clean(dd.get_text(" "))
    out["fields"] = fields

    def pick(*names):
        for k, v in fields.items():
            if any(n in k for n in names) and v:
                return v
        return ""

    m = re.search(config.CASE_NUMBER_RE, text.upper())
    out["case_number"] = normalize_case_number(pick("case number", "case no") or (m.group(0) if m else ""))
    out["case_type"] = pick("case type", "type of case", "case category", "type")
    out["status"] = pick("status")
    out["filed_date"] = parse_date(pick("filed", "file date", "date filed", "filing date", "open"))
    out["caption"] = pick("caption", "style", "case title", "title")

    # parties: any table row that carries a role word
    seen = set()
    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        joined = " | ".join(cells)
        rm = ROLE_RE.search(joined)
        if not rm or re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", cells[0] if cells else ""):
            continue
        role = rm.group(1).lower()
        name, addr = "", ""
        for c in cells:
            if ROLE_RE.fullmatch(c.strip().lower()) or c.lower() in ("party type", "role", "name", "address"):
                continue
            if re.search(r"\d+\s+\w+.*(,|\s)\s*(OH|Ohio)\b|\d{5}", c, re.I) and not addr:
                addr = c
            elif not name and len(c) < 120 and not ROLE_RE.fullmatch(c.lower()):
                name = c
        key = (role, name.lower())
        if name and key not in seen:
            seen.add(key)
            out["parties"].append({"role": role, "name": name, "address": addr})

    # docket: rows beginning with a date
    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2 and re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", cells[0]):
            out["docket"].append({"date": parse_date(cells[0]), "text": " ".join(cells[1:])})
    return out


# ----------------------------------------------------------- classification
def _has(text: str, words) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def classify_case(case_type: str, caption: str, docket_text: str = "", case_number: str = "") -> Tuple[str, bool]:
    """Return (lead_type, needs_real_estate_confirmation)."""
    head = f"{case_type} {caption}".lower()
    full = f"{head} {docket_text}".lower()
    if _has(head, config.TAX_FORECLOSURE_WORDS) and _has(full, ["foreclos", "delinquent", "tax"]):
        return "tax_foreclosure", False
    if _has(head, ["foreclos"]) or (_has(full, config.FORECLOSURE_WORDS) and "cv" in case_number.lower()):
        # County Treasurer plaintiffs = tax foreclosure
        if re.search(r"treasurer", caption, re.I):
            return "tax_foreclosure", False
        return "foreclosure", False
    if _has(head, config.DIVORCE_WORDS) or "DR" in case_number.upper():
        return "divorce_real_estate", True
    if _has(head, config.PARTITION_WORDS):
        return "partition", False
    return "", False


def has_real_estate(text: str) -> bool:
    return _has(text, config.REAL_ESTATE_HINTS)


# -------------------------------------------------------------- party names
def is_non_owner(name: str) -> bool:
    n = " " + clean(name).lower() + " "
    if re.match(r"\s*(doe|roe),\s*(jane|john)\b", n) or re.search(r"\b(jane|john|julie|jack|jill) doe\b", n):
        return True
    for w in config.NON_OWNER_PARTY_WORDS:
        if re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", n):
            return True
    return False


def split_caption(caption: str) -> Tuple[str, List[str]]:
    """'BANK OF X vs. JOHN SMITH, ET AL' -> ('BANK OF X', ['JOHN SMITH'])."""
    cap = clean(caption)
    parts = re.split(r"\s+(?:vs\.?|v\.?|versus)\s+", cap, maxsplit=1, flags=re.I)
    if len(parts) < 2:
        return "", []
    plaintiff = re.sub(r",?\s*et\s+al\.?", "", parts[0], flags=re.I).strip(" ,")
    defs = re.sub(r",?\s*et\s+al\.?", "", parts[1], flags=re.I)
    names = [clean(x) for x in re.split(r"\s*(?:;|\band\b|&|,\s*(?=[A-Z][A-Za-z.]+\s+[A-Z]))\s*", defs) if clean(x)]
    return plaintiff, names


SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def _fix_case(n: str) -> str:
    if not n.isupper():
        return n
    n = n.title()
    n = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), n)
    return re.sub(r"\b(Ii|Iii|Iv)\b", lambda m: m.group(1).upper(), n)


def pretty_name(n: str) -> str:
    n = clean(n)
    parts = [x.strip(" .") for x in n.split(",")]
    if len(parts) == 3 and parts[1].upper() in SUFFIXES:          # "PRY, II, WILLIAM JOSEPH"
        n = f"{parts[2]} {parts[0]} {parts[1].upper()}"
        return _fix_case(n)
    if "," in n and n.count(",") == 1 and not re.search(r"\b(jr|sr|ii|iii)\b", n, re.I):
        last, first = [x.strip() for x in n.split(",")]
        if re.fullmatch(r"(N\.?\s?A\.?|INC\.?|LLC|L\.L\.C\.?|CO\.?|CORP\.?|LP|L\.P\.|LTD\.?|F\.?S\.?B\.?|ET AL\.?)", first, re.I):
            return _fix_case(n)                  # company suffix, not a "LAST, FIRST" person
        n = f"{first} {last}"
    return _fix_case(n)


def owners_from(parties: List[dict], caption: str, lead_type: str) -> List[str]:
    names: List[str] = []
    if lead_type == "divorce_real_estate":
        for p in parties:
            if p["role"] in ("plaintiff", "petitioner", "defendant", "respondent") and not is_non_owner(p["name"]):
                names.append(pretty_name(p["name"]))
    else:
        for p in parties:
            if p["role"] in ("defendant", "respondent") and not is_non_owner(p["name"]):
                names.append(pretty_name(p["name"]))
    if not names and caption:
        _, defs = split_caption(caption)
        if lead_type == "divorce_real_estate":
            plaintiff, _ = split_caption(caption)
            defs = ([plaintiff] if plaintiff else []) + defs
        names = [pretty_name(d) for d in defs if not is_non_owner(d)]
    out, seen = [], set()
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def plaintiff_from(parties: List[dict], caption: str) -> str:
    for p in parties:
        if p["role"] in ("plaintiff", "petitioner"):
            return pretty_name(p["name"])
    return pretty_name(split_caption(caption)[0])


# ----------------------------------------------------- amounts / addresses
MONEY_RE = r"\$\s?([\d]{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)"


def extract_amount(text: str) -> Optional[float]:
    for pat in (r"(?:principal balance|unpaid principal|balance of|sum of|amount of|owed|due)[^$]{0,60}" + MONEY_RE,
                r"judgment[^$]{0,40}" + MONEY_RE):
        m = re.search(pat, text, re.I)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def extract_interest(text: str) -> Optional[float]:
    m = re.search(r"(\d{1,2}(?:\.\d{1,4})?)\s?%\s*(?:per annum|interest|per year)?", text, re.I)
    if m:
        v = float(m.group(1))
        if 0 < v < 30:
            return v
    return None


def extract_loan_type(text: str) -> str:
    t = text.lower()
    for label, pats in (("FHA", ["fha", "federal housing"]), ("VA", ["veterans affairs", " va loan"]),
                        ("USDA", ["usda", "rural development"]), ("HELOC", ["home equity line", "heloc"]),
                        ("Reverse", ["reverse mortgage", "home equity conversion"]),
                        ("Conventional", ["conventional"])):
        if any(p in t for p in pats):
            return label
    return ""


ADDR_RE = re.compile(
    r"\b(\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'\- ]{2,40}?\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Blvd|Boulevard|Way|Pl|Place|Cir|Circle|Pkwy|Parkway|Trl|Trail|Ter|Terrace|Hwy|Highway|Pike|Sq|Square)\b\.?"
    r"(?:\s*(?:Apt|Unit|Lot|#)\s*[\w\-]+)?)"
    r"[,\s]+([A-Za-z .'\-]{2,30}?)[,\s]+(?:OH|Ohio)\s+(\d{5})", re.I)


def extract_addresses(text: str) -> List[str]:
    out, seen = [], set()
    for m in ADDR_RE.finditer(text):
        a = f"{clean(m.group(1)).title()}, {clean(m.group(2)).title()}, OH {m.group(3)}"
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


# ------------------------------------------------ CourtView party-grid pages
def parse_party_grid_html(html: str) -> List[dict]:
    """Saved 'Search Results' page: one row per party. Returns dicts with
    party, party_type, case_number, file_date, action, status, court."""
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        head = [clean(c.get_text(" ")) for c in (table.find("tr").find_all(["th", "td"]) if table.find("tr") else [])]
        if "Case Number" not in head or "File Date" not in head:
            continue
        idx = {h: i for i, h in enumerate(head) if h}
        for tr in table.find_all("tr")[1:]:
            c = [clean(x.get_text(" ")) for x in tr.find_all("td")]
            if len(c) != len(head):
                continue
            g = lambda k: c[idx[k]] if k in idx else ""
            case = normalize_case_number(g("Case Number"))
            if not case:
                continue
            out.append({"party": g("Party/Company"), "party_type": g("Party Type"), "case_number": case,
                        "file_date": parse_date(g("File Date")), "action": g("Initiating Action"),
                        "status": g("Case Status"), "court": g("Court")})
    return out


def dedupe_names(names):
    """LANE, PERRY / LANE, PERRY J / LANE, PERRY JOSEPH, or PAT / PATRICK -> one person (longest form)."""
    people = []                                   # [surname, first, display]
    for n in names:
        disp = pretty_name(n)
        toks = [t for t in disp.split() if t.upper().rstrip(".") not in SUFFIXES]
        if len(toks) < 2:
            people.append([disp.lower(), "", disp])
            continue
        sur, first = toks[-1].lower(), toks[0].lower().rstrip(".")
        for p in people:
            f2 = p[1]
            if p[0] == sur and f2 and (first.startswith(f2) or f2.startswith(first)
                                       or (len(first) >= 6 and len(f2) >= 6 and first[:5] == f2[:5])):
                if len(disp) > len(p[2]):
                    p[2] = disp
                break
        else:
            people.append([sur, first, disp])
    return [p[2] for p in people]
