"""Step 2b - rank zones by MEASURED page-1 novelty, for run2_collector --from-probe.

WHY NOT THE ZONE DELTA. probe_zones.py showed 542 of 562 zones moved since
2026-08-10, so "skip unchanged zones" removes only 12% of the work - 98,546
pages remain, about 45 hours at the measured 0.6 pg/s. totalVendors says a zone
CHANGED; it does not say the change is a restaurant we lack.

WHY NOT THE EFFICIENCY ESTIMATE. That ranking scored **-0.099 Spearman** against
actual yield - it predicts nothing. run2_collector already prefers --from-probe
over --priority-top for this reason.

WHAT THIS USES. probe_zones.py already fetched page 1 of every zone, so the
novel-branch count per zone is free: intersect each zone's page-1 branchIds with
the known universe. That is the same measurement prioritize_areas.py --probe
makes, without re-fetching.

    branchId, NOT id. `id`/`restaurantId` is the parent CHAIN, shared across
    branches. Comparing it to a branch_id universe reported 91.1% of page 1 as
    new; the true rate is 12.1%.

PAGE-1 RATES ARE OPTIMISTIC. Page 1 is the freshest slice of a zone. August
measured 1.21 new branches per page across a whole crawl and 0.35
genuinely_new_brand per page after comparison. Use this to ORDER zones, not to
forecast totals.

    python rank_zones.py --dry-run
    python rank_zones.py                 # writes area_probe_ranked_<month>.json
    python rank_zones.py --install       # also refresh smoke_results/area_probe_ranked.json
"""
import argparse
import json
import shutil
import sys

from config import DATA, MONTH, ROOT, UNIVERSE, ZONE_SNAPSHOT

sys.stdout.reconfigure(encoding="utf-8")

RES = ROOT / "listing_scraper" / "smoke_results"
OUT = DATA / f"area_probe_ranked_{MONTH}.json"
INSTALL_TO = RES / "area_probe_ranked.json"


def main(args):
    print("=" * 74)
    print(f"  RANK ZONES BY MEASURED PAGE-1 NOVELTY  ({MONTH})")
    print("=" * 74)

    uni = set(json.loads(UNIVERSE.read_text(encoding="utf-8"))["branch_ids"])
    zones = json.loads(ZONE_SNAPSHOT.read_text(encoding="utf-8"))["zones"]
    print(f"  universe        : {len(uni):,} branch_ids")
    print(f"  zones in snapshot: {len(zones):,}")

    ranked, no_p1 = [], 0
    for z in zones:
        ids = z.get("p1_ids") or []
        if not ids:
            no_p1 += 1
            continue
        new = [i for i in ids if i not in uni]
        ranked.append({
            "id": z["id"], "name": z["name"], "url": z["url"],
            "total_vendors": z.get("total_vendors"),
            "pages_left": z.get("total_pages") or 0,
            "probe_seen": len(ids),
            "probe_new": len(new),
            "probe_new_rate": round(len(new) / len(ids), 3),
            # expected yield if the whole zone behaved like its page 1. It will
            # not - see the docstring - but it orders zones sensibly by
            # (novelty x size) rather than by novelty alone.
            "probe_expected_new": round(len(new) / len(ids)
                                        * (z.get("total_vendors") or 0)),
        })

    # cheap-and-novel first: rate desc, then smaller zones, so the early batches
    # finish fast and prove the yield before committing to the long tail
    ranked.sort(key=lambda a: (-a["probe_new_rate"], a["pages_left"]))

    live = [a for a in ranked if a["probe_new"]]
    dead = [a for a in ranked if not a["probe_new"]]
    tn = sum(a["probe_new"] for a in ranked)
    ts = sum(a["probe_seen"] for a in ranked)
    print(f"  zones with no page-1 data: {no_p1}")
    print(f"  page-1 new rate  : {tn:,}/{ts:,} = {tn/ts*100:.1f}%")
    print(f"  zones with >0 new: {len(live)}   dead zones: {len(dead)}")

    print(f"\n  {'#':>3} {'zone':32}{'new/15':>7}{'rate':>7}{'pages':>8}")
    print("  " + "-" * 58)
    for i, a in enumerate(live[:20], 1):
        print(f"  {i:>3} {a['name'][:31]:32}{a['probe_new']:>7}"
              f"{a['probe_new_rate']*100:>6.0f}%{a['pages_left']:>8,}")

    for n in (25, 50, 100, 200, len(live)):
        if n > len(live):
            continue
        sel = live[:n]
        pg = sum(a["pages_left"] for a in sel)
        print(f"\n  top {n:>3} zones -> {pg:>7,} pages "
              f"(~{pg/0.6/3600:.1f} h at 0.6 pg/s) | "
              f"~{pg*0.35:,.0f} genuinely_new_brand at August's 0.35/page")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    OUT.write_text(json.dumps(ranked, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\n  -> {OUT.name}  ({len(ranked)} zones)")
    if args.install:
        if INSTALL_TO.exists():
            shutil.copy2(INSTALL_TO, INSTALL_TO.with_suffix(".json.aug.bak"))
        shutil.copy2(OUT, INSTALL_TO)
        print(f"  -> installed as {INSTALL_TO.relative_to(ROOT)} "
              f"(August copy kept as .json.aug.bak)")
        print("\n  NEXT: cd ../listing_scraper && "
              "python run2_collector.py --from-probe 50 --concurrency 40 --dry-run")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--install", action="store_true")
    sys.exit(main(p.parse_args()))
