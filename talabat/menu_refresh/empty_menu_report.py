"""The branches that returned a menu page with zero items.

All of them HAD a menu in June (the baseline is built only from branches with
June items) and all were shipped to Tech in July. So this is churn detected
this run, not a pre-existing gap.

`empty_menu` means the page parsed and the menu container was present but held
no items - distinct from `no_menu_state` (no menu container at all) and from a
fetch failure. On Talabat that usually means delisted, but it can also mean
temporarily closed, so each one is RE-FETCHED here before we call it churn.

    python empty_menu_report.py            # csv + live re-check
    python empty_menu_report.py --no-recheck
"""
import argparse
import asyncio
import csv
import json
import os
import random
import sys
from pathlib import Path

import psycopg2
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

# Postgres credentials live in talabat/.env and are read by db_config;
# they used to be inlined here. This puts talabat/ on the import path
# no matter which directory the script is launched from.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from db_config import PG_PASSWORD  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ROOT = HERE.parent            # talabat/ - the .env lives here, NOT one above
load_dotenv(ROOT / ".env", override=True)

STATUS = DATA / "scrape" / "restaurant_status.jsonl"
BASELINE = DATA / "baseline_june.json"
OUT_CSV = DATA / "empty_menu_restaurants.csv"

DB = dict(host="localhost", port=5432, dbname="RestaurantIntelligence",
          user="postgres", password=PG_PASSWORD)

U = os.getenv("OXYLABS_USERNAME", "")
P = os.getenv("OXYLABS_PASSWORD", "")
C = os.getenv("OXYLABS_COUNTRY", "ae")

sys.stdout.reconfigure(encoding="utf-8")

if not U or not P:
    # empty credentials build a malformed proxy URL, and every request fails as
    # ProxyError - which reads like a proxy outage, not a config mistake
    sys.exit(f"  OXYLABS creds not loaded from {ROOT / '.env'} - check the path")


def proxy():
    sid = random.randint(10000, 99999)
    return f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}@pr.oxylabs.io:7777"


def parse_count(html):
    tag = BeautifulSoup(html, "html.parser").find("script", {"id": "__NEXT_DATA__"})
    if not tag or not tag.string:
        return None
    try:
        nd = json.loads(tag.string)
        items = nd["props"]["pageProps"]["initialMenuState"]["menuData"]["items"]
        return len(items or [])
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        return None


async def recheck(rows):
    """Second look at each empty branch - a one-off empty response should not
    be reported as a delisting.

    Matches scrape_menus.py exactly: impersonate is set ON THE SESSION (not per
    request), one session reused, and the SAME sessid for http and https so the
    proxy keeps one exit IP for the CONNECT tunnel.
    """
    sem = asyncio.Semaphore(5)

    async def one(sess, r):
        async with sem:
            for attempt in range(3):
                sid = f"{random.randint(10000, 99999)}"
                px = (f"http://customer-{U}-cc-{C}-sessid-{sid}:{P}"
                      f"@pr.oxylabs.io:7777")
                try:
                    resp = await sess.get(
                        r["url"], timeout=60, proxies={"http": px, "https": px},
                        headers={"Accept-Language": "en-US,en;q=0.9"})
                    if resp.status_code == 200:
                        n = parse_count(resp.text)
                        r["recheck_items"] = n
                        r["recheck"] = ("still_empty" if n == 0 else
                                        "HAS_MENU" if n else "no_menu_state")
                        return
                    r["recheck"] = f"http_{resp.status_code}"
                except Exception as e:
                    r["recheck"] = f"error_{type(e).__name__}"
                await asyncio.sleep(2 * (attempt + 1))

    async with AsyncSession(impersonate="chrome124") as sess:
        await asyncio.gather(*(one(sess, r) for r in rows))


def main(args):
    empties = [json.loads(l) for l in open(STATUS, encoding="utf-8")
               if l.strip() and json.loads(l).get("status") != "ok"]
    print(f"  branches with no menu items this run: {len(empties)}")

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    cn = psycopg2.connect(**DB)
    c = cn.cursor()
    c.execute("""select branch_id, restaurant_id, restaurant_name, restaurant_type,
                        outlet_type, chained_outlet_type, area_name, map_url,
                        serves_cuisine, rating, review_count, chain_id
                 from talabat_restaurants""")
    meta = {r[0]: r for r in c.fetchall()}
    cn.close()

    rows = []
    for e in empties:
        bid = e["branch_id"]
        m = meta.get(bid)
        b = base.get(str(bid), {})
        rows.append({
            "branch_id": bid,
            "branch_id_text": f'="{bid}"',       # Excel keeps this as text
            "restaurant_id": m[1] if m else None,
            "name": (m[2] if m else e.get("name")) or "",
            "restaurant_type": m[3] if m else "",
            "outlet_type": m[4] if m else "",
            "chained_outlet_type": m[5] if m else "",
            "area_name": (m[6] if m else None) or e.get("area_name") or "",
            "cuisines": m[8] if m else "",
            "rating": m[9] if m else "",
            "review_count": m[10] if m else "",
            "chain_id": m[11] if m else "",
            "june_item_count": b.get("baseline_item_count", 0),
            "august_item_count": 0,
            "status": e.get("status"),
            "url": e.get("url") or (m[7] if m else ""),
        })

    had = sum(1 for r in rows if r["june_item_count"] > 0)
    print(f"  of those, HAD a June menu: {had}  (so this is churn, not a gap)")
    print(f"  June items now missing   : {sum(r['june_item_count'] for r in rows):,}")

    if not args.no_recheck:
        print(f"\n  re-fetching all {len(rows)} live ...")
        asyncio.run(recheck(rows))
        from collections import Counter
        cc = Counter(r.get("recheck") for r in rows)
        print("  re-check result: " + "  ".join(f"{k}={v}" for k, v in cc.most_common()))
        recovered = [r for r in rows if r.get("recheck") == "HAS_MENU"]
        if recovered:
            print(f"\n  {len(recovered)} were TRANSIENT - they have menus now:")
            for r in recovered[:10]:
                print(f"     {r['branch_id']:>8}  {r['name'][:38]:40} "
                      f"{r['recheck_items']} items")

    cols = ["branch_id", "branch_id_text", "restaurant_id", "name",
            "restaurant_type", "outlet_type", "chained_outlet_type", "area_name",
            "cuisines", "rating", "review_count", "chain_id", "june_item_count",
            "august_item_count", "status", "recheck", "recheck_items", "url"]
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: -x["june_item_count"]):
            w.writerow(r)
    print(f"\n  -> {OUT_CSV.name} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--no-recheck", action="store_true")
    sys.exit(main(p.parse_args()))
