"""Word lists + feed settings for the Summit County scraper."""

# ---------------------------------------------------------------- lead rules
# Foreclosure is the primary lead; the rest are secondary.
FORECLOSURE_WORDS = [
    "foreclos", "order of sale", "sheriff sale", "sheriff's sale", "marshal",
    "decree of foreclosure", "lis pendens", "note and mortgage", "in rem",
]
TAX_FORECLOSURE_WORDS = [
    "delinquent tax", "delinquent land", "certificate of judgment",
    "tax foreclosure", "county treasurer", "treasurer of summit",
]
DIVORCE_WORDS = ["divorce", "dissolution", "legal separation"]
PARTITION_WORDS = ["partition", "quiet title"]

# A divorce/dissolution only becomes a lead if a home is attached.
REAL_ESTATE_HINTS = [
    "real estate", "real property", "marital residence", "marital home",
    "marital property", "residence", "parcel", "deed", "quit claim", "quitclaim",
    "legal description", "sale of the property", "sell the home", "sell the house",
    "mortgage", "refinance", "survey",
]

# Defendants that are not the homeowner.
NON_OWNER_PARTY_WORDS = [
    "unknown spouse", "unknown heirs", "john doe", "jane doe", "occupant",
    "treasurer", "state of ohio", "tax commissioner", "department of",
    "bank", "mortgage", "mers", "credit union", "financial", "savings",
    "llc", "inc", "corp", "n.a.", "trust company", "servicing", "lending",
    "county of", "auditor", "recorder", "united states", "secretary of",
    "housing", "association", "homeowners", "agency", "authority", "township", "village of", "city of",
    "fannie", "freddie", "capital one", "discover", "midland",
]

# Court case-number pattern (e.g. 24CV000123, 23DR000045). Adjust after discovery.
CASE_NUMBER_RE = r"\b\d{2}\s?[A-Z]{2}\s?\d{3,7}\b"
SEQUENCE_CASE_FORMAT = "{yy}{code}{num:06d}"   # used by --mode sequence
SEQUENCE_CODES = {"foreclosure": "CV", "divorce": "DR"}

# Sheriff-sale / default signals found in dockets (boost score).
SALE_SIGNALS = ["order of sale", "appraisal", "sheriff sale", "writ of execution",
                "confirmation of sale", "praecipe for order of sale"]
DEFAULT_SIGNALS = ["default judgment", "motion for default", "default"]

# ---- Summit: new civil filings feed (Clerk of Courts)
FEED_URL = "https://newcivilfilings.summitoh.net/"
FEED_CATEGORIES = ["foreclosure"]          # categories kept as leads (case-insensitive substring)
