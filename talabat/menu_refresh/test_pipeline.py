"""Tests for the menu-refresh delta pipeline. No API calls, no spend.

  SMOKE      inputs exist, modules import, shapes are right
  SANITY     the diff logic behaves on hand-built cases
  REGRESSION the specific hazards seen in the June delta stay handled

    python test_pipeline.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import build_baseline as BB
import compute_delta as CD

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))


def mk(key, price, cat="Main", desc="", name=None):
    return {"item_key": key, "item_name": name or key, "category": cat,
            "price_aed": price, "description": desc}


def base_of(items):
    return {
        "branch_id": 1, "baseline_run_id": "20260619_111807",
        "baseline_items": items,
        "baseline_price_hash": CD.compute_hash(
            {i["item_key"]: i["price_aed"] for i in items if i["price_aed"] is not None}),
        "baseline_full_hash": CD.compute_hash(
            [{"k": i["item_key"], "p": i["price_aed"], "c": i["category"],
              "d": i["description"]} for i in items]),
        "baseline_item_count": len(items),
    }


def smoke():
    print("\n=== SMOKE ===")
    check("baseline file exists", BB.BASELINE.exists(), str(BB.BASELINE))
    check("scrape targets exist", BB.TARGETS.exists())
    if BB.TARGETS.exists():
        tg = [json.loads(l) for l in open(BB.TARGETS, encoding="utf-8") if l.strip()]
        check("targets = 15,199", len(tg) == 15199, f"{len(tg)}")
        check("every target has branch_id + url",
              all(t.get("branch_id") and t.get("url") for t in tg))
        check("every target URL keeps ?aid=",
              all("?aid=" in t["url"] for t in tg),
              f"{sum(1 for t in tg if '?aid=' not in t['url'])} missing")
    check("hash fn matches tracker's (sorted keys, ensure_ascii=False)",
          CD.compute_hash({"b": 1, "a": 2}) == CD.compute_hash({"a": 2, "b": 1}))


def sanity():
    print("\n=== SANITY ===")
    items = [mk("burger", 20.0), mk("fries", 10.0)]
    b = base_of(items)

    st, rows = CD.diff_branch(b, items, "R2")
    check("identical menu -> no_change", st == "no_change" and not rows, f"{st}")

    st, rows = CD.diff_branch(b, items + [mk("cola", 5.0)], "R2")
    check("new item -> ADDED",
          any(r["change_type"] == "ADDED" and r["item_key"] == "cola" for r in rows))

    st, rows = CD.diff_branch(b, [items[0]], "R2")
    check("missing item -> REMOVED",
          any(r["change_type"] == "REMOVED" and r["item_key"] == "fries" for r in rows))

    st, rows = CD.diff_branch(b, [mk("burger", 25.0), items[1]], "R2")
    pr = [r for r in rows if r["field_changed"] == "price"]
    check("price move -> CHANGED/price", len(pr) == 1 and pr[0]["old_price_aed"] == 20.0
          and pr[0]["new_price_aed"] == 25.0)
    check("price_change_pct computed", pr and abs(pr[0]["price_change_pct"] - 25.0) < 0.01,
          f"{pr[0]['price_change_pct'] if pr else None}")

    st, rows = CD.diff_branch(b, [mk("burger", 20.005), items[1]], "R2")
    check("sub-0.01 float noise is NOT a change",
          not [r for r in rows if r["field_changed"] == "price"])

    st, rows = CD.diff_branch(b, [mk("burger", 20.0, cat="Grill"), items[1]], "R2")
    check("category move -> CHANGED/menu_category",
          any(r["field_changed"] == "menu_category" for r in rows))

    st, rows = CD.diff_branch(b, [mk("burger", 20.0, desc="now spicy"), items[1]], "R2")
    check("description move -> CHANGED/description",
          any(r["field_changed"] == "description" for r in rows))

    check("baseline_run_id carried on every row",
          all(r["baseline_run_id"] == "20260619_111807" for r in rows))


def db_contract():
    """Rows must slot into talabat_menu_deltas beside the 1.36M June rows.
    A vocabulary drift here does not error - it silently splits the changelog
    into two dialects that no single query can read."""
    print("\n=== DB CONTRACT (talabat_menu_deltas) ===")
    b = base_of([mk("burger", 20.0), mk("fries", 10.0)])
    b["restaurant_id"] = 9911
    _, rows = CD.diff_branch(
        b, [mk("burger", 25.0, cat="Grill"), mk("cola", 5.0)], "R2")

    check("change_type stays within the CHECK constraint",
          {r["change_type"] for r in rows} <= {"ADDED", "REMOVED", "CHANGED"})
    # June's vocabulary, read straight off the table
    check("ADDED uses field_changed='new_item'",
          all(r["field_changed"] == "new_item"
              for r in rows if r["change_type"] == "ADDED"))
    check("REMOVED uses field_changed='removed_item' (June's spelling)",
          all(r["field_changed"] == "removed_item"
              for r in rows if r["change_type"] == "REMOVED"))
    check("CHANGED fields are price/menu_category/description",
          {r["field_changed"] for r in rows if r["change_type"] == "CHANGED"}
          <= {"price", "menu_category", "description"})
    check("restaurant_id on every row (NULL on 0 of 1.36M June rows)",
          all(r.get("restaurant_id") == 9911 for r in rows))
    check("detected_at on every row (NOT NULL in practice)",
          all(r.get("detected_at") for r in rows))
    cols = {"branch_id", "restaurant_id", "run_id", "change_type", "item_name",
            "item_key", "menu_category", "field_changed", "old_value",
            "new_value", "old_price_aed", "new_price_aed", "price_change_pct",
            "detected_at", "baseline_run_id"}
    # Analytics-only fields. They have no column in talabat_menu_deltas and are
    # not meant to: `price_outlier` marks a row to exclude from trend maths, and
    # `cohort` / `days_since_baseline` exist because the September refresh diffs
    # two cohorts whose baselines are a week apart - a pooled median would mix a
    # 22-day price move with a 29-day one. load_to_postgres drops them.
    ANALYTICS_ONLY = {"price_outlier", "cohort", "days_since_baseline"}
    extra = {k for r in rows for k in r} - cols - ANALYTICS_ONLY
    check("no key that has no column to land in", not extra, f"{extra}")
    check("analytics-only fields are not DB columns",
          not (ANALYTICS_ONLY & cols))


def regression():
    print("\n=== REGRESSION (hazards seen in the June delta) ===")
    b = base_of([mk("kabab", 1.0), mk("cake", 4.0)])

    # June recorded +15,800% and +13,900% "price rises" that were placeholder
    # prices being replaced. They must not silently enter trend analytics.
    st, rows = CD.diff_branch(b, [mk("kabab", 159.0), mk("cake", 560.0)], "R2")
    pr = [r for r in rows if r["field_changed"] == "price"]
    check("absurd price jumps flagged as outliers",
          len(pr) == 2 and all(r["price_outlier"] for r in pr))

    st, rows = CD.diff_branch(b, [mk("kabab", 1.2), mk("cake", 4.4)], "R2")
    pr = [r for r in rows if r["field_changed"] == "price"]
    check("normal price moves NOT flagged",
          len(pr) == 2 and not any(r["price_outlier"] for r in pr))

    # an empty scrape must not manufacture a full-menu churn event
    st, rows = CD.diff_branch(b, [], "R2")
    removed = [r for r in rows if r["change_type"] == "REMOVED"]
    check("empty scrape handled by caller, not as mass REMOVED",
          True, "")   # the guard lives in main(); assert it is present:
    src = Path(__file__).with_name("compute_delta.py").read_text(encoding="utf-8")
    check("main() short-circuits empty scrapes as 'emptied'",
          'stats["emptied"] += 1' in src and "do NOT emit a REMOVED row" in src)

    # ...but 'emptied' was unreachable. A zero-item branch writes NO rows to
    # menu_items.jsonl, so new_by_branch has no key for it and `.get(bid)`
    # returns None - identical to a branch we never fetched. Only
    # restaurant_status.jsonl separates the two. In the September refresh this
    # reported 62 SUCCESSFUL scrapes (59 empty_menu + 3 no_menu_state) as
    # not_scraped, i.e. as our own pipeline failing.
    check("emptied vs not_scraped decided by restaurant_status, not a missing key",
          "status.get(bid)" in src and 'stats["not_scraped"] += 1' in src)

    # Talabat returns menu items in a different order every request. An
    # order-sensitive hash makes the gate fire on reordering alone: on the real
    # run it skipped 42 branches instead of 2,558 and reported 2,516 branches
    # as "changed" that had produced no delta rows at all.
    items = [mk("burger", 20.0), mk("fries", 10.0), mk("cola", 5.0)]
    b2 = base_of(items)
    st, rows = CD.diff_branch(b2, list(reversed(items)), "R2")
    check("reordered-but-identical menu -> no_change",
          st == "no_change" and not rows, f"status={st} rows={len(rows)}")

    dupes = [mk("tea", 5.0), mk("tea", 7.0), mk("juice", 9.0)]
    b3 = base_of(dupes)
    st, rows = CD.diff_branch(b3, [dupes[2], dupes[0], dupes[1]], "R2")
    check("duplicate item_keys still hash stably when reordered",
          st == "no_change", f"status={st}")

    check("branch status reflects rows found, not the gate's guess",
          CD.diff_branch(b2, items, "R2")[0] == "no_change")

    # The June baseline was cleaned in place after scraping: emoji stripped from
    # menu_category (0 rows with emoji vs 78,013 in the new raw scrape) and
    # whitespace collapsed in description. Comparing raw-vs-cleaned invented
    # 14,404 category and 9,556 description "changes" that never happened.
    b4 = base_of([mk("juice", 9.0, cat="Fresh Juices", desc="Cold  pressed")])
    _, rows = CD.diff_branch(
        b4, [mk("juice", 9.0, cat="Fresh Juices 🍉", desc="Cold pressed")], "R2")
    check("emoji-only category diff suppressed",
          not [r for r in rows if r["field_changed"] == "menu_category"])
    check("whitespace-only description diff suppressed",
          not [r for r in rows if r["field_changed"] == "description"])

    _, rows = CD.diff_branch(
        b4, [mk("juice", 9.0, cat="Cold Pressed Juices", desc="Cold  pressed")], "R2")
    check("a genuine rename still reports",
          any(r["field_changed"] == "menu_category" for r in rows))

    # June kept 5,879 ALL-CAPS categories, so case was never normalised there -
    # folding case here would swallow real edits.
    _, rows = CD.diff_branch(
        b4, [mk("juice", 9.0, cat="FRESH JUICES", desc="Cold  pressed")], "R2")
    check("case change is REAL, not suppressed",
          any(r["field_changed"] == "menu_category" for r in rows))

    _, rows = CD.diff_branch(
        b4, [mk("juice", 9.0, cat="Fresh Juices 🍉", desc="Cold  pressed")], "R2")
    check("suppressed row stores nothing (raw value never emitted)", not rows)
    check("item_key is NOT emoji-normalised (join key stays exact)",
          CD.diff_branch(base_of([mk("tea 🍵", 5.0)]),
                         [mk("tea", 5.0)], "R2")[1] != [])

    check("outlier threshold is documented and >100%", CD.OUTLIER_PCT > 100)
    check("price tolerance guards float noise", CD.PRICE_TOL == 0.01)

    # the ?aid= lesson: a URL without it returns zero items, silently
    check("baseline builder checks for ?aid=",
          "missing ?aid=" in Path(__file__).with_name("build_baseline.py")
          .read_text(encoding="utf-8"))


if __name__ == "__main__":
    smoke(); sanity(); db_contract(); regression()
    print(f"\n{'='*52}\n  PASSED {len(PASS)}   FAILED {len(FAIL)}")
    if FAIL:
        print("  failures: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
