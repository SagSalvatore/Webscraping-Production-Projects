"""Stage 1 - free signals computed locally. No API calls, no cost.

Two sources the previous (15k) run did not have:

  * MENU PROFILE. We now hold 506,575 menu items with std_term + taxonomy for
    these exact restaurants. What a place actually sells is stronger evidence
    of its format than a web snippet: a menu that is 80% `hot & cold beverages`
    + `cake` is a Cafe; one that is mostly `combo` + `burger` is QSR.
  * PRIOR CLASSIFICATIONS. tavily/FINAL_CLASSIFY.csv holds 15,768 already
    decided restaurants; identical brand names are reused for free.

Nothing here is final except `prior` reuse - the rest are HINTS passed to the
LLM, which sees them alongside the Google Maps evidence. Rules that fire alone
are recorded with their method so their accuracy can be audited separately.
"""
import csv
import json
import re
from collections import Counter, defaultdict

from config import (BAKERY_CUISINES, CAFE_BLOCKERS, CAFE_CUISINES,
                    CLOUD_KITCHEN_NAME, MENU_ITEMS, PRIOR_CLASSIFY, RESTAURANTS)

csv.field_size_limit(10 ** 7)


def norm_name(s: str) -> str:
    """Brand key: lowercase, punctuation stripped, whitespace collapsed."""
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


def load_restaurants():
    return [json.loads(l) for l in open(RESTAURANTS, encoding="utf-8") if l.strip()]


def load_menu_profile():
    """branch_id -> {taxonomy: share, top_terms: [...], items: n}."""
    tax = defaultdict(Counter)
    term = defaultdict(Counter)
    try:
        with open(MENU_ITEMS, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                tax[r["branch_id"]][r.get("taxonomy") or "?"] += 1
                term[r["branch_id"]][r.get("std_term") or "?"] += 1
    except FileNotFoundError:
        return {}
    out = {}
    for bid, c in tax.items():
        n = sum(c.values())
        out[bid] = {
            "items": n,
            "taxonomy_share": {k: round(v / n, 3) for k, v in c.most_common(4)},
            "top_terms": [t for t, _ in term[bid].most_common(5)],
        }
    return out


def load_prior():
    """normalised brand name -> prior verdict from EVERY earlier run.

    PRIOR_CLASSIFY is a list and is read in order, so a later file wins for a
    brand present in both: August's reviewed verdicts (including the manual
    CHAIN_TYPE_OVERRIDE corrections) supersede the older 15,768-row run.
    """
    prior = {}
    for p in PRIOR_CLASSIFY:
        try:
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    k = norm_name(r.get("restaurant_name"))
                    if not k:
                        continue
                    prior[k] = {
                        "restaurant_type": (r.get("restaurant_type") or "").strip(),
                        "outlet_type": (r.get("outlet_type") or "").strip(),
                        "chained_outlet_type": (r.get("chained_outlet_type") or "").strip(),
                    }
        except FileNotFoundError:
            continue
    return prior


def cuisine_hint(cuisines, name):
    """Returns (restaurant_type|None, why). Order matters - first match wins."""
    low = {str(c).strip().lower() for c in (cuisines or [])}
    nm = (name or "").lower()

    if any(k in nm for k in CLOUD_KITCHEN_NAME):
        return "Cloud Kitchen", "name says cloud/dark/virtual kitchen"

    bakery = low & BAKERY_CUISINES
    if bakery and len(bakery) / max(len(low), 1) >= 0.5:
        return "Bakery", f"baked-goods cuisines dominate ({sorted(bakery)})"

    cafe = low & CAFE_CUISINES
    if cafe and not (low & CAFE_BLOCKERS):
        return "Cafes", f"beverage/dessert cuisines only ({sorted(cafe)})"

    return None, ""


def menu_hint(profile):
    """Format hint from what the restaurant actually sells."""
    if not profile or profile["items"] < 5:
        return None, ""
    share = profile["taxonomy_share"]
    bev = share.get("beverage", 0)
    des = share.get("dessert", 0)
    terms = set(profile["top_terms"])

    if bev >= 0.5:
        return "Cafes", f"beverages are {bev:.0%} of the menu"
    if des >= 0.5 or (terms & {"cake", "pastry", "bread and bakery", "kunafa",
                               "baklava", "maamoul", "sweets & desserts"}
                      and des + share.get("core_food", 0) * 0 >= 0.3):
        return "Bakery", f"desserts/bakery are {des:.0%} of the menu"
    if bev + des >= 0.6:
        return "Cafes", f"beverages+desserts are {bev+des:.0%} of the menu"
    return None, ""


def build(restaurants=None, profiles=None, prior=None):
    """Returns {branch_id: {...hints...}} - one entry per restaurant."""
    restaurants = restaurants if restaurants is not None else load_restaurants()
    profiles = profiles if profiles is not None else load_menu_profile()
    prior = prior if prior is not None else load_prior()

    out = {}
    for r in restaurants:
        bid = r["branch_id"]
        key = norm_name(r.get("name"))
        prof = profiles.get(bid)
        ctype, cwhy = cuisine_hint(r.get("cuisines"), r.get("name"))
        mtype, mwhy = menu_hint(prof)
        out[bid] = {
            "branch_id": bid,
            "restaurant_id": r.get("restaurant_id"),
            "name": r.get("name"),
            "name_key": key,
            "area_name": r.get("area_name"),
            "cuisines": r.get("cuisines") or [],
            "menu_items": (prof or {}).get("items", 0),
            "taxonomy_share": (prof or {}).get("taxonomy_share", {}),
            "top_terms": (prof or {}).get("top_terms", []),
            "cuisine_hint": ctype, "cuisine_why": cwhy,
            "menu_hint": mtype, "menu_why": mwhy,
            "prior": prior.get(key),
        }
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    h = build()
    print(f"restaurants        : {len(h):,}")
    print(f"distinct name keys : {len({v['name_key'] for v in h.values()}):,}")
    print(f"with a menu profile: {sum(1 for v in h.values() if v['menu_items']):,}")
    print(f"cuisine hint fired : {Counter(v['cuisine_hint'] for v in h.values() if v['cuisine_hint'])}")
    print(f"menu hint fired    : {Counter(v['menu_hint'] for v in h.values() if v['menu_hint'])}")
    print(f"prior reuse        : {sum(1 for v in h.values() if v['prior']):,}")
