"""Spot-check N random 'genuinely new' brands against Talabat live.

The funnel says 1,182 brands are new to us. That is a claim about OUR coverage,
derived entirely from our own files - so it can only be falsified by going back
to the source. This fetches a random sample and asks, per row:

  1  does the page still exist and serve a restaurant?
  2  does the branch_id on the page match the one we recorded?
  3  does the NAME match what we recorded?
  4  is the brand's name genuinely absent from the July/August exports?

(4) is the one that matters. (1)-(3) catch a broken pipeline; (4) catches the
thing Sagar doubted - that we are re-reporting restaurants we already hold.

?aid= IS LOAD-BEARING. Strip it and Talabat serves a generic homepage with no
ld+json and no restaurant data at all - a "Talabat changed their structure"
conclusion was wrong once for exactly this reason.

    python spot_check.py            20 rows, seeded so it is reproducible
    python spot_check.py -n 40 --seed 7
"""
import argparse
import csv
import json
import os
import random
import re
import sys
from pathlib import Path

import ijson
import orjson
from curl_cffi import requests as cffi
from dotenv import load_dotenv
from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "listing_comparison"))
from common import norm_name                                    # noqa: E402

load_dotenv(ROOT / ".env", override=True)
SEPT = ROOT / "listing_comparison" / "output" / \
    "sept_full_2026-09-08_identity_classification.csv"
EXPORTS = [("july", ROOT / "export" / "talabat_export.json"),
           ("august", ROOT / "August_menu" / "data" / "August_export.json")]
RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
sys.stdout.reconfigure(encoding="utf-8")


def proxy():
    u, p = os.environ["OXYLABS_USERNAME"], os.environ["OXYLABS_PASSWORD"]
    c = os.environ.get("OXYLABS_COUNTRY", "ae")
    return f"http://customer-{u}-cc-{c}:{p}@pr.oxylabs.io:7777"


def fetch(sess, url):
    try:
        r = sess.get(url, timeout=45)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        m = RX.search(r.text)
        if not m:
            return {"error": "no __NEXT_DATA__"}
        d = json.loads(m.group(1))["props"]["pageProps"]
        rest = (d.get("initialMenuState") or {}).get("restaurant") or {}
        return {"name": rest.get("name"), "branch_id": rest.get("branchId"),
                "cuisine": rest.get("cuisineString") or rest.get("cuisine"),
                "area": rest.get("areaName"),
                "items": len((d.get("initialMenuState") or {}).get("menuItems") or [])}
    except Exception as exc:
        return {"error": type(exc).__name__}


def main(args):
    rows = [r for r in csv.DictReader(open(SEPT, encoding="utf-8-sig"))
            if r["category"] == "genuinely_new_brand"]
    random.seed(args.seed)
    sample = random.sample(rows, min(args.n, len(rows)))
    print("=" * 78)
    print(f"  SPOT CHECK  {len(sample)} of {len(rows):,} 'genuinely new' brands")
    print("=" * 78)

    # every brand name we already shipped, for check (4)
    known = set()
    for _, p in EXPORTS:
        with open(p, "rb") as f:
            for rec in ijson.items(f, "item"):
                k = norm_name(rec.get("name"))
                if k:
                    known.add(k)
    print(f"  known brand names in July+August exports : {len(known):,}\n")

    sess = cffi.Session(impersonate="chrome124")
    sess.proxies = {"http": proxy(), "https": proxy()}

    ok = mismatch = gone = already = 0
    for i, r in enumerate(sample, 1):
        live = fetch(sess, r["url"])
        if live.get("error"):
            gone += 1
            print(f"  {i:>2}. {r['name'][:34]:36} FETCH FAILED  {live['error']}")
            continue
        id_ok = str(live.get("branch_id")) == str(r["branch_id"])
        nm_score = fuzz.token_sort_ratio(norm_name(r["name"]),
                                         norm_name(live.get("name")))
        in_known = norm_name(r["name"]) in known
        if in_known:
            already += 1
        flag = "OK" if (id_ok and nm_score >= 85 and not in_known) else "CHECK"
        if flag == "OK":
            ok += 1
        else:
            mismatch += 1
        print(f"  {i:>2}. {r['name'][:34]:36} {flag}")
        print(f"      live name : {str(live.get('name'))[:46]:48} name~{nm_score:.0f}")
        print(f"      branch_id : ours {r['branch_id']} / live "
              f"{live.get('branch_id')}  {'match' if id_ok else 'MISMATCH'}")
        print(f"      cuisine   : {str(live.get('cuisine'))[:46]}")
        print(f"      area      : {str(live.get('area'))[:30]:32}"
              f"menu items {live.get('items')}")
        if in_known:
            print(f"      *** name ALREADY in July/August export ***")

    print("\n" + "=" * 78)
    print(f"  verified new  : {ok}/{len(sample)}")
    print(f"  needs review  : {mismatch}")
    print(f"  fetch failed  : {gone}")
    print(f"  already in an export (would be a real error) : {already}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    sys.exit(main(p.parse_args()))
