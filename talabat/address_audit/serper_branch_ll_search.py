"""Is a branch with no Google address MISSED by our search, or NOT ON Google Maps?

Sagar, Sept 2026: "i want to know the reason first - is serper google map endpoint
would not find, or in real those branch address are not listed in google maps.
If the second is happening then we will put NA".

WHY THE FIRST SEARCH CANNOT ANSWER IT. serper_contact_fill.py sent
"{name} {area} uae" as plain text with no location. Serper returns ~20 places
per query, so for a brand with 60+ UAE branches the one we want is often simply
not in the page: 680 of the 1,388 blanks got back ONLY other branches of the same
brand, all more than 5 km away. That is a search limitation, not evidence that
the branch is unlisted.

THIS SEARCH ASKS THE QUESTION DIRECTLY: the brand name, with the map CENTRED on
the branch's own coordinates (Serper `ll` = "@lat,lng,17z", street level). If
Google Maps lists the branch, it is the nearest result. Verdicts:

    found          same brand within MATCH_M of the branch -> its own address
    not listed     searched AT the branch, and Google shows no same-brand place
                   within MATCH_M -> the branch is not on Google Maps -> "NA"

The acceptance rule is unchanged from the contact repair Sagar approved - same
brand AND within 300 m - with one widening, measured on the cache: a trailing
generic word no longer breaks the name match ('Il Forno Restaurant' vs
'Il Forno, Mushrif Mall'). Distance still has to prove the location.

CACHED FOR GOOD (Sagar: "do cache for this serper run as well so that we wont do
next time expensive operations"). Every response is stored in
data/serper_branch_ll_cache.json under branch_id (branch_id|zoom for other
zooms) with a `fetched_at` timestamp, saved every 200 requests and on exit. A
later run only pays for branches it has never seen; --max-age-days N re-fetches
entries older than N days when a refresh is actually wanted.

    python serper_branch_ll_search.py --dry-run
    python serper_branch_ll_search.py --sample 10     10 per prior reason
    python serper_branch_ll_search.py --all
"""
import argparse
import asyncio
import csv
import math
import os
import random
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import orjson
from dotenv import load_dotenv
from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "August_classification"))
from serper_contact_fill import outside_uae, same_brand          # noqa: E402
from serper_places import Pacer                                  # noqa: E402

load_dotenv(ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")
DATA = HERE / "data"
BLANKS = DATA / "blank_address_branches.csv"
CACHE = DATA / "serper_branch_ll_cache.json"
OUT = DATA / "blank_address_ll_results.csv"
MAPS_URL = "https://google.serper.dev/maps"
MATCH_M = 300.0
ZOOM = "17z"
CONCURRENCY, QPS = 10, 15.0

GENERIC = {"restaurant", "restaurants", "resturant", "cafe", "café", "cafeteria", "llc",
           "l.l.c", "co", "company", "trading", "branch", "est", "the"}
SEP = re.compile(r"\s+-\s+|,|\||\(")


def core(s):
    """Brand core: text before a branch separator, minus trailing generic words."""
    s = SEP.split(str(s or ""))[0]
    toks = re.findall(r"[\w'&]+", s.lower())
    while toks and toks[-1] in GENERIC:
        toks.pop()
    while toks and toks[0] == "the":
        toks.pop(0)
    return re.sub(r"[^a-z0-9]", "", "".join(toks))


def brand_match(name, title):
    if same_brand(name, title):
        return True
    a, b = core(name), core(title)
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return variant_match(name, title)


# Words that carry no brand identity wherever they sit, plus the article and the
# emirate names Google appends to a title ('SARAVANAA BHAVAN ABUDHABI').
DROP = GENERIC | {"and", "al", "el", "of", "dubai", "sharjah", "ajman", "fujairah", "uae", "abudhabi",
                  "cafteria"}
NON_ASCII = re.compile(r"[^\x00-\x7F]+")
# A name made ONLY of these is a description, not a brand: 'Juice Center',
# 'Oriental Dining', 'Yemen Mandi'. The variant rules never accept one.
FOOD_WORDS = set("""
juice juices center centre shawarma shawerma grill grills grilled pizza pizzeria burger burgers biryani
biriyani mandi mandy madhbi kitchen sweets sweet bakery bakers bake coffee tea house corner express food
foods chicken fried broast broasted kabab kebab kababs kebabs noodle noodles sushi bar lounge shop cake
cakes dining oriental cuisine fresh healthy diet meal meals plans plan star royal grand new time spot
point palace garden hut box bowl bowls salad salads shake shakes ice cream falafel falafil karak chai
sandwich sandwiches fish seafood bbq pasta tandoor tandoori dosa chaat pastries pastry patisserie
confectionery chocolate chocolates dessert desserts waffle waffles crepe crepes donut donuts fries wings
taco tacos steak steaks rolls roll wraps wrap platter mix fusion bistro deli market mart grocery butchery
yemen yemeni lebanese lebanon turkish indian india pakistani pakistan syrian iranian persian arabic
arabian emirati egyptian chinese thai filipino korean japanese italian mexican american british german
russian afghan kerala malabar hyderabadi hyd bombay mumbai delhi punjabi nepali nepalese sri lankan
ethiopian african moroccan iraqi jordanian palestinian saudi kuwaiti gulf asian continental
international special original famous best taste tasty delight delights hot spicy golden city world
family first one by for la le
""".split())
# Words a Google title may put BEFORE a brand without being another business:
# the branch or place it names ('AL KARAMA OLD MUMBAI ICECREAM'), or what kind
# of place it is ('Gelateria La Romana'). Place words come from area_list.csv.
LEAD_OK = {"main", "branch", "new", "gelateria", "patisserie", "pizzeria", "trattoria", "dine", "in",
           "delivery"}


def _area_words():
    p = ROOT / "area_classification" / "area_list.csv"
    if not p.exists():
        return set()
    return {t for row in csv.reader(open(p, encoding="utf-8-sig")) for v in row[:1]
            for t in re.findall(r"[a-z0-9]+", v.lower())} - {"al", "and", "the", "of", "city"}


AREA_WORDS = _area_words()


def brand_tokens(s, keep=False):
    """Accents folded ('Ladurée' = 'Laduree'), other scripts removed, text after
    a branch separator cut, '&' = 'and', filler words dropped anywhere
    (keep=True drops only 'and', for a whole-name identity test)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = NON_ASCII.sub(" ", "".join(c for c in s if not unicodedata.combining(c)))
    s = re.sub(r"\babu\s*dhabi\b", " abudhabi ", SEP.split(s)[0].lower())
    drop = {"and"} if keep else DROP
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in drop]


def variant_match(name, title):
    """The same brand spelled another way. Measured on the 642 'not listed'
    verdicts of the wrong-emirate search: ~40 had the branch itself within 60 m
    under a variant the prefix rule rejects - 'Nayaab Handi' / 'Nayaab Haandi',
    'Salt & Spice' / 'Salt and Spice', 'Laduree' / 'Ladurée Marina Mall',
    'Teyyar Cake & Flower' / 'Teyyar Flower & Cake'.

    Never a shared WORD, and never a description: 'The Korean Restaurant' vs
    'Hanok Korean Restaurant', 'Al Khan Restaurant' vs 'Bundoo Khan Restaurant'
    and 'Artisan Bakery' vs "Bloomsbury's Boutique Cafe & Artisan Bakery" stay
    rejected. Every verdict these rules changed was listed and read before use."""
    A, B = brand_tokens(name), brand_tokens(title)
    a, b = "".join(A), "".join(B)
    if not a or not b:
        return False
    if len(a) >= 3 and brand_tokens(name, keep=True) == brand_tokens(title, keep=True):
        return True             # identical once '&'/'and' and accents agree
    (s, S), (l, L) = sorted(((a, A), (b, B)), key=lambda x: len(x[0]))
    if all(t in FOOD_WORDS for t in S):
        return False            # 'Tacos' vs 'Cafteria Tacos'
    if a == b:
        return len(a) >= 3
    # one is the other plus a branch suffix
    if (len(S) >= 2 or len(s) >= 7) and l.startswith(s):
        return True
    # the same words in another order
    if len(S) >= 2 and sorted(A) == sorted(B):
        return True
    # a spelling variant of the WHOLE name (not a part of it)
    if (min(len(A), len(B)) >= 2 or len(s) >= 8) and len(s) >= 6 and fuzz.ratio(a, b) >= 88:
        return True
    # the whole multi-word name inside the other, on word boundaries
    if len(S) >= 2 and len(s) >= 8:
        at = word_run(s, L)
        if at is None:
            return False
        if S is B:
            # OUR name holds Google's whole title: Talabat listings add
            # descriptors ('Bubble Tea Bobaa Mix', 'Daily Meal Plans By The 500 Calorie Project')
            return True
        # Google's title holds our whole name: only a place or branch word may
        # precede it, otherwise it is another business ('Chocolate Royal Sweets')
        return all(t in LEAD_OK or t in AREA_WORDS for t in L[:at])
    return False


def word_run(s, L):
    """Index of the token in L where the joined string s starts AND ends on
    token boundaries, else None ('burgerking' is not in 'burger kingdom')."""
    starts, ends, pos = {}, set(), 0
    for n, t in enumerate(L):
        starts[pos] = n
        pos += len(t)
        ends.add(pos)
    l = "".join(L)
    i = l.find(s)
    while i != -1:
        if i in starts and i + len(s) in ends:
            return starts[i]
        i = l.find(s, i + 1)
    return None


def metres(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def load_cache():
    return orjson.loads(CACHE.read_bytes()) if CACHE.exists() else {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(c))
    tmp.replace(CACHE)


def ckey(b):
    """Cache key: the branch at the default zoom, branch|zoom otherwise, so a
    second pass at another zoom never overwrites the first."""
    return b["branch_id"] if ZOOM == "17z" else f"{b['branch_id']}|{ZOOM}"


MAX_AGE_DAYS = None


def fresh(entry):
    """A cached answer is reused unless it failed or is older than --max-age-days."""
    if not entry or entry.get("failed"):
        return False
    if MAX_AGE_DAYS is None or not entry.get("fetched_at"):
        return True
    age = datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])
    return age.days < MAX_AGE_DAYS


async def fetch(client, b, key, sem, pacer, cache, stats):
    k = ckey(b)
    if fresh(cache.get(k)):
        stats["cached"] += 1
        return
    body = {"q": b["name"], "ll": f"@{b['lat']},{b['lng']},{ZOOM}", "gl": "ae", "hl": "en"}
    for attempt in range(4):
        await pacer.wait()
        async with sem:
            try:
                r = await client.post(MAPS_URL, headers={"X-API-KEY": key,
                                                         "Content-Type": "application/json"},
                                      json=body, timeout=30)
                if r.status_code == 200:
                    d = r.json()
                    cache[k] = {"body": body, "places": d.get("places") or [],
                                "fetched_at": datetime.now(timezone.utc).isoformat()}
                    stats["ok"] += 1
                    return
                if r.status_code in (429, 500, 502, 503):
                    await asyncio.sleep(2 * (2 ** attempt))
                    continue
                stats[f"http_{r.status_code}"] += 1
                break
            except Exception as exc:
                stats[type(exc).__name__] += 1
                await asyncio.sleep(2 * (2 ** attempt))
    cache[k] = {"body": body, "places": [], "failed": True,
                "fetched_at": datetime.now(timezone.utc).isoformat()}
    stats["failed"] += 1


def verdict(b, entry):
    """-> (verdict, place or None, distance_m of nearest same-brand place)."""
    if entry.get("failed"):
        return "request failed", None, None
    best, bestd, nearest_brand = None, None, None
    for p in entry.get("places") or []:
        if p.get("latitude") is None or outside_uae(p.get("latitude"), p.get("longitude")):
            continue
        if not brand_match(b["name"], p.get("title") or ""):
            continue
        d = metres((b["lat"], b["lng"]), (p["latitude"], p["longitude"]))
        nearest_brand = d if nearest_brand is None else min(nearest_brand, d)
        if d <= MATCH_M and (bestd is None or d < bestd):
            best, bestd = p, d
    if best:
        return "found", best, bestd
    if not entry.get("places"):
        return "not listed: Google returned nothing at the branch", None, None
    if nearest_brand is None:
        return "not listed: no same-brand place near the branch", None, None
    return "not listed: brand only elsewhere", None, nearest_brand


async def main(args):
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        raise SystemExit("SERPER_API_KEY missing from talabat/.env")
    global OUT, ZOOM, MAX_AGE_DAYS
    ZOOM = args.zoom
    MAX_AGE_DAYS = args.max_age_days
    if args.out:
        OUT = Path(args.out).resolve()
    blanks = list(csv.DictReader(open(Path(args.input) if args.input else BLANKS, encoding="utf-8-sig")))
    cache = load_cache()
    if args.sample:
        by = defaultdict(list)
        for b in blanks:
            by[b["prior_reason"]].append(b)
        rnd = random.Random(20260917)
        todo = [b for grp in by.values() for b in rnd.sample(grp, min(args.sample, len(grp)))]
    else:
        todo = blanks
    need = sum(1 for b in todo if not fresh(cache.get(ckey(b))))
    print("=" * 78)
    print(f"  LOCATION-CENTRED Google Maps search   {len(todo):,} branches | {need:,} credits")
    print("=" * 78)
    async with httpx.AsyncClient() as c:
        r = await c.get("https://google.serper.dev/account", headers={"X-API-KEY": key}, timeout=20)
        print(f"  serper balance: {r.json().get('balance') if r.status_code == 200 else '?'}")
    if args.dry_run:
        print("  --dry-run: no requests")
        return 0

    stats = Counter()
    sem, pacer = asyncio.Semaphore(CONCURRENCY), Pacer(QPS)
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            tasks = [fetch(client, b, key, sem, pacer, cache, stats) for b in todo]
            for i, f in enumerate(asyncio.as_completed(tasks), 1):
                await f
                if i % 200 == 0:
                    save_cache(cache)
                    print(f"    {i:,}/{len(todo):,} | {i / (time.time() - t0):.1f}/s | {dict(stats)}", flush=True)
    finally:
        save_cache(cache)
    print(f"  requests: {dict(stats)}")

    rows, table = [], defaultdict(Counter)
    for b in todo:
        v, p, d = verdict(b, cache.get(ckey(b), {}))
        table[b["prior_reason"]][v.split(":")[0]] += 1
        rows.append({**b, "verdict": v, "matched_title": (p or {}).get("title", ""),
                     "distance_m": f"{d:.0f}" if d is not None else "",
                     "address": (p or {}).get("address", ""), "phone": (p or {}).get("phoneNumber", ""),
                     "cid": (p or {}).get("cid", ""),
                     "places_returned": len(cache.get(ckey(b), {}).get("places") or [])})
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n  {'prior reason (text search)':32} {'found':>7} {'not listed':>11} {'failed':>7}")
    tot = Counter()
    for reason in sorted(table):
        c = table[reason]
        tot.update(c)
        print(f"  {reason:32} {c['found']:>7} {c['not listed']:>11} {c['request failed']:>7}")
    n = sum(tot.values())
    print(f"  {'TOTAL':32} {tot['found']:>7} {tot['not listed']:>11} {tot['request failed']:>7}"
          f"   found {tot['found'] / max(n, 1) * 100:.1f}%")
    print(f"\n  -> {OUT.relative_to(ROOT)}")
    for r in [x for x in rows if x["verdict"] == "found"][:4]:
        print(f"     found  {r['name'][:22]:24} {r['distance_m']:>4} m  {r['address'][:52]}")
    for r in [x for x in rows if x["verdict"].startswith("not listed")][:4]:
        print(f"     NA     {r['name'][:22]:24} {r['verdict']}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, help="this many per prior reason (calibration)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--input", help="branch CSV (default: blank_address_branches.csv); "
                                   "a POSITIVE CONTROL of already-verified branches proves the method")
    p.add_argument("--out", help="results CSV (default: blank_address_ll_results.csv)")
    p.add_argument("--zoom", default="17z", help="map zoom for ll (17z street level; 15z wider)")
    p.add_argument("--max-age-days", type=int, default=None,
                   help="re-fetch cached answers older than this; default reuses the cache forever")
    a = p.parse_args()
    if not (a.sample or a.all or a.dry_run):
        p.error("choose --sample N, --all or --dry-run")
    sys.exit(asyncio.run(main(a)))
