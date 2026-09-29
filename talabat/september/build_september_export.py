"""September deliverable - September_export.json, in the SAME schema as
export/talabat_export.json and August_menu/data/August_export.json.

EVERY derivation is IMPORTED, twice over: the field helpers come from
August_menu/build_august_export.py, which itself imports smart_title, slugify,
chain_id_int, parse_address_components and is_popular from
export/export_to_json.py. Nothing here recomputes a shipped contract.

chain_id comes from sept_chain_ids.json (build_chain_ids.py) rather than being
derived again, so one file owns it.

TWO DELIBERATE DIFFERENCES FROM AUGUST, both documented at the point of use:

  CITY. August parsed the city out of the Google address and left it null when
  there was none. September holds a coordinate-kNN city for 1,046 of 1,047
  restaurants - measured 99.45% against 47.2% for an LLM on the same task - so
  the parsed value is preferred where it exists and the kNN fills the rest,
  instead of shipping null.

  NO is_verified_location FAN-OUT (unless --fanout). July and August emitted an
  EXTRA record per Google Maps location on top of the per-branch record, each
  repeating the brand's whole menu - the source of July's 220,189 duplicate
  menu entries. September's 44 chains would add ~110 such records. Sagar has
  repeatedly asked for one row per restaurant, so the default here is base
  records only; --fanout reproduces August's behaviour exactly if wanted.

    python build_september_export.py --dry-run
    python build_september_export.py
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))
sys.path.insert(0, str(ROOT / "August_menu"))

from build_august_export import (NA, chain_key, in_uae, km_apart,  # noqa: E402
                                 looks_uae, menu_item, nrm)
from export_to_json import parse_address_components, smart_title   # noqa: E402

CLS = ROOT / "August_classification" / "data_sep" / "restaurants_classified.jsonl"
MAPS = ROOT / "August_classification" / "data_sep" / "google_maps_details.jsonl"
LISTING = HERE / "data" / "sept_restaurants_for_classification.jsonl"
CHAIN = HERE / "data" / "sept_chain_ids.json"
ITEMS = HERE / "data" / "menus" / "menu_items_final.jsonl"
OUT = HERE / "data" / "September_export.json"
REPORT = HERE / "data" / "September_export_report.json"
MAX_ADDR_KM = 25.0
sys.stdout.reconfigure(encoding="utf-8")


def main(args):
    print("=" * 78)
    print("  BUILD September_export.json")
    print("=" * 78)
    stats = Counter()

    listings = {}
    for line in open(LISTING, encoding="utf-8"):
        r = json.loads(line)
        listings[r["branch_id"]] = r
    cls = {}
    for line in open(CLS, encoding="utf-8"):
        c = json.loads(line)
        cls[c["branch_id"]] = c
    chains = json.loads(CHAIN.read_text(encoding="utf-8"))
    maps = defaultdict(list)
    for line in open(MAPS, encoding="utf-8"):
        g = json.loads(line)
        maps[nrm(g["brand"])].append(g)
    menus = defaultdict(list)
    for line in open(ITEMS, encoding="utf-8"):
        m = json.loads(line)
        menus[m["branch_id"]].append(m)
    print(f"  restaurants {len(listings):,} | classified {len(cls):,} | "
          f"chain_ids {len(chains):,}")
    print(f"  menu rows {sum(len(v) for v in menus.values()):,} across "
          f"{len(menus):,} restaurants")
    print(f"  google brands with locations: {len(maps):,}")

    # ---- exclusions, reported not silent -------------------------------
    # A Talabat listing whose OWN coordinates fall outside the UAE is not a UAE
    # restaurant. September had exactly one: branch 790177 "Malak Al Tawouk
    # test" at lat 4.41 - a dropped digit from 24.41, which lands it in the Gulf
    # of Guinea. The name ends in "test" and it is also the single restaurant
    # the city kNN could not label, so three independent signals agree. Test
    # listings recur (the logo pipeline found more), which is why this is a
    # standing gate rather than a one-off patch.
    dropped = []
    for bid in list(listings):
        b = listings[bid]
        try:
            la, lo = float(b["lat"]), float(b["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if not in_uae(la, lo):
            dropped.append({"branch_id": bid, "name": b["name"],
                            "lat": la, "lon": lo, "url": b.get("url"),
                            "reason": "talabat coordinates outside the UAE"})
            del listings[bid]
    if dropped:
        print(f"\n  EXCLUDED {len(dropped)} listing(s):")
        for d in dropped:
            print(f"     {d['branch_id']}  {d['name'][:38]:40} "
                  f"{d['lat']},{d['lon']}  {d['reason']}")

    out = []
    for bid, b in listings.items():
        r = cls.get(bid, {})
        ch = chains.get(str(bid), {})

        # nearest same-brand Google location, capped - a brand holds several and
        # this branch is one of them. Brand-only would import a sibling's phone.
        g, glocs = None, maps.get(nrm(b["name"]), [])
        cand = [(km_apart(b.get("lat") and float(b["lat"]),
                          b.get("lon") and float(b["lon"]),
                          x.get("latitude"), x.get("longitude")), x)
                for x in glocs]
        cand = [(d, x) for d, x in cand if d is not None]
        if cand:
            d, best = min(cand, key=lambda t: t[0])
            if d <= MAX_ADDR_KM:
                g = best
            else:
                stats["brand_listing_too_far"] += 1
        elif glocs:
            g = glocs[0]

        # a Google address is only usable if THAT listing is itself in the UAE
        addr = None
        if g and in_uae(g.get("latitude"), g.get("longitude")):
            addr = g.get("address")
            if addr and not looks_uae(addr):
                stats["address_foreign_rejected"] += 1
                addr = None
        elif g:
            stats["address_outside_uae_rejected"] += 1

        area_name = b.get("area_name") or None
        if addr:
            city, area, sub = parse_address_components(addr)
            raw = addr
            stats["address_real"] += 1
        else:
            raw, city, area, sub = area_name, None, area_name, None
            stats["address_from_area_name"] += 1
        if not area:
            area = area_name
        # SEPTEMBER: fall back to the coordinate-kNN city rather than ship null
        if not city and b.get("city"):
            city = b["city"]
            stats["city_from_knn"] += 1
        elif city:
            stats["city_from_address"] += 1

        lat = float(b["lat"]) if b.get("lat") not in (None, "") else None
        lng = float(b["lon"]) if b.get("lon") not in (None, "") else None
        if lat is None:
            lat, lng = (g or {}).get("latitude"), (g or {}).get("longitude")
            stats["geo_from_serper"] += 1
        else:
            stats["geo_from_talabat"] += 1

        gu = g if (g and in_uae(g.get("latitude"), g.get("longitude"))) else None
        phone = (gu or {}).get("phone") or NA
        website = (gu or {}).get("website") or NA
        maps_url = (gu or {}).get("google_maps_url") or NA
        for f, v in (("phone", phone), ("website", website),
                     ("maps_url", maps_url)):
            if v == NA:
                stats[f"{f}_NA"] += 1

        cuisines = b.get("cuisines") or []
        chain_type = r.get("chained_outlet_type")
        if chain_type in ("N/A", "", None):
            chain_type = None            # July/August ship null for independents

        items = [menu_item(x, stats) for x in menus.get(bid, [])]
        if not items:
            stats["no_menu_items"] += 1
        stats["count_from_maps" if (r.get("google_locations_uae") or 0) >= 1
              else "count_from_talabat"] += 1

        out.append({
            "source_name": "talabat",
            "source_id": str(bid),
            "name": smart_title(b["name"]),
            "cuisine": cuisines[0] if cuisines else None,
            "sub_cuisines": [],
            "key_cuisines": cuisines,
            "restaurant_type": r.get("restaurant_type"),
            "outlet_type": r.get("outlet_type"),
            "chain_type": chain_type,
            "chain_id": ch.get("chain_id"),
            # SEPTEMBER FORWARD (Sagar): this is the GOOGLE MAPS UAE location
            # count, so it agrees with outlet_type, which the Maps count already
            # decides. Until August it was the Talabat branch count, which
            # disagreed visibly - 38 of September's 44 chains showed
            # chain_locations_count 1. Earlier cohorts keep their own meaning;
            # this is not backfilled.
            # A brand with NO Maps presence falls back to its Talabat branch
            # count rather than shipping 0 - the restaurant demonstrably exists,
            # so 0 would be a wrong answer, not a missing one.
            "chain_locations_count": (
                r.get("google_locations_uae")
                if (r.get("google_locations_uae") or 0) >= 1
                else ch.get("chain_locations_count")),
            "currency": "AED",
            "location": {"raw": raw, "country": "UAE", "city": city,
                         "area": area, "sublocality": sub},
            "geo": {"lat": lat, "lng": lng},
            "contact_phone": phone,
            "website": website,
            "maps_url": maps_url,
            "menu_items": items,
        })

    # ---- invariants ----------------------------------------------------
    print(f"\n  records: {len(out):,}")
    assert len({r["source_id"] for r in out}) == len(out), "source_id not unique"
    assert all(r["chain_id"] for r in out), "a record has no chain_id"
    assert all(r["location"]["country"] == "UAE" for r in out)
    bad_geo = [r for r in out if r["geo"]["lat"] is not None
               and not in_uae(r["geo"]["lat"], r["geo"]["lng"])]
    print(f"  ASSERT source_id unique        : PASS")
    print(f"  ASSERT every record has chain_id: PASS")
    print(f"  ASSERT geo inside the UAE      : "
          f"{'PASS' if not bad_geo else f'FAIL {len(bad_geo)}'}")
    assert not bad_geo
    print(f"  menu items total               : "
          f"{sum(len(r['menu_items']) for r in out):,}")
    bad_ct = [r for r in out if r["outlet_type"] == "Chain"
              and (r["chain_locations_count"] or 0) < 2]
    print(f"  ASSERT Chain => count >= 2     : "
          f"{'PASS' if not bad_ct else f'FAIL {len(bad_ct)}'}"
          f"   (chain_locations_count is the Maps count from Sept on)")
    assert not bad_ct
    print(f"    from google maps             : {stats['count_from_maps']:>6,}")
    print(f"    fallback to talabat branches : {stats['count_from_talabat']:>6,}")
    for k in ("address_real", "address_from_area_name", "city_from_address",
              "city_from_knn", "geo_from_talabat", "phone_NA", "website_NA",
              "maps_url_NA", "no_menu_items", "address_foreign_rejected"):
        print(f"    {k:28} {stats[k]:>6,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        print("\n  SAMPLE RECORD (menu trimmed to 2 items):")
        s = dict(out[0]); s["menu_items"] = s["menu_items"][:2]
        print(json.dumps(s, ensure_ascii=False, indent=2)[:2000])
        return 0

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    REPORT.write_text(json.dumps({
        "records": len(out),
        "excluded": dropped,
        "menu_items": sum(len(r["menu_items"]) for r in out),
        "stats": dict(stats),
        "outlet_type": dict(Counter(r["outlet_type"] for r in out)),
        "chain_type": dict(Counter(str(r["chain_type"]) for r in out)),
        "restaurant_type": dict(Counter(r["restaurant_type"] for r in out)),
        "city": dict(Counter(r["location"]["city"] for r in out).most_common()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name}  ({OUT.stat().st_size/1e6:.0f} MB, {len(out):,} records)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
