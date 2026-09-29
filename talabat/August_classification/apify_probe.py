"""50-CID Apify probe - measure how much phone/website Google actually holds.

THE QUESTION THIS ANSWERS:
Serper /maps returned website on only 43% of locations. That is either
(a) Serper truncating, or (b) small UAE restaurants genuinely having no website
on their listing. Those imply opposite decisions - a full Apify run is worth
~$15 if (a), and worthless if (b). Rather than guess, probe 50 and measure.

Two enrichment recommendations have already been made on unmeasured
assumptions this month (Serper /places "free enrichment", then a direct CID
fetch). Both were wrong. This one gets measured first.

FEEDS CIDs AS startUrls, not search terms. We already know exactly which
listing we want - we hold the CID - so searching by name again would re-run the
matching problem and risk a different business. A CID is unambiguous.

COST CONTROLS (learned the expensive way on the dairy project):
  * NO actor-side filters. compass/crawler-google-places bills a
    "filter-applied" add-on of $0.001/place PER FILTER on the FREE tier, +25%
    over the $0.004 base. Everything filterable is filtered locally instead.
  * maxReviews/maxImages/maxQuestions = 0. Reviews are the expensive part.
  * The dataset is downloaded immediately: FREE-tier datasets are deleted after
    7 days and a completed run whose dataset expired is spend with nothing
    to show for it.

    python apify_probe.py --dry-run      plan + cost, spends nothing
    python apify_probe.py --confirm      runs it (~$0.20)
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ROOT = HERE.parent.parent
KEYS_XLSX = ROOT / "dairy_uae" / "apify keys.xlsx"
CREDITS = ROOT / "dairy_uae" / "data" / "apify_credits.json"

API = "https://api.apify.com/v2"
ACTOR = "compass~crawler-google-places"
COST_PER_PLACE = 0.004
OUT = DATA / "apify_probe_results.json"
N_PROBE = 50

sys.stdout.reconfigure(encoding="utf-8")


def load_key():
    """Pick the account with the most remaining credit.

    Reuses dairy_uae/discovery/key_pool.py rather than re-reading the files:
    real tokens live in `apify keys.xlsx`, while apify_credits.json holds only
    `token_masked`, and key_pool already joins the two correctly.
    """
    sys.path.insert(0, str(ROOT / "dairy_uae"))
    try:
        from discovery.key_pool import KeyPool
    except ImportError as exc:
        print(f"  cannot import key_pool: {exc}")
        return None, None
    pool = KeyPool.load()
    keys = getattr(pool, "keys", None) or list(pool)
    best = None
    for k in keys:
        rem = getattr(k, "remaining_usd", None)
        tok = getattr(k, "token", None)
        if tok and rem and (best is None or rem > best[1]):
            best = (tok, rem, getattr(k, "name", "?"))
    return (best[0], best) if best else (None, None)


def targets():
    """Restaurants with a maps_url but no website - the actual open question."""
    new = [json.loads(l) for l in open(DATA / "google_maps_details.jsonl",
                                       encoding="utf-8") if l.strip()]
    # one location per brand, preferring those missing a website
    seen, out = set(), []
    for e in new:
        if not e.get("cid") or e["brand"] in seen:
            continue
        if e.get("website"):
            continue                      # already known - nothing to learn
        seen.add(e["brand"])
        out.append(e)
    return out


def main(args):
    tg = targets()
    print("=" * 68)
    print("  APIFY PROBE - does Google hold the websites Serper omitted?")
    print("=" * 68)
    print(f"  locations with a CID but NO website from Serper : {len(tg):,}")
    probe = tg[:N_PROBE]
    cost = len(probe) * COST_PER_PLACE
    print(f"  probing                                        : {len(probe)}")
    print(f"  estimated cost                                 : ${cost:.2f}")

    tok, info = load_key()
    if not tok:
        print("  NO USABLE KEY")
        return 1
    print(f"  key: {info[2]}  remaining ${info[1]:.2f}")

    urls = [{"url": e["google_maps_url"]} for e in probe]
    payload = {
        "startUrls": urls,
        "language": "en",
        "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
        "includeOpeningHours": True,
        "includePeopleAlsoSearch": False,
        "scrapeDirectories": False,
        "scrapeReviewsPersonalData": False,
        "additionalInfo": False,
        "includeHistogram": False,
        # NOTE: no skipClosedPlaces / categoryFilterWords / searchMatching /
        # placeMinimumStars - each triggers the +$0.001/place filter add-on.
    }
    if args.dry_run:
        print("\n  --dry-run: nothing spent.")
        print(f"  would POST {len(urls)} startUrls to {ACTOR}")
        print(f"  sample: {urls[0]['url']}")
        return 0
    if not args.confirm:
        print("\n  refusing to spend without --confirm")
        return 1

    with httpx.Client(timeout=120) as c:
        r = c.post(f"{API}/acts/{ACTOR}/runs", params={"token": tok}, json=payload)
        if r.status_code >= 300:
            print(f"  start failed HTTP {r.status_code}: {r.text[:200]}")
            return 1
        run = r.json()["data"]
        rid, ds = run["id"], run["defaultDatasetId"]
        print(f"\n  run {rid} started; polling ...")
        t0 = time.time()
        while True:
            time.sleep(15)
            s = c.get(f"{API}/actor-runs/{rid}", params={"token": tok}).json()["data"]
            st = s["status"]
            print(f"    {st}  {time.time()-t0:.0f}s")
            if st in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if st != "SUCCEEDED":
            print(f"  run ended {st}")
            return 1
        # download immediately - free-tier datasets expire in 7 days
        items = c.get(f"{API}/datasets/{ds}/items",
                      params={"token": tok, "format": "json", "clean": "true"}).json()

    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(items)
    print(f"\n  places returned: {n}")
    if not n:
        print("  nothing came back - check the run log in Apify console")
        return 1
    for fld, key in (("website", "website"), ("phone", "phone"),
                     ("address", "address"), ("openingHours", "openingHours"),
                     ("categoryName", "categoryName")):
        c_ = sum(1 for x in items if x.get(key))
        print(f"    {fld:14} {c_:>3}/{n}  ({c_/n*100:5.1f}%)")
    print(f"\n  >>> THE ANSWER: Serper gave website on 0 of these {len(probe)}.")
    w = sum(1 for x in items if x.get("website"))
    print(f"      Apify found {w} ({w/n*100:.0f}%).")
    if w / max(n, 1) >= 0.4:
        full = 2182 * (w / n)
        print(f"      -> a full run would recover roughly {full:,.0f} websites "
              f"for ~${3800*COST_PER_PLACE:.0f}. WORTH IT.")
    else:
        print(f"      -> most of these genuinely have no website. NOT worth a full run.")
    print(f"\n  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    sys.exit(main(p.parse_args()))
