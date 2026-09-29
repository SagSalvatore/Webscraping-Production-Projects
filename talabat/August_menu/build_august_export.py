"""August deliverable - August_export.json, in the SAME schema as
export/talabat_export.json.

Runs entirely off files. Nothing is loaded into Postgres; that happens later,
separately, once the deliverable is out.

EVERY derivation is IMPORTED from export/export_to_json.py - smart_title,
slugify, chain_id_int, light_normalize_name, strip_fused_address_suffix,
parse_address_components, is_popular. Reimplementing them would drift from the
contract Tech is already built against, on exactly the fields they join on.

CHAIN_ID. chain_id_int(slugify(...)) is a pure function of the brand name -
verified by reproducing 4,000/4,000 of the shipped ids from the name alone. So:
  * every restaurant gets one, chain or independent, per instruction;
  * all locations of one brand share it, because the key is the normalised
    brand name;
  * a brand that also exists in the July catalogue lands on the IDENTICAL id
    with no lookup table and no reconciliation step.
chain_locations_count is the number of August branches on that chain.

MISSING VALUES, per Sagar:
    website / contact_phone / maps_url absent  -> "NA"  (string, not null)
    address absent  -> fall back to area_name, which Talabat gives us on every
                       restaurant, so location.raw and location.area are always
                       populated.
geo stays null when unknown - lat/lng are numbers and "NA" would break the type
for anything parsing them.

    python build_august_export.py --dry-run
    python build_august_export.py
"""
import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))

from export_to_json import (            # noqa: E402  the shipped contract
    chain_id_int,
    is_popular,
    light_normalize_name,
    parse_address_components,
    slugify,
    smart_title,
    strip_fused_address_suffix,
)
from clean_menu_data import (           # noqa: E402  the shipped cleaners
    clean_item_key,
    clean_text,
    extract_english_from_bilingual,
)

CLS = ROOT / "August_classification" / "data" / "restaurants_classified.jsonl"
LISTING = ROOT / "listing_comparison" / "output" / "talabat_genuinely_new_brands.jsonl"
ADDR_WITH = ROOT / "August_classification" / "data" / "restaurants_WITH_address.json"
ADDR_WITHOUT = ROOT / "August_classification" / "data" / "restaurants_WITHOUT_address.json"
MAPS = ROOT / "August_classification" / "data" / "google_maps_details.jsonl"
ITEMS = HERE / "data" / "menu_items_FINAL_reviewed.jsonl"
OUT = HERE / "data" / "August_export.json"
REPORT = HERE / "data" / "August_export_report.json"

NA = "NA"

# UAE bounding box. Serper matches Google listings by brand NAME, and an exact
# name match still lands on a same-named business in another country - measured:
# 220 location records in Orlando, Chicago, Jakarta, Kashmir. Telling Tech a UAE
# restaurant is in Illinois is worse than omitting the location entirely, so any
# enrichment falling outside these bounds is dropped, not shipped.
UAE_BOUNDS = (22.5, 26.2, 51.0, 56.6)      # lat_min, lat_max, lon_min, lon_max


# restaurants_WITH_address.json carries TALABAT's lat/lon, not the Google
# listing's, so its foreign addresses cannot be caught by a coordinate gate -
# the coordinate is a correct UAE one sitting next to a Massachusetts address.
# Those have to be rejected on the address TEXT.
_FOREIGN_TAIL = {c.lower() for c in [
    "USA", "United States", "Canada", "India", "Pakistan", "Turkey", "Türkiye",
    "France", "Italy", "Spain", "Germany", "United Kingdom", "Egypt",
    "Saudi Arabia", "Kuwait", "Qatar", "Bahrain", "Oman", "Indonesia",
    "Malaysia", "Philippines", "Jordan", "Lebanon", "Syria", "Iraq", "Iran",
    "Puerto Rico", "Nigeria", "Kenya", "Australia", "Brazil", "Mexico",
    "China", "Japan", "South Korea", "Thailand", "Vietnam", "Nepal",
    "Bangladesh", "Sri Lanka", "Russia", "Poland", "Greece", "Portugal",
    "Netherlands", "Belgium", "Sweden", "Norway", "Denmark", "Singapore"]}
_US_ZIP = re.compile(r",\s*[A-Z]{2}\s+\d{5}(-\d{4})?\s*$")


_ARABIC = re.compile("[؀-ۿ]+")


def strict_name(s):
    """Fold a name to letters+digits only, Arabic stripped, for EXACT matching.
    Bilingual google titles like 'مطعم الغصن - Al Ghousn Restaurant' reduce to
    'al ghousn restaurant' and correctly equal the brand."""
    t = _ARABIC.sub(" ", str(s or ""))
    t = re.sub(r"[^A-Za-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def title_matches_brand(brand, title):
    """Serper matches Google listings by NAME, and for the short generic names
    common among UAE independents that over-matches badly: 'Rukn' (Arabic for
    'corner') pulled 16 unrelated restaurants - Rukn Al Falah, Rukn Al Rayhan,
    Rukn Al Bait Al Arabi - one of which carried a different restaurant's
    website. 'ghaf' pulled 8, 'oven' 10.

    July never hit this because its 1,989 location records were MNC brands
    (KFC, Starbucks) whose names are globally distinctive.

    A prefix rule does NOT fix it - every 'Rukn Al ...' passes one. Only exact
    equality does, which is also the rule Sagar specified for enrichment.
    """
    b, t = strict_name(brand), strict_name(title)
    return bool(b) and b == t


def looks_uae(address):
    """A country name only counts in the FINAL segment, where a country goes.
    'China Cluster - Dubai' and 'near china mall - Ajman' are real UAE places
    and must NOT be rejected."""
    a = str(address or "").strip()
    if not a:
        return False
    if _US_ZIP.search(a):
        return False
    tail = a.split(" - ")[-1].split(",")[-1].strip().lower()
    return tail not in _FOREIGN_TAIL


# How far a Google listing may sit from the branch's own Talabat coordinates
# and still be believed to describe THAT outlet. Serper matches by brand, so
# without this a chain's Dubai branch inherits the address of its Abu Dhabi one:
# measured 777 base records whose brand-level address was >5 km from the branch,
# 307 of them >100 km, one 18,073 km. Median for a correct match is 0.02 km, so
# 2 km is generous.
MAX_ADDR_KM = 2.0


def km_apart(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    return math.hypot((lat1 - lat2) * 111.0, (lon1 - lon2) * 100.0)


def in_uae(lat, lon):
    if lat is None or lon is None:
        return False
    return (UAE_BOUNDS[0] <= lat <= UAE_BOUNDS[1]
            and UAE_BOUNDS[2] <= lon <= UAE_BOUNDS[3])

sys.stdout.reconfigure(encoding="utf-8")


def nrm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def chain_key(name):
    """Identical to build_chain_ids() for the non-MNC path: smart_title, strip
    the fused '... in <place>, UAE' address artefact, then case/space
    normalise. Must not diverge or August ids stop matching July's."""
    return light_normalize_name(strip_fused_address_suffix(smart_title(name)))


def load_maps():
    """Brand-level Google enrichment, keyed on brand name (the file has no
    branch_id). Returns {brand: [all locations]} - the FULL list, because each
    location becomes its own verified record, exactly as July's 1,989 did."""
    by_brand = defaultdict(list)
    with open(MAPS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                by_brand[nrm(r.get("brand"))].append(r)
    return by_brand


def load_listing():
    """Talabat's OWN lat/lon, from the listing scrape. 100% coverage on all
    7,244 - far better than the 41.4% the brand-level Serper join gives, and
    it is per-BRANCH rather than per-branch there, so Talabat's is also the
    correct geometry for the specific outlet."""
    out = {}
    with open(LISTING, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["branch_id"]] = r
    return out


def load_maps_urls():
    """Branch-keyed Google Maps URLs. restaurants_WITH_address.json (456) and
    restaurants_WITHOUT_address.json (2,906) both carry branch_id, so unlike
    google_maps_details.jsonl these join exactly rather than by brand name.
    Union = 3,362, against 2,961 from the brand join alone."""
    out = {}
    for p in (ADDR_WITH, ADDR_WITHOUT):
        for x in json.load(open(p, encoding="utf-8")):
            if x.get("google_maps_url"):
                out[x["branch_id"]] = x["google_maps_url"]
    return out


def load_branch_addresses():
    """Branch-level addresses - the only ones safe to derive city from."""
    out = {}
    for x in json.load(open(ADDR_WITH, encoding="utf-8")):
        if x.get("address"):
            out[x["branch_id"]] = x
    return out


def load_menu():
    by_branch = defaultdict(list)
    n = 0
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            by_branch[r["branch_id"]].append(r)
            n += 1
    return by_branch, n


def menu_item(r, stats):
    raw_cat = r.get("category") or ""
    key = clean_item_key(r.get("item_key") or r.get("item_name") or "")
    if not key:
        key = clean_text(r.get("item_name") or "") or (r.get("item_name") or "")
        stats["item_name_fallback"] += 1
    sec = clean_text(raw_cat)
    desc = extract_english_from_bilingual(r.get("description") or "")
    return {
        "name": smart_title(str(key).replace("_", " ")),
        "section": smart_title(str(sec or "").replace("_", " ")) if sec else "",
        "description": smart_title(desc) if desc else None,
        "std_term": smart_title(r.get("std_term")) if r.get("std_term") else None,
        "price": r.get("price_aed"),
        "ingredients": r.get("ingredients") or [],
        "is_popular": is_popular(raw_cat),
    }


def main(args):
    print("=" * 72)
    print("  BUILD August_export.json")
    print("=" * 72)

    rests = [json.loads(l) for l in open(CLS, encoding="utf-8") if l.strip()]
    maps = load_maps()
    listing = load_listing()
    maps_urls = load_maps_urls()
    branch_addr = load_branch_addresses()
    menus, n_items = load_menu()
    print(f"  talabat geo  : {sum(1 for r in rests if listing.get(r['branch_id'], {}).get('lat')):,}")
    print(f"  maps_url     : {len(maps_urls):,} branch-keyed")
    print(f"  restaurants : {len(rests):,}")
    print(f"  menu items  : {n_items:,} across {len(menus):,} branches")
    print(f"  maps brands : {len(maps):,}")

    # chain_id + chain_locations_count over the whole August cohort.
    #
    # Counted by the FINAL chain_id, not by the pre-slugify key. slugify folds
    # spelling variants of one brand together - 'etch cafe'/'ètch cafe',
    # "big bowl's"/'big bowl`s', one name carrying an invisible RTL mark - so
    # 6,686 keys become 6,682 ids. Counting by key would split those brands'
    # location counts across their variants and under-report every one of them.
    keys = {r["branch_id"]: chain_key(r["restaurant_name"]) for r in rests}
    ids = {bid: chain_id_int(slugify(k)) for bid, k in keys.items()}
    counts = Counter(ids.values())
    folded = len(set(keys.values())) - len(counts)
    print(f"  distinct chain keys: {len(set(keys.values())):,} -> "
          f"{len(counts):,} chain_ids ({folded} spelling variants folded)")
    print(f"  multi-location brands: {sum(1 for v in counts.values() if v > 1):,}")

    stats = Counter()
    out = []
    for r in rests:
        bid = r["branch_id"]
        cid = ids[bid]
        if not menus.get(bid) and not args.keep_empty:
            # 111 restaurants return no menu items. Sagar: strip them from the
            # Tech deliverable, keep them for the DB. --keep-empty retains them.
            stats["dropped_no_menu"] += 1
            continue
        glocs = maps.get(nrm(r["restaurant_name"])) or []
        b = listing.get(bid) or {}
        # pick the google listing nearest THIS branch, not simply the first one
        g = None
        if glocs and b.get("lat") is not None:
            cand = [(km_apart(b["lat"], b["lon"], x.get("latitude"),
                              x.get("longitude")), x) for x in glocs]
            cand = [(dd, x) for dd, x in cand if dd is not None]
            if cand:
                dd, best = min(cand, key=lambda t: t[0])
                if dd <= MAX_ADDR_KM:
                    g = best
                else:
                    stats["brand_listing_too_far"] += 1
        elif glocs:
            g = glocs[0]
        area_name = r.get("area_name") or None

        # branch-level address beats the brand-level one.
        #
        # A brand-level address is only trustworthy if THAT google listing is
        # itself in the UAE. Serper matches by brand name, so "Atomic Cafe"
        # resolved to Beverly, Massachusetts and "Falafel Garden" to Kyoto -
        # 183 base records were about to ship a foreign address bolted onto
        # correct UAE coordinates from Talabat. Same rule the location records
        # already use; it just was not applied on this path.
        ba = branch_addr.get(bid)
        branch_address = (ba or {}).get("address")
        if branch_address and not looks_uae(branch_address):
            stats["branch_address_foreign_rejected"] += 1
            branch_address = None
        brand_addr = None
        if g and in_uae(g.get("latitude"), g.get("longitude")):
            brand_addr = g.get("address")
            if brand_addr and not looks_uae(brand_addr):
                stats["brand_address_foreign_rejected"] += 1
                brand_addr = None
        elif g:
            stats["brand_address_outside_uae_rejected"] += 1
        address = branch_address or brand_addr
        if branch_address:
            stats["address_branch_level"] += 1
        if address:
            city, area, sub = parse_address_components(address)
            raw = address
            stats["address_real"] += 1
        else:
            # per Sagar: no address -> use the area_name Talabat gave us
            raw, city, area, sub = area_name, None, area_name, None
            stats["address_from_area_name"] += 1
        if not area:
            area = area_name

        # Talabat's own coordinates first - 100% coverage and branch-specific.
        lat, lng = b.get("lat"), b.get("lon")
        if lat is None:
            lat, lng = (g or {}).get("latitude"), (g or {}).get("longitude")
            stats["geo_from_serper"] += 1
        else:
            stats["geo_from_talabat"] += 1
        if lat is None:
            stats["geo_missing"] += 1

        # phone/website come from the same foreign listing when it is wrong,
        # so they are gated on the same check
        gu = g if (g and in_uae(g.get("latitude"), g.get("longitude"))) else None
        phone = (gu or {}).get("phone") or NA
        website = (gu or {}).get("website") or NA
        maps_url = maps_urls.get(bid) or (g or {}).get("google_maps_url") or NA
        for f, v in (("phone", phone), ("website", website), ("maps_url", maps_url)):
            if v == NA:
                stats[f"{f}_NA"] += 1

        cuisines = [c.strip() for c in str(r.get("cuisines") or "").split(",")
                    if c.strip()]
        chain_type = r.get("chained_outlet_type")
        if chain_type in ("N/A", "", None):
            chain_type = None          # July ships null for independents

        items = [menu_item(x, stats) for x in menus.get(bid, [])]
        if not items:
            stats["no_menu_items"] += 1

        out.append({
            "source_name": "talabat",
            "source_id": str(bid),
            "name": smart_title(r.get("restaurant_name")),
            "cuisine": cuisines[0] if cuisines else None,
            "sub_cuisines": [],
            "key_cuisines": cuisines,
            "restaurant_type": r.get("restaurant_type"),
            "outlet_type": r.get("outlet_type"),
            "chain_type": chain_type,
            "chain_id": cid,
            "chain_locations_count": counts[cid],
            "currency": "AED",
            "location": {"raw": raw, "country": "UAE", "city": city,
                         "area": area, "sublocality": sub},
            "geo": {"lat": lat, "lng": lng},
            "contact_phone": phone,
            "website": website,
            "maps_url": maps_url,
            "menu_items": items,
        })

    # ---- verified location records, the August analogue of July's 1,989 ----
    #
    # Serper found 3,909 Google locations across 2,727 brands. July emitted one
    # record per location for its MNC brands, ON TOP of the per-branch records,
    # marked is_verified_location=true and repeating the brand's menu (which is
    # where July's 220,189 duplicate menu entries come from). source_id stays
    # the representative branch's, so source_id is NOT unique in the file -
    # 17,187 entries over 15,198 ids in July. Mirrored exactly here.
    rep_by_brand = {}
    for rec in out:
        rep_by_brand.setdefault(nrm(rec["name"]), rec)
    loc_records = []
    for brand, locs in maps.items():
        rep = rep_by_brand.get(brand)
        if not rep:
            continue
        for loc in locs:
            if not in_uae(loc.get("latitude"), loc.get("longitude")):
                stats["location_outside_uae_dropped"] += 1
                continue
            if not title_matches_brand(loc.get("brand"), loc.get("title")):
                stats["location_name_mismatch_dropped"] += 1
                continue
            addr = loc.get("address")
            if addr and not looks_uae(addr):
                # a foreign address means the listing is not this UAE outlet -
                # drop the record rather than keep it with the address blanked
                stats["location_foreign_address_dropped"] += 1
                continue
            city, area, sub = (parse_address_components(addr) if addr
                               else (None, None, None))
            loc_records.append({
                **{k: rep[k] for k in
                   ("source_name", "source_id", "name", "cuisine",
                    "sub_cuisines", "key_cuisines", "restaurant_type",
                    "outlet_type", "chain_type", "chain_id")},
                "chain_locations_count": len(locs),
                "currency": "AED",
                "location": {"raw": addr or rep["location"]["raw"],
                             "country": "UAE", "city": city,
                             "area": area or rep["location"]["area"],
                             "sublocality": sub},
                "geo": {"lat": loc.get("latitude"), "lng": loc.get("longitude")},
                "contact_phone": loc.get("phone") or NA,
                "website": loc.get("website") or NA,
                "maps_url": loc.get("google_maps_url") or NA,
                "is_verified_location": True,
                "menu_items": rep["menu_items"],
            })
    stats["verified_location_records"] = len(loc_records)
    out.extend(loc_records)

    n_menu = sum(len(r["menu_items"]) for r in out)
    print(f"\n  --- built {len(out):,} restaurant records, "
          f"{n_menu:,} menu item entries ---")
    for k in ("address_real", "address_branch_level", "address_from_area_name",
              "geo_from_talabat", "geo_from_serper", "geo_missing",
              "phone_NA", "website_NA", "maps_url_NA", "dropped_no_menu",
              "item_name_fallback", "verified_location_records",
              "location_outside_uae_dropped",
              "brand_address_outside_uae_rejected", "brand_listing_too_far",
              "branch_address_foreign_rejected", "brand_address_foreign_rejected",
              "verified_address_foreign_rejected",
              "location_name_mismatch_dropped",
              "location_foreign_address_dropped"):
        if stats[k]:
            print(f"     {k:26} {stats[k]:>7,}")

    cids = {r["chain_id"] for r in out}
    multi = sum(1 for r in out if r["chain_locations_count"] > 1)
    print(f"\n  chain_id: {len(cids):,} distinct | "
          f"{multi:,} records on a multi-location brand")
    print(f"  every record has one: {all(r['chain_id'] for r in out)}")
    byname = defaultdict(set)
    for r in out:
        byname[nrm(r["name"])].add(r["chain_id"])
    split = {n: c for n, c in byname.items() if len(c) > 1}
    print(f"  brands split across >1 chain_id: {len(split)}   <- must be 0")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    REPORT.write_text(json.dumps({
        "restaurants": len(out), "menu_item_entries": n_menu,
        "distinct_chain_ids": len(cids),
        "records_on_multi_location_brand": multi,
        "stats": dict(stats),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name} ({OUT.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-empty", action="store_true",
                   help="keep the 111 restaurants that have no menu items")
    sys.exit(main(p.parse_args()))
