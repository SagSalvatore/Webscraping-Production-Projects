"""Generate brands/<brand>/input.json for the September chicken-QSR cohort.

Follows the proven per-brand shape used for the 27 brands already run (see
brands/popeyes/input.json): one Google Maps query per UAE city, each query
pre-labelled with its area + emirate so process_brand.py can set the emirate
from the QUERY rather than by parsing the address.

Ten queries are split into two groups that run as two simultaneous Apify actor
runs, so wall-clock is the slower group rather than the sum.

KEYWORDS ARE THE POST-SCRAPE FILTER, and they are a substring match
(is_brand -> `keyword in normalized_name`). Two consequences drive the lists
below:

  * spell every variant a Google Maps title might use. A CURLY apostrophe once
    dropped 100% of real McDonald's matches (36 raw hits, 0 survived), so both
    apostrophe forms and the bare form are listed. _normalize_quotes also folds
    curly -> straight, so this is belt and braces.

  * a GENERIC keyword over-collects. 'crispy chicken' will also match
    'Al Baik Crispy Chicken' and 'Crispy Chicken House' - different businesses.
    workdone.md records this as the highest-impact bug of the whole project
    (55% false positives in a sampled batch). Brands flagged review=True below
    get a candidate split for human review instead of shipping blind.

    python make_brand_inputs.py --dry-run
    python make_brand_inputs.py
"""
import argparse
import csv
import json
import sys
from pathlib import Path

APIFY_DIR = Path(__file__).resolve().parent
BRANDS_DIR = APIFY_DIR / "brands"
KEYS_CSV = APIFY_DIR / "keys.csv"
COHORT_DIR = APIFY_DIR / "sept_chicken"
sys.stdout.reconfigure(encoding="utf-8")

# (query city, area label, emirate) - identical to the 27 brands already run
CITIES = [
    ("Dubai",          "Dubai",          "Dubai"),
    ("Abu Dhabi",      "Abu Dhabi",      "Abu Dhabi"),
    ("Al Ain",         "Al Ain",         "Abu Dhabi"),
    ("Sharjah",        "Sharjah",        "Sharjah"),
    ("Ajman",          "Ajman",          "Ajman"),
    ("Ras Al Khaimah", "Ras Al Khaimah", "Ras Al Khaimah"),
    ("Fujairah",       "Fujairah",       "Fujairah"),
    ("Umm Al Quwain",  "Umm Al Quwain",  "Umm Al Quwain"),
    ("Khor Fakkan",    "Khor Fakkan",    "Sharjah"),
    ("Kalba",          "Kalba",          "Sharjah"),
]
GROUP_A = 5          # first five cities in group_a, rest in group_b

# folder, search name, display, output stem, keywords, needs_review
BRANDS = [
    ("texas_chicken", "Texas Chicken", "Texas Chicken UAE", "TEXAS_CHICKEN_UAE",
     # dual-branded: Church's Chicken outside the US, Texas Chicken in the Gulf.
     # Both spellings appear on UAE Google Maps listings.
     ["texas chicken", "church's chicken", "churchs chicken", "churchs texas"],
     False),

    ("wingstop", "Wingstop", "Wingstop UAE", "WINGSTOP_UAE",
     ["wingstop", "wing stop"], False),

    ("nandos", "Nando's", "Nando's UAE", "NANDOS_UAE",
     ["nando's", "nandos", "nando s"], False),

    ("chicking", "Chicking", "Chicking UAE", "CHICKING_UAE",
     ["chicking", "chic king", "chiking"], False),

    ("raising_canes", "Raising Cane's", "Raising Cane's UAE", "RAISING_CANES_UAE",
     ["raising cane's", "raising canes", "raising cane"], False),

    ("nash_hot_chicken", "Nash Hot Chicken", "Nash Hot Chicken UAE",
     "NASH_HOT_CHICKEN_UAE",
     # 'nash' alone would hit unrelated venues; keep the qualifier
     ["nash hot chicken", "nashville hot chicken by nash"], False),

    ("crispy_chicken", "Crispy Chicken", "Crispy Chicken UAE", "CRISPY_CHICKEN_UAE",
     # GENERIC - see module docstring. Every match goes to review.
     ["crispy chicken"], True),

    ("daves_hot_chicken", "Dave's Hot Chicken", "Dave's Hot Chicken UAE",
     "DAVES_HOT_CHICKEN_UAE",
     ["dave's hot chicken", "daves hot chicken", "dave s hot chicken"], False),

    ("malak_al_tawouk", "Malak Al Tawouk", "Malak Al Tawouk UAE",
     "MALAK_AL_TAWOUK_UAE",
     # transliterated Arabic - several spellings in the wild
     ["malak al tawouk", "malak altawouk", "malak el tawouk",
      "malak al tawook", "malak tawouk", "malek al tawouk"], False),

    ("hardees", "Hardee's", "Hardee's UAE", "HARDEES_UAE",
     ["hardee's", "hardees", "hardee s"], False),
]


def build(folder, search_name, display, out_stem, keywords, review):
    qmeta = [{"query": f"{search_name} in {city}, UAE",
              "area": area, "emirate": emirate}
             for city, area, emirate in CITIES]
    queries = [q["query"] for q in qmeta]
    return {
        "brand_name": search_name,
        "brand_display": display,
        "brand_keywords": keywords,
        "output_filename": out_stem,
        "needs_manual_review": review,
        "searchMatching": "all",
        "maxCrawledPlacesPerSearch": 100,
        "language": "en",
        "countryCode": "ae",
        "maxReviews": 0,
        "maxImages": 0,
        "includeOpeningHours": False,
        "scrapeDirectories": False,
        "additionalInfo": False,
        "proxyConfig": {"useApifyProxy": True,
                        "apifyProxyGroups": ["RESIDENTIAL"]},
        "_comment_query_metadata": "Lookup table: search query -> area + emirate.",
        "query_metadata": qmeta,
        "_comment_groups": "2 groups run in parallel per brand (5 city queries each).",
        "groups": {"group_a": queries[:GROUP_A], "group_b": queries[GROUP_A:]},
    }


def assign_keys(n):
    """One team key per brand, least-used first.

    Mirrors mnc_apify/key_assignment.json: each brand runs under a separate
    free-tier Apify account so the brands execute as simultaneous runs rather
    than queueing behind one account's concurrency limit. Free-tier keys bill
    at $0.004/place (vs $0.003 on the main BRONZE account) but come from the
    team's $100/month of free credit rather than real balance.

    Ordered by remaining credit so repeated cohorts spread load instead of
    always hammering the same first few accounts.
    """
    keys = [r["Name"].strip()
            for r in csv.DictReader(open(KEYS_CSV, encoding="utf-8-sig"))
            if r.get("Name", "").strip()]
    status_path = APIFY_DIR / "output" / "team_keys_status.json"
    if status_path.exists():
        try:
            st = json.loads(status_path.read_text(encoding="utf-8"))
            rows = st if isinstance(st, list) else st.get("keys", [])
            rem = {r.get("name", "").strip(): float(r.get("remaining", 0) or 0)
                   for r in rows if isinstance(r, dict)}
            if rem:
                keys.sort(key=lambda k: -rem.get(k, 0))
        except Exception:
            pass                      # ordering is a nicety, not a requirement
    if len(keys) < n:
        raise SystemExit(f"only {len(keys)} keys for {n} brands")
    return keys[:n]


def main(args):
    print("=" * 74)
    print("  GENERATE BRAND INPUTS  (September chicken-QSR cohort)")
    print("=" * 74)
    print(f"  {'folder':22}{'queries':>8}{'keywords':>10}  review")
    print("  " + "-" * 52)
    wrote = 0
    for folder, name, display, stem, kws, review in BRANDS:
        cfg = build(folder, name, display, stem, kws, review)
        n = len(cfg["query_metadata"])
        print(f"  {folder:22}{n:>8}{len(kws):>10}  {'YES' if review else ''}")
        if args.dry_run:
            continue
        d = BRANDS_DIR / folder
        if (d / "input.json").exists() and not args.force:
            print(f"      exists, skipped (use --force to overwrite)")
            continue
        d.mkdir(parents=True, exist_ok=True)
        (d / "input.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        wrote += 1

    # key assignment, same convention as mnc_apify/key_assignment.json
    keys = assign_keys(len(BRANDS))
    assignment = {b[0]: k for b, k in zip(BRANDS, keys)}
    print(f"\n  {'brand':22}{'apify key (free tier)'}")
    print("  " + "-" * 48)
    for b, k in assignment.items():
        print(f"  {b:22}{k}")
    if not args.dry_run:
        COHORT_DIR.mkdir(parents=True, exist_ok=True)
        (COHORT_DIR / "key_assignment.json").write_text(
            json.dumps(assignment, indent=2, ensure_ascii=False),
            encoding="utf-8")

    total_q = len(BRANDS) * len(CITIES)
    print(f"\n  brands {len(BRANDS)} | total queries {total_q}")
    print(f"  worst-case places = {total_q} x 100 = {total_q*100:,}"
          f"  ->  ${total_q*100*0.003:,.2f} at $0.003/place")
    print("  (realistic is far lower - Popeyes returned 77 places for 10 queries,"
          " $0.31)")
    if args.dry_run:
        print("\n  --dry-run: nothing written")
    else:
        print(f"\n  wrote {wrote} input.json files under brands/")
        print("  next:  python scripts/run_brand_scraper.py --brand <folder> --dry-run")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    sys.exit(main(p.parse_args()))
