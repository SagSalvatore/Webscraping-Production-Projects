"""Tests for the classification pipeline.

    python test_pipeline.py              offline only - no API calls, no spend
    python test_pipeline.py --live       adds ~20 real Serper calls + 1 LLM batch

THREE LAYERS:
  SMOKE      does everything import, load, and produce the right shapes
  SANITY     do the rules and coercion logic behave on hand-built cases
  REGRESSION do the specific bugs found while building stay fixed

The regression cases are not hypothetical - each one is a real failure observed
during development, with the observed value recorded in the test.
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config as C
import rules
from serper_places import (match_places, strip_arabic, is_non_uae, name_matches,
                           name_matches_strict, outside_uae)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))


# ───────────────────────────── SMOKE ─────────────────────────────
def smoke():
    print("\n=== SMOKE ===")
    check("restaurants file exists", C.RESTAURANTS.exists(), str(C.RESTAURANTS))
    check("menu items file exists", C.MENU_ITEMS.exists(), str(C.MENU_ITEMS))
    # a list since September: every earlier run feeds the free prior-reuse tier
    check("prior classify exists", all(p.exists() for p in C.PRIOR_CLASSIFY),
          str([str(p) for p in C.PRIOR_CLASSIFY if not p.exists()]))
    check("tavily keys exist", C.TAV_KEYS.exists())

    if not C.RESTAURANTS.exists():
        return
    h = rules.build()
    check("rules.build returns rows", len(h) > 0, f"{len(h)}")
    one = next(iter(h.values()))
    need = {"branch_id", "name", "name_key", "cuisines", "cuisine_hint",
            "menu_hint", "taxonomy_share", "prior"}
    check("hint rows have all keys", need <= set(one), f"missing {need - set(one)}")
    # every stage module must import cleanly and resolve its module-level names.
    # A missing `TAVILY_QUERY` import shipped once and only surfaced at runtime,
    # after the whole stage had been launched.
    import importlib
    for mod in ("rules", "serper_places", "tavily_fallback", "classify_llm"):
        try:
            m = importlib.import_module(mod)
            importlib.reload(m)
            check(f"{mod} imports cleanly", True)
        except Exception as e:
            check(f"{mod} imports cleanly", False, f"{type(e).__name__}: {e}")
    import tavily_fallback as _tf
    check("tavily_fallback resolves TAVILY_QUERY",
          getattr(_tf, "TAVILY_QUERY", None) == "{name} uae restaurant")
    check("prompt mentions Google Maps", "Google Maps" in C.SYSTEM_PROMPT)
    check("prompt defines all 5 types",
          all(t in C.SYSTEM_PROMPT for t in C.VALID_TYPE))
    check("prompt names MNC examples",
          all(b in C.SYSTEM_PROMPT for b in ("KFC", "Domino's", "Subway")))
    check("prompt says Local Chain is the default",
          "choose Local Chain" in C.SYSTEM_PROMPT)


# ───────────────────────────── SANITY ────────────────────────────
def sanity():
    print("\n=== SANITY ===")
    # cuisine rules
    t, _ = rules.cuisine_hint(["Coffee", "Tea", "Desserts"], "Bean There")
    check("coffee+tea -> Cafes", t == "Cafes", f"got {t}")
    t, _ = rules.cuisine_hint(["Coffee", "Biryani", "Indian"], "Spice & Bean")
    check("coffee+biryani -> NOT Cafes", t != "Cafes", f"got {t}")
    t, _ = rules.cuisine_hint(["Bakery", "Cakes", "Pastries"], "Sweet Corner")
    check("bakery cuisines -> Bakery", t == "Bakery", f"got {t}")
    t, _ = rules.cuisine_hint(["Burgers"], "Wing Cloud Kitchen")
    check("cloud kitchen name wins", t == "Cloud Kitchen", f"got {t}")

    # menu profile
    t, _ = rules.menu_hint({"items": 90, "taxonomy_share": {"beverage": 0.72},
                            "top_terms": ["juice"]})
    check("72% beverage menu -> Cafes", t == "Cafes", f"got {t}")
    t, _ = rules.menu_hint({"items": 3, "taxonomy_share": {"beverage": 0.9},
                            "top_terms": []})
    check("tiny menu gives no hint", t is None, f"got {t}")

    # name normalisation
    check("norm_name strips punctuation",
          rules.norm_name("McDonald's  (Al Barsha)") == "mcdonalds al barsha")

    # arabic + geo filters
    check("strip_arabic keeps latin",
          strip_arabic("Karak Basha - كرك باشا") == "Karak Basha")
    check("is_non_uae detects Bahrain", is_non_uae("Busaiteen, Bahrain"))
    check("is_non_uae allows Dubai", not is_non_uae("Al Wasl Rd - Dubai"))

    # derived chained_outlet_type
    from classify_llm import coerce
    h = {"branch_id": 1, "name": "Solo Cafe", "cuisine_hint": None, "menu_hint": None}
    foot = {"Solo Cafe": {"uae_locations": 1, "outlet_type": "Independent"}}
    out = coerce({"restaurant_type": "Cafes", "outlet_type": "Independent",
                  "chained_outlet_type": "Local Chain"}, h, foot)
    check("Independent forced to N/A", out["chained_outlet_type"] == "N/A",
          f"got {out['chained_outlet_type']}")
    foot = {"Solo Cafe": {"uae_locations": 4, "outlet_type": "Chain"}}
    out = coerce({"restaurant_type": "Cafes", "outlet_type": "Independent",
                  "chained_outlet_type": "N/A"}, h, foot)
    check("4 Maps locations override to Chain", out["outlet_type"] == "Chain")
    check("chain with no sub-type defaults Local",
          out["chained_outlet_type"] == "Local Chain")
    h2 = {"branch_id": 2, "name": "KFC", "cuisine_hint": None, "menu_hint": None}
    out = coerce({"restaurant_type": "Quick-Service Restaurants",
                  "outlet_type": "Chain", "chained_outlet_type": "Local Chain"},
                 h2, {"KFC": {"uae_locations": 10, "outlet_type": "Chain"}})
    check("MNC seed overrides to MNC Chain", out["chained_outlet_type"] == "MNC Chain")
    # gate 3: foreign origin + global reach -> MNC even when not in the seed
    h3 = {"branch_id": 9, "name": "Nusr-Et Steakhouse", "cuisine_hint": None, "menu_hint": None}
    f3 = {"Nusr-Et Steakhouse": {"uae_locations": 3, "outlet_type": "Chain"}}
    out = coerce({"restaurant_type": "Full-Service Restaurants", "outlet_type": "Chain",
                  "chained_outlet_type": "MNC Chain", "country_of_origin": "Turkey",
                  "operates_outside_uae": True}, h3, f3)
    # SUPERSEDED Sept 2026: gate 3 is review-only. An unrecognised brand ships
    # as Local Chain however the model answers; only the seed list sets MNC.
    check("gate 3 is review-only, ships Local",
          out["chained_outlet_type"] == "Local Chain"
          and "mnc_claim_pending_review" in out["coercions"])
    # a UAE-BORN brand that expanded abroad is still MNC by this rule
    out = coerce({"restaurant_type": "Cafes", "outlet_type": "Chain",
                  "chained_outlet_type": "Local Chain", "country_of_origin": "UAE",
                  "operates_outside_uae": True}, h3, f3)
    check("UAE-born expanded abroad also review-only",
          out["chained_outlet_type"] == "Local Chain")
    # the seed list is untouched - real multinationals still resolve directly
    hk = {"branch_id": 7, "name": "Texas Chicken", "cuisine_hint": None,
          "menu_hint": None}
    out_k = coerce({"restaurant_type": "Quick-Service Restaurants",
                    "outlet_type": "Chain", "chained_outlet_type": "Local Chain"},
                   hk, {"Texas Chicken": {"uae_locations": 6, "outlet_type": "Chain"}})
    check("seed brand still resolves MNC directly",
          out_k["chained_outlet_type"] == "MNC Chain")
    out = coerce({"restaurant_type": "Full-Service Restaurants", "outlet_type": "Chain",
                  "chained_outlet_type": "MNC Chain", "country_of_origin": "UAE",
                  "operates_outside_uae": False}, h3, f3)
    check("UAE-only stays Local Chain", out["chained_outlet_type"] == "Local Chain")
    out = coerce({"restaurant_type": "Cafes", "outlet_type": "Chain",
                  "chained_outlet_type": "MNC Chain", "country_of_origin": "unknown",
                  "operates_outside_uae": False}, h3, f3)
    check("unevidenced MNC claim demoted to Local",
          out["chained_outlet_type"] == "Local Chain" and "demoted" in out["coercions"])
    out = coerce({"restaurant_type": "Nonsense Type", "outlet_type": "Chain",
                  "chained_outlet_type": "Local Chain"},
                 {"branch_id": 3, "name": "X", "cuisine_hint": "Bakery",
                  "menu_hint": None}, {})
    check("invalid type falls back to rule hint", out["restaurant_type"] == "Bakery")


# ─────────────────────────── REGRESSION ──────────────────────────
    # --- September: foreign results a country BLOCKLIST cannot catch ---
    # Every one of these was in September's output and carried no NON_UAE_TOKEN.
    check("geo gate rejects Canada", outside_uae(43.7758, -79.3224))
    check("geo gate rejects California", outside_uae(34.1461, -118.1313))
    check("geo gate rejects Tunisia", outside_uae(36.9010, 10.1881))
    check("geo gate rejects Portugal", outside_uae(38.7197, -9.1510))
    check("geo gate rejects Malaysia", outside_uae(3.1628, 101.7133))
    check("geo gate rejects Australia", outside_uae(-33.7680, 151.2656))
    check("geo gate keeps Dubai", not outside_uae(25.1972, 55.2744))
    check("geo gate keeps Abu Dhabi", not outside_uae(24.4539, 54.3773))
    check("geo gate keeps Fujairah", not outside_uae(25.1288, 56.3265))
    check("geo gate passes when coords missing", not outside_uae(None, None))

    # --- September: a one-word brand swallowed unrelated businesses ---
    check("CHIPS does not match Fish And Chips Restaurant",
          not name_matches_strict("CHIPS", "Fish And Chips Restaurant"))
    check("TOGETHER does not match Better Together Cafe",
          not name_matches_strict("TOGETHER", "Better Together Cafe Motor City"))
    check("Leo does not match Leo&Loona Kids Park",
          not name_matches_strict("Leo", "Leo&Loona Kids Park"))
    check("Kyoto does not match A Kyoto Sip",
          not name_matches_strict("Kyoto", "A Kyoto Sip"))
    check("Kyoto DOES match Kyoto burger",
          name_matches_strict("Kyoto", "Kyoto burger"))
    check("one-word brand matches itself exactly",
          name_matches_strict("Kyoto", "Kyoto"))
    # SUPERSEDED Sept 2026. This used to assert that a multi-token brand could
    # match mid-title, i.e. that "Nepaliko Sagarmatha Restaurant" counted as a
    # branch of "Sagarmatha Restaurant". Sagar's rule is that a different
    # restaurant sharing the name is not a branch, so the brand must LEAD.
    check("multi-token brand no longer matches mid-title",
          not name_matches_strict("Sagarmatha Restaurant",
                                  "Nepaliko Sagarmatha Restaurant"))
    check("multi-token branch suffix still matches",
          name_matches_strict("Kathmandu Restaurant",
                              "Kathmandu restaurant B1"))


    # --- September: "(>=10, capped)" on a brand with ZERO matches ---
    # capped describes Serper's raw page of 10, not this brand's locations.
    from classify_llm import build_payload
    h = {"branch_id": 1, "name": "X", "cuisines": [], "taxonomy_share": {},
         "top_terms": [], "menu_items": 0, "cuisine_hint": None,
         "menu_hint": None, "cuisine_why": "", "menu_why": "", "prior": None,
         "area_name": ""}
    pay = build_payload(h, {"X": {"uae_locations": 0, "capped": True}}, {})
    check("capped annotation hidden when 0 matched",
          "capped" not in pay, pay)
    pay = build_payload(h, {"X": {"uae_locations": 10, "capped": True}}, {})
    check("capped annotation kept when 10 matched", "capped" in pay, pay)


    # --- Sagar, Sept 2026: different restaurants sharing a name ---
    # containment let unrelated businesses count as branches
    for b, t in [("Juice Center", "Haji Ali Juice Center"),
                 ("Juice Center", "Mumbai Masti Juice Center"),
                 ("Juice Station", "OG JUICE STATION"),
                 ("Aldimashqi Restaurant", "Alqasr Aldimashqi Restaurant"),
                 ("Khorfakkan Restaurant", "Hosun khorfakkan Restaurant"),
                 ("Sagarmatha Restaurant", "Nepaliko Sagarmatha Restaurant"),
                 ("GREEN TEA", "arabian green tea"),
                 ("Parco Restaurant", "Golden Parco Restaurant"),
                 ("AL TAAWON RESTAURANT", "Najam Al Taawon Restaurant"),
                 ("Sweet Burger", "House of the new Sweet Burger")]:
        check(f"rejects '{b[:18]}' <- '{t[:26]}'", not name_matches_strict(b, t))
    # ...while the SAME restaurant must still match
    for b, t in [("Trigo & Taco", "TRIGO&TACO"),
                 ("Wrap & Bite Cafe", "Wrap&Bite Cafe"),
                 ("Qasr Alasala Mandi&Mathbi",
                  "Qasr Alasala mandi and mathbi restaurant"),
                 ("Al Jazeera and Gulf Restaurant", "Al Jazeera & gulf restaurant"),
                 ("KHAFAIEF CAFETERIA", "Al Khafaief Cafeteria"),
                 ("ghandoor cafeteria and pastery", "GHANDOOR Cafeteria & Pastery"),
                 ("Kathmandu Restaurant", "Kathmandu restaurant B1"),
                 ("Al Barq Cafeteria", "AL BARQ CAFETERIA Muwaihat3"),
                 ("Shahad Al Jazeera", "Shahad Al Jazeera Sweets"),
                 ("cup of joy", "كب اوف جوي - cup of joy")]:
        check(f"keeps '{b[:18]}' <- '{t[:26]}'", name_matches_strict(b, t))


def regression():
    print("\n=== REGRESSION (real bugs found while building) ===")
    # 1. Sour Mango: 6 raw places, only 2 the brand -> must not read as a chain
    places = [{"title": "Pakistani and Indian Mangoes", "address": "Dubai"},
              {"title": "Sour Bliss", "address": "Dubai"},
              {"title": "Mango Market DWC", "address": "Dubai"},
              {"title": "Mister Mango Dubai", "address": "Dubai"},
              {"title": "SOUR MANGO RESTAURANT", "address": "Sharjah",
               "latitude": 25.3, "longitude": 55.4}]
    kept = match_places("Sour Mango", places)
    check("fuzzy neighbours excluded (Sour Mango)", len(kept) == 1,
          f"kept {len(kept)}: {[p['title'] for p in kept]}")

    # 2. Karak Basha: exact title match sits in BAHRAIN, another in OMAN
    places = [{"title": "Karak Basha - كرك باشا", "address": "Busaiteen, Bahrain",
               "latitude": 26.2, "longitude": 50.6},
              {"title": "كرك الباشا", "address": "Al Hamra, Oman"}]
    kept = match_places("Karak Basha", places)
    check("foreign locations excluded (Bahrain/Oman)", len(kept) == 0,
          f"kept {len(kept)}")

    # 3. Arabic-only title as the sole result must still count
    kept = match_places("Karak Basha", [{"title": "كرك باشا", "address": "Dubai",
                                         "latitude": 25.1, "longitude": 55.2}])
    check("arabic-only sole result counts", len(kept) == 1, f"kept {len(kept)}")

    # 4. Same outlet listed twice -> one location, not two
    kept = match_places("Al Mallah", [
        {"title": "Al Mallah", "address": "Satwa, Dubai", "latitude": 25.2368, "longitude": 55.2769},
        {"title": "Al Mallah Dhiyafah", "address": "Dhiyafa, Dubai", "latitude": 25.2368, "longitude": 55.2769},
        {"title": "Al Mallah Sharjah", "address": "Al Majaz, Sharjah", "latitude": 25.33, "longitude": 55.38}])
    check("duplicate coordinates collapse", len(kept) == 2, f"kept {len(kept)}")

    # 5. Shahad Al Jazeera: 4 real branches, confirmed manually by Sagar
    kept = match_places("Shahad Al Jazeera Sweets", [
        {"title": "Shahad Al Jazeera Sweets & Pastries Hili", "latitude": 24.30678, "longitude": 55.760216},
        {"title": "Shahad Al Jazeera Sweets & Pastries Al Aamerah", "latitude": 24.223177, "longitude": 55.548645},
        {"title": "Shahad Al Jazeera Sweets & Pastries Al Jahili", "address": "Al Jahili", "latitude": 24.215385, "longitude": 55.757057},
        {"title": "Shahad Al Jazeera Sweets & Pastries Zakher", "latitude": 24.150229, "longitude": 55.69751}])
    check("Shahad Al Jazeera = 4 locations -> Chain",
          len(kept) == 4 and len(kept) >= C.CHAIN_MIN_LOCATIONS, f"kept {len(kept)}")

    # 6. EXACT name match - fuzzy scoring inflated Chain counts.
    #    Each of these was a real false positive in the 100-brand test.
    for brand, title, want in [
            ("The One Restaurant", "A One Restaurant", False),
            ("The One Restaurant", "One to Ten Restaurant LLC", False),
            ("The One Restaurant", "THE OBA RESTAURANT", False),
            ("Kathmandu Restaurant", "Kathmandu Darbar Restaurant", False),
            ("Kathmandu Restaurant", "Kathmandu Palace Restaurant Dubai", False),
            ("Cup of Joy", "CITY OF JOY RESTAURANT L.L.C.", False),
            ("Al Barq Cafeteria", "AL BARQ CAFETERIA Muwaihat3", True),
            ("Kathmandu Restaurant", "Kathmandu restaurant B1", True),
            ("Cup of Joy", "كب اوف جوي - cup of joy", True),
            ("Shahad Al Jazeera Sweets", "Shahad Al Jazeera Sweets & Pastries Hili", True),
            # & == and : recovered 50 brands whose Talabat name spells out "and"
            ("ghandoor cafeteria and pastery", "GHANDOOR Cafeteria & Pastery", True),
            ("Sweets & Pastries", "Sweets and Pastries Branch 2", True)]:
        check(f"exact-match {brand[:16]!r} vs {title[:22]!r}",
              name_matches(brand, title) == want)

    # 7. the query template that measured 15/15 vs 11/15
    # 429 must NEVER be treated as a dead key - this exact bug stopped a run
    # with "every tavily key is dead" after a traffic burst rate-limited them.
    import tavily_fallback as _tf, inspect
    src = inspect.getsource(_tf.preflight)
    check("preflight keeps rate-limited (429) keys",
          "401, 403" in src and "keeping it" in src)
    check("preflight drops only 401/403",
          src.count("dead.append") == 1)

    check("serper template keeps 'uae restaurant'",
          C.SERPER_QUERY == "{name} uae restaurant", C.SERPER_QUERY)
    check("tavily uses the SAME template",
          C.TAVILY_QUERY == "{name} uae restaurant", C.TAVILY_QUERY)
    check("prompt asks for country_of_origin",
          "country_of_origin" in C.SYSTEM_PROMPT)
    check("prompt uses branches-outside-UAE rule",
          "operates_outside_uae" in C.SYSTEM_PROMPT
          and "includes GCC neighbours" in C.SYSTEM_PROMPT)
    check("prompt says unfamiliar => local, not MNC",
          "never heard of it, it is local" in C.SYSTEM_PROMPT)

    # 7. whitespace variants ('N/A ' x135) must be impossible
    for v in list(C.VALID_TYPE) + list(C.VALID_OUTLET) + list(C.VALID_CHAINED):
        if v != v.strip():
            check("no padded constants", False, repr(v))
            break
    else:
        check("no padded constants", True)


def live():
    print("\n=== LIVE (spends a little) ===")
    import os, requests
    from dotenv import load_dotenv
    load_dotenv(C.ROOT / ".env", override=True)
    k = os.getenv("SERPER_API_KEY")
    r = requests.get("https://google.serper.dev/account",
                     headers={"X-API-KEY": k}, timeout=20)
    bal = r.json().get("balance") if r.status_code == 200 else None
    check("serper account reachable", r.status_code == 200, r.text[:80])
    check("serper balance covers 6,678 queries", (bal or 0) > 7000, f"balance={bal}")

    r = requests.post(C.SERPER_URL, headers={"X-API-KEY": k, "Content-Type": "application/json"},
                      json={"q": C.SERPER_QUERY.format(name="KFC"), "gl": "ae"}, timeout=30)
    p = r.json().get("places", []) if r.status_code == 200 else []
    check("KFC returns many places", len(match_places("KFC", p)) >= 2, f"{len(p)} raw")

    r = requests.post(C.SERPER_URL, headers={"X-API-KEY": k, "Content-Type": "application/json"},
                      json={"q": C.SERPER_QUERY.format(name="Bu Qtair"), "gl": "ae"}, timeout=30)
    p = r.json().get("places", []) if r.status_code == 200 else []
    check("Bu Qtair reads as single-location", len(match_places("Bu Qtair", p)) <= 1,
          f"{len(match_places('Bu Qtair', p))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--cohort", default="aug",
                   help="aug | sep - consumed by config at import time, "
                        "declared here only so argparse accepts it")
    a = ap.parse_args()
    smoke(); sanity(); regression()
    if a.live:
        live()
    print(f"\n{'='*54}\n  PASSED {len(PASS)}   FAILED {len(FAIL)}")
    if FAIL:
        print("  failures: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
