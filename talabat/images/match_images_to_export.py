"""
Match Checker — DOWNLOADED images vs talabat_export.json
============================================================
Verifies that the images actually downloaded by download_images.py
correspond to the menu items IT already has in
talabat/export/talabat_export.json — not just "did we find a matching name
on the live page" (images_confirmed.jsonl, Phase 1's output — a URL being
found there doesn't guarantee the file download later succeeded), but "how
many of the EXISTING menu items IT is already shipping now have an actual
downloaded image file on disk" (images_manifest.jsonl, Phase 2's output —
only ever contains items that were successfully written to disk).

Why name-fuzzy-matching, not an ID join:
  talabat_export.json's menu_items[] never carries Talabat's raw numeric item
  ID (checked build_record() in export_to_json.py — only name/section/
  description/std_term/price/ingredients/is_popular are exported). The DB's
  talabat_menu_items.item_id column DOES have it, but that ID is unreliable
  across brands anyway: Starbucks's live item IDs (e.g. 3433004894) share ZERO
  overlap with its DB item_ids (e.g. 2294951490) for the exact same items —
  Talabat re-migrated Starbucks (and likely other large chains) through a
  separate "qikserve" backend at some point, reassigning every ID. Name
  matching, by contrast, worked well across every test case (Peet's Coffee
  89.5%, Starbucks 83% by name despite the ID mismatch, Häagen-Dazs 0% on a
  naive exact-string compare but 91.5% once Arabic script is stripped from
  both sides first — the live scrape's raw names are bilingual mashups like
  "1 Flavor Scoop  سكوب بوظة1" while the export's cleaned name is
  English-only, "1 Flavor Scoop 1").

Matching pipeline per restaurant (aligned by source_id, present in both
files):
  1. Normalize both sides: strip Arabic script, lowercase, collapse whitespace.
  2. Exact match on the normalized string (fast path, catches most items).
  3. Fuzzy fallback (rapidfuzz token_set_ratio, threshold 80 — empirically
     validated: genuine matches cluster at 82-100, genuine non-matches at
     60-75, e.g. "Fanta Orange" vs "Coca Cola" correctly scores 60.6 and is
     rejected rather than forced into a false match).

Output:
  image_match_summary.json    — per-restaurant AND aggregate coverage of
                                 "how many already-exported menu items now
                                 have a downloaded image," the metric that
                                 actually matters here (not raw scrape/
                                 download counts)
  image_match_unmatched.jsonl — EVERY unmatched export item (not just a
                                 sample), one row per gap, with the closest
                                 candidate found and its score — so gaps can
                                 actually be examined, not just counted

Run (only after download_images.py has finished — needs images_manifest.jsonl):
  python "talabat/images/match_images_to_export.py"                # full run
  python "talabat/images/match_images_to_export.py" --test         # test_images_manifest.jsonl only
"""

import argparse
import json
import re
from pathlib import Path

import orjson
from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
EXPORT_PATH = HERE.parent / "export" / "talabat_export.json"

FUZZY_THRESHOLD = 80
ARABIC_PATTERN = re.compile(r"[؀-ۿ]+")


def normalize(name):
    s = ARABIC_PATTERN.sub(" ", name or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def match_restaurant(export_items, downloaded_items):
    """Returns (matches, unmatched_export_items) where matches is a list of
    {export_name, downloaded_name, item_id, local_path, url, method, score}."""
    live_normalized = [(normalize(it["name"]), it) for it in downloaded_items]
    exact_index = {}
    for norm_name, it in live_normalized:
        exact_index.setdefault(norm_name, it)

    matches = []
    unmatched = []
    for exp_item in export_items:
        exp_norm = normalize(exp_item["name"])

        if exp_norm in exact_index:
            it = exact_index[exp_norm]
            matches.append({
                "export_name": exp_item["name"], "downloaded_name": it["name"],
                "item_id": it.get("item_id"), "local_path": it.get("local_path"), "url": it.get("url"),
                "method": "exact", "score": 100.0,
            })
            continue

        best_score, best_it = 0.0, None
        for norm_name, it in live_normalized:
            score = fuzz.token_set_ratio(exp_norm, norm_name)
            if score > best_score:
                best_score, best_it = score, it

        if best_score >= FUZZY_THRESHOLD:
            matches.append({
                "export_name": exp_item["name"], "downloaded_name": best_it["name"],
                "item_id": best_it.get("item_id"), "local_path": best_it.get("local_path"), "url": best_it.get("url"),
                "method": "fuzzy", "score": round(best_score, 1),
            })
        else:
            unmatched.append({"export_name": exp_item["name"], "best_score": round(best_score, 1), "closest_downloaded_name": best_it["name"] if best_it else None})

    return matches, unmatched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Use test_images_manifest.jsonl instead of the full images_manifest.jsonl")
    args = parser.parse_args()

    input_path = DATA_DIR / ("test_images_manifest.jsonl" if args.test else "images_manifest.jsonl")
    summary_path = DATA_DIR / ("test_image_match_summary.json" if args.test else "image_match_summary.json")
    unmatched_path = DATA_DIR / ("test_image_match_unmatched.jsonl" if args.test else "image_match_unmatched.jsonl")

    if not input_path.exists():
        print(f"ERROR: {input_path} does not exist — run download_images.py first (this checks actually-downloaded files, not just scraped URLs)")
        return

    export_data = orjson.loads(open(EXPORT_PATH, "rb").read())
    export_by_sid = {r["source_id"]: r["menu_items"] for r in export_data if "menu_items" in r}
    print(f"Loaded {len(export_by_sid):,} restaurants with menu_items from {EXPORT_PATH.name}")

    manifest_rows = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                manifest_rows.append(json.loads(line))
    print(f"Loaded {len(manifest_rows):,} restaurants with downloaded images from {input_path.name}")

    per_restaurant = []
    all_unmatched = []
    total_export_items = 0
    total_matched = 0
    total_exact = 0
    total_fuzzy = 0

    for r in manifest_rows:
        sid = r["source_id"]
        export_items = export_by_sid.get(sid)
        if export_items is None:
            per_restaurant.append({"source_id": sid, "restaurant_name": r["restaurant_name"], "status": "not_found_in_export"})
            continue

        downloaded_items = r.get("menu_items", [])
        matches, unmatched = match_restaurant(export_items, downloaded_items)

        exact_count = sum(1 for m in matches if m["method"] == "exact")
        fuzzy_count = sum(1 for m in matches if m["method"] == "fuzzy")
        coverage = len(matches) / len(export_items) * 100 if export_items else 0.0

        per_restaurant.append({
            "source_id": sid,
            "chain_id": r.get("chain_id"),
            "restaurant_name": r["restaurant_name"],
            "export_item_count": len(export_items),
            "downloaded_item_count": len(downloaded_items),
            "matched": len(matches),
            "matched_exact": exact_count,
            "matched_fuzzy": fuzzy_count,
            "unmatched_export_items": len(unmatched),
            "coverage_pct": round(coverage, 1),
        })

        for u in unmatched:
            all_unmatched.append({"source_id": sid, "chain_id": r.get("chain_id"), "restaurant_name": r["restaurant_name"], **u})

        total_export_items += len(export_items)
        total_matched += len(matches)
        total_exact += exact_count
        total_fuzzy += fuzzy_count

    with open(unmatched_path, "w", encoding="utf-8") as f:
        for u in all_unmatched:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")

    overall_coverage = total_matched / total_export_items * 100 if total_export_items else 0.0
    summary = {
        "restaurants_checked": len(manifest_rows),
        "total_export_menu_items": total_export_items,
        "total_matched": total_matched,
        "total_matched_exact": total_exact,
        "total_matched_fuzzy": total_fuzzy,
        "total_unmatched": len(all_unmatched),
        "overall_coverage_pct": round(overall_coverage, 1),
        "per_restaurant": per_restaurant,
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 65)
    print("MATCH SUMMARY")
    print(f"  Restaurants checked         : {len(manifest_rows):,}")
    print(f"  Existing export menu items  : {total_export_items:,}")
    print(f"  Matched (exact + fuzzy)     : {total_matched:,} ({overall_coverage:.1f}% coverage)")
    print(f"    - exact match             : {total_exact:,}")
    print(f"    - fuzzy match             : {total_fuzzy:,}")
    print(f"  Unmatched (no image found)  : {len(all_unmatched):,}")
    print(f"  Summary   -> {summary_path}")
    print(f"  Unmatched -> {unmatched_path} (every gap, for review)")
    print("=" * 65)


if __name__ == "__main__":
    main()
