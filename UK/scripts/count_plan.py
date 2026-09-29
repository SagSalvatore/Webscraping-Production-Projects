"""
count_plan.py - decides, for every brand, where its "Number of Outlets" comes
from. Exactly one source per brand, in this order:

  1. official   counted by the project team from the brand's own website
  2. report     a location-report count (ScrapeHero / xmap, built from the
                brand's own store locator, concessions included) dated 2025+,
                and not contradicted by the independent web figure
  3. published  the company's own published figure - for pub groups whose pubs
                trade under their own names (Google Maps cannot find them by
                brand), and Costa stores (its report counts Costa Express
                self-serve machines too: 16,619 vs ~2,700 stores)
  4. apify      a Google Maps count via apify_outlets.py - only in regions whose
                brands are small enough to count within budget (the UK). For
                the US top 30 (1,000-19,000 outlets each) the fallback is
                instead:
     web        the verified web / company figure from research.py

    CHAIN_REGION=usa python count_plan.py   # writes data/count_plan.json, prints the split

Per-brand decisions live in RULES, keyed by region.
"""
from __future__ import annotations

import json

import config
from research import load_rows

RULES = {
    "uk": {
        "apify_fallback": True,
        # Pubs trade under their own names ("The Red Lion"), so a Google Maps
        # search by brand cannot enumerate them; Greene King's figure also
        # counts leased and tenanted pubs that do not link to Greene King.
        "published": {
            "Greene King Pubs", "Craft Union", "Chef & Brewer", "Brunning & Price",
            "Nicholson's", "Peach Pubs", "Lounges",
            # Apify found 21 of ~246 and 81 of ~235 - their pubs are mostly
            # listed under the pub's own name
            "Hungry Horse", "Sizzling Pubs",
            "Costa Coffee",   # stores only - decided 2026-09-25
        },
        "reject_report": {
            "Costa Coffee": "report counts Costa Express self-serve machines; stores only wanted",
            "Pure": "report is for a different business (445 vs ~17 Pure shops)",
        },
        "review_note": {
            "Yo! Sushi": "report count includes supermarket kiosks (concessions are in scope)",
            "Pizza Hut Restaurants": "store-locator count may include delivery units",
            "Côte": "same brand as the 'Côte Brasserie' row",
            "Côte Brasserie": "same brand as the 'Côte' row",
            # Apify counts worth a second look - set 2026-09-25 after the run
            "Revolution": "10 open on Google Maps after The Revel Collective's administration closed many bars",
            "The Ivy Collection": "33 listings link to ivycollection.com; sites without a website on Google are missed (web figure ~47)",
            "Pepe's Piri Piri": "Apify run stopped at the $35 limit near the end of its sweep; count may be slightly low",
            "Kaspa's": "Apify run stopped at the $35 limit near the end of its sweep; count may be slightly low",
            "Upper Crust": "Apify run stopped at the $35 limit near the end of its sweep; count may be slightly low",
            "Bar + Block": "no open site on Google Maps; the listed Bar + Block Steakhouses are permanently closed",
            "Fireaway Pizza": "95 on Google Maps; the brand's own figure (~169) may include sites outside the UK",
            "Coco di Mama": "run reached its cap - a minimum figure",
            "German Doner Kebab": "run reached its cap - a minimum figure",
            "TGI Fridays": "open sites only; many closed since the 2024 administration",
            "Frankie & Benny's": "open sites only; many closed",
            "Chiquito": "open sites only; 50 listed sites are permanently closed",
            "Sizzling Pubs": "published figure from a third-party venue site (pubs trade under their own names)",
            "Costa Coffee": "stores only (Costa Express machines excluded); figure from Wikipedia",
        },
        # have a fresh report AND get an Apify count, to compare
        "calibration": {"Franco Manca", "Itsu"},
        # counted by the team from the brand's own website (2026-09-25)
        "official": {"Cake Box": 310, "Pizza Hut Delivery": 364, "Bill's": 50},
        "official_url": {"Bill's": "https://bills-website.co.uk/locations/"},
        "official_as_of": "2026-09-25",
    },
    "usa": {"apify_fallback": False},
    # counts from region_counts.py (per-country sums / stated regional totals);
    # what they cannot cover goes to Apify within the shared $50 cap
    # apify_extra: web sums that are partial or stale (checked 2026-09-28) - an
    # Apify count, when there is one, replaces them
    "europe": {"apify_fallback": True,
               "apify_extra": {"La Piadineria", "Columbus Café"},
               # a company figure beats a Google Maps sweep that was cut short
               "country_fix": {
                   "Pizza Hut": {"Czech Republic": dict(
                       count=16, as_of=None, source="web", url="https://www.mediaguru.cz/pizza-hut-s-novym-konceptem-mirime-i-do-mensich-mest",
                       quote="Pizza Hut currently operates 16 restaurants on the Czech market (the 370 read earlier "
                             "was every pizza restaurant in Czechia)")},
               },
               "published_fig": {
                   "Kotipizza": dict(count=300, as_of="2026", url="https://fi.wikipedia.org/wiki/Kotipizza",
                                     evidence="fi.wikipedia: in 2026 the chain had 300 restaurants in Finland "
                                              "(kotipizzagroup.com: over 300); replaces a 2010 figure of 281"),
                   "Autogrill": dict(count=400, as_of=None, url="https://www.myautogrill.it",
                                     evidence="myautogrill.it: more than 50 brands and 400 points of sale in Italy"),
                   "Marie Blachère": dict(count=830, as_of=None, url="https://www.heypongo.com/blog/marie-blachere-rentabilite-franchise",
                                          evidence="830 bakeries (France, Belgium, Portugal, United States, Canada and "
                                                   "Luxembourg); snacking.fr: nearly 850 bakeries"),
                   "Steinecke": dict(count=518, as_of="2025",
                                     url="https://www.handelsdaten.de/backereien/top-10-backwarenfilialisten-anzahl-verkaufsstellen-deutschland-2025",
                                     evidence="handelsdaten.de top-10 bakery chains 2025: grows by 16 branches to 518 "
                                              "locations (Wikipedia: 518 branches in 2024; steinecke.de: over 500)"),
               },
               "review_note": {
                   "Steinecke": "Google Maps sweep stopped early (351 bakeries found); company figure used",
                   "New York Pizza": "Netherlands run stopped when its key hit the monthly limit; count may be slightly low",
                   "Autogrill": "Autogrill sites in Italy; Avolta reports over 800 F&B stores in Italy (2024), "
                                "counting each brand unit inside those sites",
                   "Marie Blachère": "network total incl. a few stores in the US and Canada",
                   "5 to go": "open sites on Google Maps across the EU-27; no dated company figure found to compare",
                   "Paul": "France from a dated store-locator report (402, May 2026) - above the client estimate on "
                           "its own",
                   "Columbus Café": "open French sites on Google Maps (the last web figure was 200, from 2022)",
                   "Subway": "sum of 15 countries; several smaller markets not found - a minimum",
                   "Starbucks": "sum of 16 countries; some figures dated 2024 - review",
               }},
    "gcc": {"apify_fallback": True,
            "apify_extra": {"KFC", "Pizza Hut", "Herfy", "Caribou Coffee", "Papa Johns", "Dr.CAFE", "Dip n Dip",
                            "Popeyes", "Texas Chicken", "Dunkin'", "McDonald's", "Chicking",
                            "Al Tazaj", "Costa Coffee"},
            # "GCC totals" that are not GCC totals (read 2026-09-28)
            "reject_aggregate": {
                "Cinnabon": "the 152 is Nesto hypermarkets' store count, not Cinnabon's",
                "Domino's Pizza": "the 523 is Alamar Foods' 11-country total (incl. Egypt, Pakistan, Morocco)",
                "Al Tazaj": "the 106 comes from an unidentified per-country tally, not Al Tazaj",
                "Starbucks": "the 450 is dated Q3 2017",
            },
            # checked against the per-country web figures and the brands' own sites, 2026-09-28
            "review_note": {
                "Baskin-Robbins": "stated network of the master franchisee (Galadari) also covers Jordan and "
                                  "Australia - the GCC-only figure is lower",
                "Starbucks": "sum of Saudi Arabia, UAE and Kuwait only; Qatar, Bahrain and Oman figures not found - "
                             "a minimum",
                "Domino's Pizza": "sum of Saudi Arabia and UAE only; the other four GCC figures not found - a minimum",
                "Cinnabon": "sum of Saudi Arabia and UAE only; the other four GCC figures not found - a minimum",
                "McDonald's": "Google Maps 912 agrees with the per-country web figures (914: KSA 458, UAE 222, "
                              "Kuwait 89, Qatar 78, Oman 35, Bahrain 32); the client estimate looks low",
                "Dunkin'": "Saudi run reached its cap (460) - a minimum figure; web sources put KSA near 800",
                "Dip n Dip": "open GCC sites on Google Maps; the brand's own site states 83 cafes in 12 "
                             "countries worldwide, below the client's GCC estimate",
                "Chicking": "open GCC sites on Google Maps; the 230 web figure covers 30+ countries",
                "Gloria Jean's Coffees": "open GCC sites on Google Maps; 40 listed sites are permanently closed",
                "Dr.CAFE": "open Saudi sites on Google Maps, English and Arabic titles searched; company figures "
                           "found are undated or wider in scope (over 122 shops in Saudi Arabia; 650 across the "
                           "Middle East and Asia-Pacific)",
                "Texas Chicken": "open GCC sites on Google Maps; 23 listed sites are permanently closed",
                "Costa Coffee": "open GCC stores on Google Maps in all six countries, most in the UAE, Saudi Arabia "
                                "and Kuwait; Costa Express machines excluded; the web figures found were partial "
                                "(no UAE figure, Bahrain from 2018)",
                "Al Tazaj": "open GCC sites on Google Maps, almost all in Saudi Arabia",
            }},
}
RULE = {"published": set(), "reject_report": {}, "review_note": {}, "calibration": set(),
        "official": {}, "official_url": {}, "official_as_of": None, **RULES.get(config.REGION, {})}
AGREE_RANGE = (0.65, 1.5)                # report / web-figure ratio accepted without review


def _jsonl(path):
    return {r["input_name"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def build_multi(estimates: dict) -> dict:
    """Multi-country regions: the per-country sum and any stated regional total
    from region_counts.py; where both exist and disagree, the client's estimate
    breaks the tie and the row is flagged."""
    rc = _jsonl(config.DATA_DIR / "region_counts.jsonl")
    op = config.DATA_DIR / "outlets.jsonl"
    outlets = _jsonl(op) if op.exists() else {}
    scope = config.R["scope_label"]
    plan = {}
    for idx, name in load_rows():
        r = rc.get(name, {})
        entry = {"row_index": idx, "input_name": name, "note": RULE["review_note"].get(name)}
        cands = []
        # per-country figures read wrong (a market-wide count, a stale year) are replaced or dropped
        pc = dict(r.get("per_country") or {})
        for c, v in RULE.get("country_fix", {}).get(name, {}).items():
            if v is None:
                pc.pop(c, None)
            else:
                pc[c] = v
        if pc:
            r = {**r, "per_country": pc, "country_sum": sum(v["count"] for v in pc.values())}
        if r.get("country_sum"):
            pc = r["per_country"]
            parts = ", ".join(f"{c} {v['count']:,} ({v['source']}{', ' + str(v['as_of']) if v.get('as_of') else ''})"
                              for c, v in sorted(pc.items(), key=lambda kv: -kv[1]["count"]))
            dates = sorted(v["as_of"] for v in pc.values() if v.get("as_of"))
            cands.append(dict(source="region_sum", count=r["country_sum"], as_of=dates[-1] if dates else None,
                              url=next(iter(pc.values()))["url"],
                              evidence=f"sum of {len(pc)} countries: {parts}"))
        if r.get("region_aggregate") and name not in RULE.get("reject_aggregate", {}):
            a = r["region_aggregate"]
            cands.append(dict(source="aggregate", count=a["count"], as_of=a.get("as_of"), url=a["url"],
                              evidence=f"stated {a['scope']} total: {a['quote']}"))
        o = outlets.get(name)
        if o and name in RULE.get("apify_extra", set()) and o.get("raw"):
            cands = [dict(source="apify", count=o["uk_outlets"], as_of=None, url=None,
                          evidence=f"Google Maps count via Apify ({o['runs']} country runs)")]
        if name in RULE.get("published_fig", {}):
            cands = [dict(source="published", **RULE["published_fig"][name])]
        est = estimates.get(idx)
        if len(cands) == 2 and est:
            mid = sum(est) / 2
            cands.sort(key=lambda c: abs(c["count"] - mid))
            lo, hi = sorted(c["count"] for c in cands)
            if hi > lo * 1.35:
                entry["note"] = (f"country sum {cands[0]['count'] if cands[0]['source'] == 'region_sum' else cands[1]['count']:,} vs "
                                 f"stated {scope} total; the one closer to the client estimate is used - review")
        if cands:
            entry.update(cands[0])
        elif RULE["apify_fallback"]:
            entry.update(source="apify", count=None)
        else:
            entry.update(source="missing", count=None)
        entry["apify"] = entry["source"] == "apify" or name in RULE.get("apify_extra", set())
        plan[name] = entry
    return plan


def build() -> dict:
    if config.R.get("countries"):
        estimates = {}
        if config.R.get("estimate_col"):
            from build_output import parse_estimate
            client = config.load_input()
            estimates = {i: parse_estimate(v) for i, v in enumerate(client[config.R["estimate_col"]])}
        return build_multi(estimates)
    reports = _jsonl(config.DATA_DIR / "location_reports.jsonl")
    research = _jsonl(config.DATA_DIR / "research.jsonl")
    review, reject = RULE["review_note"], RULE["reject_report"]
    estimates = {}
    if config.R.get("estimate_col"):
        from build_output import parse_estimate
        client = config.load_input()
        estimates = {i: parse_estimate(v) for i, v in enumerate(client[config.R["estimate_col"]])}
    plan = {}
    for idx, name in load_rows():
        rep = reports.get(name, {})
        r = research.get(name, {})
        best, hint = rep.get("best"), r.get("uk_outlets_hint")
        entry = {"row_index": idx, "input_name": name, "note": review.get(name)}
        usable = bool(best and rep.get("fresh") and name not in reject)
        if usable and hint and not (AGREE_RANGE[0] <= best["count"] / hint <= AGREE_RANGE[1]) and name not in review:
            est = estimates.get(idx)
            if est:
                # The client's estimate breaks the tie: McDonald's US report
                # 13,882 vs a "web figure" of 1,507 read off a per-market PDF.
                mid = sum(est) / 2
                keep_report = abs(best["count"] - mid) <= abs(hint - mid)
                usable = keep_report
                entry["note"] = (f"report {best['count']} disagrees with web figure {hint}; "
                                 f"{'report' if keep_report else 'web figure'} kept as closer to the client estimate - review")
            else:
                usable = False
                entry["note"] = (f"report {best['count']} disagrees with web figure {hint}; "
                                 + ("counted on Google Maps instead" if RULE["apify_fallback"] else "web figure used - review"))
        web = dict(source="published", count=hint, as_of=None, url=(r.get("uk_outlets_hint_urls") or [None])[0],
                   evidence="figure stated by the company / press in the cited source")
        if name in RULE["official"]:
            entry.update(source="official", count=RULE["official"][name], as_of=RULE["official_as_of"],
                         url=RULE["official_url"].get(name),
                         evidence="counted from the brand's official website by the project team")
        elif usable:
            entry.update(source="report", count=best["count"], as_of=best["as_of"], url=best["url"],
                         evidence=best["sentence"])
        elif name in RULE["published"]:
            entry.update(web)
            if name in reject:
                entry["note"] = review.get(name) or reject[name]
        elif RULE["apify_fallback"]:
            entry.update(source="apify")
            if name in reject:
                entry["note"] = reject[name]
        elif hint:
            entry.update({**web, "source": "web"})
            if best and not rep.get("fresh"):
                entry["note"] = (entry["note"] or "") + f" older report: {best['count']} as of {best['as_of']}"
        else:
            entry.update(source="missing", count=None)
        entry["apify"] = entry["source"] == "apify" or name in RULE["calibration"]
        plan[name] = entry
    return plan


def main() -> None:
    plan = build()
    (config.DATA_DIR / "count_plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    by = {}
    for e in plan.values():
        by.setdefault(e["source"], []).append(e["input_name"])
    for s, names in by.items():
        print(f"{s:<10} {len(names):>3}  {', '.join(names[:12])}{' ...' if len(names) > 12 else ''}")
    print(f"apify runs incl. calibration: {sum(e['apify'] for e in plan.values())}")
    missing = [e["input_name"] for e in plan.values() if e["source"] in ("published", "web") and not e["count"]]
    if missing:
        print("FIGURE MISSING:", missing)


if __name__ == "__main__":
    main()
