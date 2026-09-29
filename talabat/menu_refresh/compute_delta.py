"""Step 3 - diff the new scrape against the June baseline.

Reimplements the two-layer logic of talabat_menu_tracker.run_delta so it works
on the flat JSONL the async scraper produces, and adds the guards the June run
lacked.

  Layer 1  hash gate   - price_hash and full_hash both match -> no_change,
                         no diffing at all. Most branches land here.
  Layer 2  deep diff   - keyed on item_key:
                           ADDED    in new, not in baseline
                           REMOVED  in baseline, not in new  (churn)
                           CHANGED  same key, different price / category /
                                    description (price compared with a 0.01
                                    AED tolerance so float noise is not a diff)

TWO GUARDS THE JUNE DELTA DID NOT HAVE:

1. PRICE OUTLIER FLAG. June recorded an average price change of +122.7%,
   driven by rows like "MIX KABAB 2 PERSON 1.0 -> 159.0 (+15,800%)" and
   "Two Layer Cake 4.0 -> 560.0 (+13,900%)". Those are placeholder prices in
   the old baseline being replaced, not real inflation. Any move beyond
   OUTLIER_PCT is marked `price_outlier=true` so it can be excluded from
   trend analytics instead of silently poisoning the average.

2. EMPTY-SCRAPE PROTECTION. If a branch returns 0 items, that is recorded as
   `emptied` - NOT as "every item removed". A transient empty page would
   otherwise manufacture a churn event for the whole menu. Real emptying is
   still visible, just labelled honestly.

`baseline_run_id` is written on every delta row. All 1.36M existing June rows
have it NULL, so it is impossible to tell what they were compared against.

    python compute_delta.py --dry-run
    python compute_delta.py
"""
import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def _argv(flag, default=None):
    """Read a flag before the module-level paths below are bound. argparse runs
    inside main(), far too late - these constants are already resolved."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    for a in sys.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


# --run <dir> points every path at a refresh directory, so a month's inputs and
# outputs stay together and two runs can never share a file. Bare invocation
# still reproduces the August/June comparison exactly.
RUN = _argv("--run")
SUB = _argv("--scrape", "scrape")          # 'smoke' for the trial slice
if RUN:
    RD = DATA / RUN
    BASELINE = RD / f"baseline_{RUN.split('_')[-1]}.json"
    NEW_ITEMS = RD / SUB / "menu_items.jsonl"
    NEW_STATUS = RD / SUB / "restaurant_status.jsonl"
    DELTA_OUT = RD / f"menu_deltas_{RUN.split('_')[-1]}.jsonl"
    REPORT = RD / f"delta_report_{RUN.split('_')[-1]}.json"
else:
    BASELINE = DATA / "baseline_june.json"
    NEW_ITEMS = DATA / "scrape" / "menu_items.jsonl"
    NEW_STATUS = DATA / "scrape" / "restaurant_status.jsonl"
    DELTA_OUT = DATA / "menu_deltas.jsonl"
    REPORT = DATA / "delta_report.json"

OUTLIER_PCT = 300.0        # beyond this a "price change" is almost certainly
                           # a placeholder being replaced, not a real move
PRICE_TOL = 0.01

# --------------------------------------------------------------------------
# COSMETIC-DIFF SUPPRESSION
#
# The June baseline in Postgres was cleaned AFTER it was scraped: something
# overwrote menu_category in place, stripping emoji and collapsing whitespace.
# Measured on the real data:
#     menu_category  JUNE 0 rows with emoji  vs  NEW 78,013
#     description    JUNE 0 double-spaces    vs  NEW 344 distinct values
# Both scrapers read `originalSection` untouched, so this is our own cleaning
# layer, not restaurants renaming sections. Comparing raw-vs-cleaned turns
# "Fresh Juices" -> "Fresh Juices [emoji]" into a change that never happened.
#
# So text fields are compared NORMALISED while the RAW new value is stored.
# Case is deliberately NOT folded: June kept 5,879 ALL-CAPS categories, so case
# was never normalised and a case difference is a real edit.
#
# item_key is NOT normalised here - it was left alone in June (measured: only
# 65 phantom add/remove pairs in 235,414, i.e. 0.03%), and normalising the join
# key would silently merge genuinely distinct items.
# --------------------------------------------------------------------------
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF"      # pictographs, emoticons, symbols
    "\U00002600-\U000027BF"       # misc symbols + dingbats
    "\U00002190-\U000021FF"       # arrows
    "\U00002B00-\U00002BFF"       # misc symbols and arrows
    "\U0000FE00-\U0000FE0F"       # variation selectors
    "\U0001F1E6-\U0001F1FF"       # regional indicators
    "‍⃣]"               # ZWJ, keycap
)
COSMETIC = Counter()


def norm_text(s: str) -> str:
    """Compare-only normalisation. Never stored - only used to decide whether
    two values differ for a reason a human would call a change."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", EMOJI.sub("", s)).strip()

sys.stdout.reconfigure(encoding="utf-8")


def compute_hash(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def diff_branch(base, new_items, run_id):
    """Returns (status, [delta rows])."""
    price_map = {i["item_key"]: i["price_aed"] for i in new_items
                 if i.get("price_aed") is not None}
    # sorted for the same reason as in build_baseline - item order is not
    # stable across requests, and an order-sensitive hash makes the gate useless
    full_map = sorted(
        ({"k": i["item_key"], "p": i.get("price_aed"),
          "c": i.get("category", ""), "d": i.get("description", "")}
         for i in new_items),
        key=lambda x: (x["k"], str(x["p"]), x["c"], x["d"]))
    if (compute_hash(price_map) == base["baseline_price_hash"]
            and compute_hash(full_map) == base["baseline_full_hash"]):
        return "no_change", []

    old_map, new_map = {}, {}
    for i in base["baseline_items"]:
        if i.get("item_key"):
            old_map[i["item_key"]] = i
    for i in new_items:
        if i.get("item_key"):
            new_map[i["item_key"]] = i

    bid = base["branch_id"]
    brid = base.get("baseline_run_id")
    rid = base.get("restaurant_id")
    out = []

    # THE COHORTS HAVE DIFFERENT WINDOWS. July's baseline is 2026-08-19,
    # August's 2026-08-12 - a week apart. A pooled "median price change" would
    # silently mix a 22-day move with a 29-day one, so every row carries the
    # window it was measured over and velocity is stated per 30 days.
    since = base.get("baseline_scraped_at")
    days = None
    if since:
        try:
            days = round((datetime.now(timezone.utc)
                          - datetime.fromisoformat(since)).total_seconds()
                         / 86400.0, 2)
        except ValueError:
            days = None
    cohort = base.get("cohort")

    def row(**kw):
        return {"branch_id": bid, "restaurant_id": rid, "run_id": run_id,
                "baseline_run_id": brid, "cohort": cohort,
                "days_since_baseline": days, "detected_at": now(), **kw}

    for k, i in new_map.items():
        if k not in old_map:
            out.append(row(change_type="ADDED", item_key=k,
                           item_name=i.get("item_name", ""),
                           menu_category=i.get("category", ""),
                           field_changed="new_item",
                           new_price_aed=i.get("price_aed")))
    for k, i in old_map.items():
        if k not in new_map:
            out.append(row(change_type="REMOVED", item_key=k,
                           item_name=i.get("item_name", ""),
                           menu_category=i.get("category", ""),
                           field_changed="removed_item",
                           old_price_aed=i.get("price_aed")))
    for k in old_map:
        if k not in new_map:
            continue
        o, n = old_map[k], new_map[k]
        op, np_ = o.get("price_aed"), n.get("price_aed")
        if op is not None and np_ is not None:
            if abs(float(op) - float(np_)) > PRICE_TOL:
                pct = ((np_ - op) / op * 100) if op else None
                out.append(row(change_type="CHANGED", item_key=k,
                               item_name=n.get("item_name", ""),
                               menu_category=n.get("category", ""),
                               field_changed="price",
                               old_price_aed=op, new_price_aed=np_,
                               price_change_pct=round(pct, 2) if pct is not None else None,
                               price_outlier=bool(pct is not None and abs(pct) > OUTLIER_PCT)))
        elif op != np_:
            out.append(row(change_type="CHANGED", item_key=k,
                           item_name=n.get("item_name", ""), field_changed="price",
                           old_price_aed=op, new_price_aed=np_))
        for fld, col in (("category", "menu_category"), ("description", "description")):
            ov, nv = str(o.get(fld) or "").strip(), str(n.get(fld) or "").strip()
            if ov != nv and norm_text(ov) == norm_text(nv):
                COSMETIC[col] += 1        # emoji/whitespace only - not a change
                continue
            if ov != nv:
                out.append(row(change_type="CHANGED", item_key=k,
                               item_name=n.get("item_name", ""),
                               menu_category=n.get("category", ""),
                               field_changed=col,
                               old_value=ov[:500], new_value=nv[:500]))
    # a branch that failed the hash gate but produced no rows really did not
    # change - report what the diff found, not what the gate guessed
    return ("changed" if out else "no_change"), out


def main(args):
    if not NEW_ITEMS.exists():
        print(f"  {NEW_ITEMS} not found - run the scrape first:")
        print("    python ../August_menu/scrape_menus.py --source data/scrape_targets.jsonl "
              "--outdir data/scrape --concurrency 10 --rate 2.0")
        return 1

    base = json.loads(BASELINE.read_text(encoding="utf-8"))

    # backfill restaurant_id for a baseline built before it was carried
    if any("restaurant_id" not in b for b in base.values()):
        tgt = DATA / "scrape_targets.jsonl"
        rid_by_branch = {}
        if tgt.exists():
            for line in open(tgt, encoding="utf-8"):
                if line.strip():
                    t = json.loads(line)
                    rid_by_branch[str(t["branch_id"])] = t.get("restaurant_id")
        for k, b in base.items():
            b.setdefault("restaurant_id", rid_by_branch.get(k))
        filled = sum(1 for b in base.values() if b.get("restaurant_id"))
        print(f"  restaurant_id backfilled from targets: {filled:,}/{len(base):,}")

    new_by_branch = defaultdict(list)
    for line in open(NEW_ITEMS, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            new_by_branch[str(r["branch_id"])].append(r)
    status = {}
    if NEW_STATUS.exists():
        for line in open(NEW_STATUS, encoding="utf-8"):
            if line.strip():
                s = json.loads(line)
                status[str(s["branch_id"])] = s.get("status")

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=" * 66)
    print(f"  MENU DELTA  run_id={run_id}")
    print("=" * 66)
    print(f"  baseline branches : {len(base):,}")
    print(f"  scraped branches  : {len(new_by_branch):,}")

    stats = Counter()
    empty_reason = Counter()
    deltas = []
    for bid, b in base.items():
        items = new_by_branch.get(bid)
        if not items:
            # A branch with zero items writes NO rows to menu_items.jsonl, so
            # new_by_branch has no key for it at all: "we never fetched it" and
            # "we fetched it and it returned nothing" both arrive here as None.
            # restaurant_status.jsonl is the only thing that separates them, and
            # the difference is the whole story - `not_scraped` says OUR
            # pipeline missed a branch, `emptied` says the RESTAURANT cleared
            # its menu. Reported as not_scraped, 62 successful scrapes looked
            # like 62 failures.
            st = status.get(bid)
            if st is None:
                stats["not_scraped"] += 1
            else:
                # still do NOT emit a REMOVED row per item: a transient empty
                # page would otherwise manufacture churn for an entire menu
                stats["emptied"] += 1
                empty_reason[st] += 1
            continue
        st, rows = diff_branch(b, items, run_id)
        stats[st] += 1
        deltas.extend(rows)

    by_type = Counter(d["change_type"] for d in deltas)
    outliers = sum(1 for d in deltas if d.get("price_outlier"))
    print(f"\n  branch outcomes:")
    for k in ("no_change", "changed", "emptied", "not_scraped"):
        if stats[k]:
            print(f"     {k:12} {stats[k]:>7,}"
                  + (f"   ({', '.join(f'{r}={n:,}' for r, n in empty_reason.most_common())})"
                     if k == "emptied" and empty_reason else ""))
    print(f"\n  delta rows: {len(deltas):,}")
    for k, v in by_type.most_common():
        print(f"     {k:9} {v:>9,}")
    fld = Counter(d["field_changed"] for d in deltas if d["change_type"] == "CHANGED")
    if fld:
        print(f"     CHANGED by field: {dict(fld)}")
    moves = [d["price_change_pct"] for d in deltas
             if d.get("price_change_pct") is not None and not d.get("price_outlier")]
    if moves:
        moves.sort()
        print(f"\n  price moves (outliers excluded): {len(moves):,}")
        print(f"     median {moves[len(moves)//2]:+.1f}%   "
              f"mean {sum(moves)/len(moves):+.1f}%")
    print(f"  price outliers flagged (>|{OUTLIER_PCT:.0f}%|): {outliers:,}"
          "   <- excluded from the averages above")
    if COSMETIC:
        print(f"\n  cosmetic diffs suppressed: {sum(COSMETIC.values()):,}")
        for k, v in COSMETIC.most_common():
            print(f"     {k:16} {v:>7,}")
        print("     (emoji/whitespace only - June's baseline was cleaned "
              "in place after scraping)")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(DELTA_OUT, "w", encoding="utf-8") as f:
        for d in deltas:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    REPORT.write_text(json.dumps({
        "run_id": run_id, "baseline_branches": len(base),
        "scraped_branches": len(new_by_branch),
        "branch_outcomes": dict(stats),
        "emptied_by_scrape_status": dict(empty_reason),
        "delta_rows": len(deltas),
        "by_change_type": dict(by_type),
        "changed_by_field": dict(fld),
        "price_outliers_flagged": outliers,
        "outlier_threshold_pct": OUTLIER_PCT,
        "cosmetic_diffs_suppressed": dict(COSMETIC),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {DELTA_OUT.name} ({len(deltas):,} rows)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--run-id")
    p.add_argument("--run", help="refresh directory under data/, e.g. "
                                 "refresh_202609 - consumed at import time")
    p.add_argument("--scrape", default="scrape",
                   help="subdirectory of the scrape output ('smoke' to trial)")
    sys.exit(main(p.parse_args()))
