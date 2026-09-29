"""Make every identically-named menu item carry one std_term and one ingredient
list, by adopting the LLM-reviewed verdict.

THE PROBLEM. Several distinct item_keys render as ONE displayed name once
Arabic, CJK and emoji are stripped, and they do not agree:

    'pepsi'              -> Fries + Pepsi     [july_export]   <- wrong
    'pepsi بيبسي'        -> Soft Drink        [llm_review]
    'pepsi 🥤'           -> Soft Drink        [llm_review]
        ... 9 keys, all displaying as "Pepsi", two std_terms

Measured across the July deliverable: 2,202 names, 157,107 rows (12.59%).

THE PATTERN IS CONSISTENT. In every conflict inspected, the bare English key
came from july_export and carried the WRONG term, while the bilingual variants
went through the review and got the right one - `coca cola` -> Chocolate Drink
vs `coca cola كوكا كولا` -> Soft Drink. The review only covered the 52,867 NEW
keys, so inherited errors like that were never seen.

THE RULE. Within a display-name group that disagrees, adopt the reviewed
verdict. Where several reviewed members disagree, take the one covering the most
menu rows. Groups with NO reviewed member are left exactly as they are - there
is nothing better to adopt, and inventing a winner would be guessing.

Writes a new mapping; the deliverable is rebuilt from it, so every value stays
keyed by item_key and the guarantee holds structurally.

    python resolve_collapsed_names.py --dry-run
    python resolve_collapsed_names.py
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))

from export_to_json import smart_title           # noqa: E402
from clean_menu_data import clean_item_key       # noqa: E402

IN_MAP = DATA / "std_term_mapping_reviewed.json"
OUT_MAP = DATA / "std_term_mapping_consistent.json"
ITEMS = DATA / "scrape" / "menu_items.jsonl"
REPORT = DATA / "collapsed_names_report.json"

sys.stdout.reconfigure(encoding="utf-8")


def display_name(key):
    k = clean_item_key(key) or key
    return smart_title(str(k).replace("_", " "))


def main(args):
    print("=" * 74)
    print("  RESOLVE COLLAPSED NAMES")
    print("=" * 74)

    mapping = json.loads(IN_MAP.read_text(encoding="utf-8"))
    print(f"  mapping keys : {len(mapping):,}")

    rows = Counter()
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows[json.loads(line)["item_key"]] += 1
    print(f"  menu rows    : {sum(rows.values()):,}")

    groups = defaultdict(list)
    for k, v in mapping.items():
        groups[display_name(k)].append(k)
    print(f"  display names: {len(groups):,}")

    def sig(v):
        return (v.get("std_term"),
                tuple(sorted(x.lower() for x in (v.get("ingredients") or []))))

    conflicted = {n: ks for n, ks in groups.items()
                  if len(ks) > 1 and len({sig(mapping[k]) for k in ks}) > 1}
    conflict_rows = sum(rows[k] for ks in conflicted.values() for k in ks)
    print(f"\n  names in conflict : {len(conflicted):,}  ({conflict_rows:,} rows)")

    changed_keys = 0
    changed_rows = 0
    no_verdict = 0
    no_verdict_rows = 0
    examples = []
    updates = {}

    for name, ks in conflicted.items():
        reviewed = [k for k in ks if mapping[k].get("method") == "llm_review"]
        if not reviewed:
            no_verdict += 1
            no_verdict_rows += sum(rows[k] for k in ks)
            continue
        # several reviewed members can still disagree - the one covering the
        # most menu rows is the best-evidenced answer, not an arbitrary pick
        by_sig = defaultdict(int)
        for k in reviewed:
            by_sig[sig(mapping[k])] += rows[k]
        win_sig = max(by_sig.items(), key=lambda t: t[1])[0]
        winner = next(k for k in reviewed if sig(mapping[k]) == win_sig)
        w = mapping[winner]

        for k in ks:
            if sig(mapping[k]) == win_sig:
                continue
            if len(examples) < 8:
                examples.append((name, k, mapping[k].get("std_term"),
                                 w.get("std_term"), rows[k]))
            updates[k] = {**mapping[k],
                          "std_term": w.get("std_term"),
                          "taxonomy": w.get("taxonomy"),
                          "ingredients": list(w.get("ingredients") or []),
                          "method": "collapsed_name_review"}
            changed_keys += 1
            changed_rows += rows[k]

    print(f"    resolvable from a verdict : {len(conflicted)-no_verdict:,}")
    print(f"    NO reviewed member        : {no_verdict:,}  "
          f"({no_verdict_rows:,} rows, left untouched)")
    print(f"\n  item_keys to change : {changed_keys:,}")
    print(f"  menu rows affected  : {changed_rows:,}")
    print("\n  examples:")
    for nm, k, old, new, n in examples:
        print(f"     {nm[:26]:28} key={k[:26]:28} {str(old)[:18]:20} -> "
              f"{str(new)[:18]:20} rows={n}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    mapping.update(updates)

    # prove the invariant now holds at mapping level
    still = 0
    for name, ks in groups.items():
        if len({sig(mapping[k]) for k in ks}) > 1:
            still += 1
    print(f"\n  display names still in conflict: {still:,}"
          f"   (all lack a reviewed verdict)" if still else "")

    OUT_MAP.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "names_in_conflict": len(conflicted),
        "resolved_from_verdict": len(conflicted) - no_verdict,
        "no_reviewed_member": no_verdict,
        "item_keys_changed": changed_keys,
        "menu_rows_affected": changed_rows,
        "names_still_conflicting": still,
    }, indent=2), encoding="utf-8")
    print(f"  -> {OUT_MAP.name}")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
