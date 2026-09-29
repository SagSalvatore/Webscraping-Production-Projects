"""
config.py - shared settings for the chain-details pipeline, per region.

The region comes from the CHAIN_REGION environment variable (default "uk", so
every UK command keeps working unchanged):

    CHAIN_REGION=usa python research.py

Each region has its own input list and its own data/ and output/ folders, so
caches and ledgers never mix. Keys come from TRACKER_TAVILY_KEYS_apify.xlsx
(sheets 'tavily' and 'apify', columns S.No / Name / Keys); the OpenAI key from
talabat/.env.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
UK_DIR = HERE.parent
REPO = UK_DIR.parent

KEYS_XLSX = REPO / "TRACKER_TAVILY_KEYS_apify.xlsx"
load_dotenv(REPO / "talabat" / ".env")
OPENAI_API_KEY = os.getenv("OPEN_AI_API", "")

# label        how the region is written in search queries and prompts
# country      the home market an outlet count is scoped to
# home_names   words that mean "inside the home market" (never a foreign country)
# adjacent     territories that alone never make a brand International
# report_*     how a location report states a count for this market
# estimate_col the client's own estimate, compared against in build_output
REGIONS = {
    "uk": {
        "dir": UK_DIR, "input": "chain_uk.csv", "name_col": None,
        "label": "UK", "country": "United Kingdom", "tavily_country": "united kingdom",
        "home_names": {"england", "scotland", "wales", "northern ireland", "great britain", "britain",
                       "uk", "u.k.", "united kingdom"},
        "adjacent": {"Isle of Man", "Jersey", "Guernsey", "Gibraltar"},
        "report_place": "the United Kingdom", "report_regex": r"(?:the )?(?:united kingdom|uk)",
        "hq_style": "city_country", "estimate_col": None, "output_stem": "UK_chain_details",
    },
    "usa": {
        "dir": UK_DIR / "USA", "input": "usa.xlsx", "name_col": "Restaurant Name",
        "label": "US", "country": "United States", "tavily_country": "united states",
        "home_names": {"united states", "usa", "us", "u.s.", "u.s.a.", "america", "united states of america"},
        "adjacent": {"Puerto Rico", "Guam"},
        "report_place": "the United States", "report_regex": r"(?:the )?(?:united states|usa|us|u\.s\.)",
        "hq_style": "city_state", "estimate_col": "Number of Outlets (US)", "output_stem": "USA_chain_details",
        "home_note": "Puerto Rico and Guam are US territories, not foreign countries; Canada and Mexico are foreign.",
        "report_preference": ["scrapehero.com", "xmap.ai"],
        "intl_followup": True,
    },
    # Multi-country regions: the count is summed over `countries`; Outlet Type
    # for Europe is judged against each brand's OWN home (HQ) country (the
    # client file marks single-country chains like Steinecke as Regional);
    # for GCC the client's Outlet Type is a format (QSR / Cafe / Dessert).
    "europe": {
        "dir": UK_DIR / "EUROPE", "input": "europe.xlsx", "name_col": "Restaurant Name",
        "label": "Europe", "country": None, "tavily_country": None,
        "home_names": set(), "adjacent": set(), "home_from_hq": True,
        "outside_phrase": "the brand's home country",
        "home_note": "Count every country other than the brand's home (head-office) country.",
        "countries": ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark",
                      "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy",
                      "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal",
                      "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"],
        "scope_label": "EU-27",
        "hq_style": "city_country", "estimate_col": "Number of Outlets (EU-27)", "output_stem": "EUROPE_chain_details",
        "report_preference": ["scrapehero.com", "xmap.ai"],
    },
    "gcc": {
        "dir": UK_DIR / "GCC", "input": "gcc.xlsx", "name_col": "Restaurant Name",
        "label": "GCC", "country": None, "tavily_country": None,
        "home_names": {"gcc", "gulf"}, "adjacent": set(),
        "outside_phrase": "the GCC (UAE, Saudi Arabia, Kuwait, Qatar, Bahrain, Oman)",
        "home_note": "The GCC is the UAE, Saudi Arabia, Kuwait, Qatar, Bahrain and Oman.",
        "countries": ["United Arab Emirates", "Saudi Arabia", "Kuwait", "Qatar", "Bahrain", "Oman"],
        "scope_label": "GCC", "outlet_type_mode": "format",
        "hq_style": "city_country", "estimate_col": "Number of Outlets (GCC)", "output_stem": "GCC_chain_details",
        "report_preference": ["scrapehero.com", "xmap.ai"],
    },
}

REGION = os.getenv("CHAIN_REGION", "uk").lower()
if REGION not in REGIONS:
    raise SystemExit(f"CHAIN_REGION must be one of {sorted(REGIONS)}, got {REGION!r}")
R = REGIONS[REGION]

REGION_DIR: Path = R["dir"]
INPUT_FILE = REGION_DIR / R["input"]
INPUT_CSV = INPUT_FILE            # older name, still imported by some scripts
DATA_DIR = REGION_DIR / "data"
# key balances are account-level, so one file serves every region
KEY_BALANCES = UK_DIR / "data" / "key_balances.json"
OUTPUT_DIR = REGION_DIR / "output"
# per-brand overrides: the UK file predates regions and lives beside the scripts
OVERRIDES = HERE / "brand_overrides.json" if REGION == "uk" else REGION_DIR / "brand_overrides.json"


def load_input() -> pd.DataFrame:
    """The client's list as given, one row per brand, blank rows dropped."""
    if INPUT_FILE.suffix.lower() == ".csv":
        df = pd.read_csv(INPUT_FILE, encoding="utf-8-sig")
    else:
        df = pd.read_excel(INPUT_FILE)
    col = R["name_col"] or df.columns[0]
    df = df.rename(columns={col: "_name"})
    df["_name"] = df["_name"].astype(str).str.strip()
    return df[df["_name"].ne("") & df["_name"].str.lower().ne("nan")].reset_index(drop=True)


def load_rows() -> list[tuple[int, str]]:
    """[(row_index, brand name)] in the client's order - the stable row key."""
    return list(enumerate(load_input()["_name"]))


def load_keys(pool: str) -> list[tuple[str, str]]:
    """[(name, key)] for one sheet of the tracker, blank rows skipped."""
    df = pd.read_excel(KEYS_XLSX, sheet_name=pool)
    out = []
    for _, row in df.iterrows():
        key = str(row.get("Keys") or "").strip()
        if key and key.lower() != "nan":
            out.append((str(row["Name"]).strip(), key))
    return out
