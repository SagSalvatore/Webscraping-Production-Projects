"""July-cohort menu refresh -> July_menu_update.json.

Per Sagar: Tech does NOT need the whole restaurant record again for these - they
already hold it. This file carries source_id plus the refreshed menu, in exactly
the menu_items[] shape of talabat_export.json, so it drops straight into their
existing parser.

Built entirely from files in menu_refresh/data. Nothing is read from Postgres.

    scrape/menu_items.jsonl            1,246,162 refreshed items, 15,119 branches
    std_term_mapping_august.json       item_key -> std_term / taxonomy / ingredients
    std_term_canonical_ingredients.json  std_term -> class ingredient profile

VERIFIED BEFORE BUILDING (verify_july_mapping.py):
  * 310,061 item_keys that Tech already has come back with the IDENTICAL
    std_term AND identical ingredients - 0 genuine relabels.
  * The only 113 differences are keys July shipped with std_term=null; those now
    carry a value. A fix, not drift.
  * 52,754 genuinely new item_keys, resolved by ndjson / excel-exact / kNN.

The 80 delisted branches are NOT in this file - Tech keeps their July menus,
per management.

Text goes through the same treatment as the August deliverable: smart_title,
the shipped cleaners, emoji/mojibake/Arabic removed.

    python build_july_update.py --dry-run
    python build_july_update.py
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))

from export_to_json import is_popular, smart_title        # noqa: E402
from clean_menu_data import (                             # noqa: E402
    clean_item_key, clean_text, extract_english_from_bilingual)

try:
    from ftfy import fix_text
except ImportError:
    def fix_text(s):
        return s

ITEMS = DATA / "scrape" / "menu_items.jsonl"
MAPPING = DATA / "std_term_mapping_august.json"
PROFILES = DATA / "std_term_canonical_ingredients.json"
OUT = DATA / "July_menu_update.json"
REPORT = DATA / "July_menu_update_report.json"

# same two terms August hit: no learned ingredient profile, and the items behind
# them are set menus or non-food. Resolved the same way, for consistency.
TERM_FIX = {"Breakfast Pastry Set": "Combo", "Churchkhella": "Combo"}

ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]+")
CJK = re.compile("[一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]+")
INVIS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f­​-‏‪-‮⁠-⁤﻿￹-￻]+")
WS = re.compile(r"\s+")
ORPHAN = re.compile(r"(\s*[-,]\s*){2,}")
EDGE = re.compile(r"^[\s\-,]+|[\s\-,]+$")
MOJI_FIX = {"â€TM": "'", "â€™": "'", "â€œ": '"', "â€\x9d": '"',
            "â€“": "-", "â€”": "-"}
# Matched CASE-INSENSITIVELY: smart_title runs before scrub and lowercases the
# text, so "â€TM" arrives as "â€tms" and a case-sensitive key never fires.
MOJI_RX = [(re.compile(re.escape(b), re.I), g) for b, g in MOJI_FIX.items()]

sys.stdout.reconfigure(encoding="utf-8")

# ftfy.fix_text is the expensive step and almost nothing needs it. Only call it
# when the string actually contains something it could repair - mojibake bytes,
# typographic quotes, or non-ASCII. On 1.25M items x ~4 fields that is the
# difference between ~20 minutes and a couple.
# Non-ASCII OR an HTML entity. The first version tested only for non-ASCII and
# silently skipped "Mushrooms &amp; Grilled" - pure ASCII, but ftfy decodes the
# entity. That fast-path traded correctness for speed on exactly those strings.
SUSPECT = re.compile(r"[^ -~]|&(?:[A-Za-z]{2,8}|#\d{2,5});")
_CACHE = {}


def scrub(s):
    """Same normalisation the August deliverable went through."""
    if not isinstance(s, str) or not s:
        return s
    if not SUSPECT.search(s):
        return s                      # pure ASCII: nothing here to fix
    hit = _CACHE.get(s)
    if hit is not None:
        return hit
    src = s
    out = s
    for rx, good in MOJI_RX:
        out = rx.sub(good, out)
    out = fix_text(unicodedata.normalize("NFKC", out))
    out = INVIS.sub("", out)
    out = ARABIC.sub(" ", out)
    out = CJK.sub(" ", out)
    out = EDGE.sub("", WS.sub(" ", ORPHAN.sub(" - ", out))).strip()
    out = out or None
    if len(_CACHE) < 400000:
        _CACHE[src] = out
    return out


def main(args):
    print("=" * 74)
    print("  BUILD July_menu_update.json")
    print("=" * 74)

    map_path = Path(args.mapping) if getattr(args, "mapping", None) else MAPPING
    print(f"  mapping source: {map_path.name}")
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    print(f"  mapping keys {len(mapping):,} | std_term profiles {len(profiles):,}")

    by_branch = defaultdict(list)
    n = 0
    with open(ITEMS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                by_branch[r["branch_id"]].append(r)
                n += 1
    print(f"  refreshed items {n:,} over {len(by_branch):,} branches")

    stats = Counter()
    out = []
    for bid, rows in by_branch.items():
        items = []
        for r in rows:
            k = r["item_key"]
            m = mapping.get(k) or {}
            term = m.get("std_term")
            if term in TERM_FIX:
                term = TERM_FIX[term]
                stats["std_term_remapped"] += 1

            ings = m.get("ingredients") or []
            # A REVIEWED key's empty list is a decision, not a gap. 5,378 keys
            # come back as "not applicable - non-menu item" (party candles, salt
            # lamps, flowers); refilling those from the class profile would put
            # 'salt' in a candle's ingredients and silently overrule the
            # reviewer. Only unreviewed keys get the profile fallback.
            reviewed = m.get("method") == "llm_review"
            if not ings and reviewed:
                stats["reviewed_empty_respected"] += 1
            elif not ings:
                ings = profiles.get(term) or []
                if ings:
                    stats["ingredients_from_profile"] += 1
                else:
                    stats["ingredients_still_empty"] += 1

            raw_cat = r.get("category") or ""
            key = clean_item_key(k) or clean_text(r.get("item_name") or "") or k
            sec = clean_text(raw_cat)
            desc = extract_english_from_bilingual(r.get("description") or "")

            items.append({
                "name": scrub(smart_title(str(key).replace("_", " "))),
                "section": scrub(smart_title(str(sec or "").replace("_", " ")))
                           if sec else "",
                "description": scrub(smart_title(desc)) if desc else None,
                "std_term": scrub(smart_title(term)) if term else None,
                "price": r.get("price_aed"),
                "ingredients": [x for x in (scrub(i) for i in ings) if x],
                "is_popular": is_popular(raw_cat),
            })
        # Talabat lists the same dish twice in a section often enough to matter:
        # 1,689 exact (name, section, price) repeats across the cohort. Keep the
        # first; a repeat carries no information and reads as a data error.
        seen = set()
        deduped = []
        for it in items:
            sig = (it["name"], it["section"], it["price"])
            if sig in seen:
                stats["duplicate_item_dropped"] += 1
                continue
            seen.add(sig)
            deduped.append(it)
        out.append({"source_name": "talabat", "source_id": str(bid),
                    "menu_items": deduped})
        if len(out) % 2000 == 0:
            print(f"    {len(out):,}/{len(by_branch):,} branches", flush=True)

    total = sum(len(r["menu_items"]) for r in out)
    print(f"\n  --- BUILT ---")
    print(f"    restaurants  : {len(out):,}")
    print(f"    menu entries : {total:,}")
    for k, v in stats.most_common():
        print(f"    {k:28} {v:>8,}")

    # integrity
    ids = [r["source_id"] for r in out]
    assert len(set(ids)) == len(ids), "duplicate source_id"
    no_std = sum(1 for r in out for i in r["menu_items"] if not i["std_term"])
    no_ing = sum(1 for r in out for i in r["menu_items"] if not i["ingredients"])
    no_price = sum(1 for r in out for i in r["menu_items"] if i["price"] is None)
    empty = sum(1 for r in out if not r["menu_items"])
    print(f"\n  --- INTEGRITY ---")
    print(f"    duplicate source_id        : 0")
    print(f"    items without std_term     : {no_std:,}")
    print(f"    items without ingredients  : {no_ing:,}")
    print(f"    items without price        : {no_price:,}")
    print(f"    restaurants with no menu   : {empty:,}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    tmp.replace(OUT)
    REPORT.write_text(json.dumps({
        "restaurants": len(out), "menu_entries": total,
        "stats": dict(stats),
        "items_without_std_term": no_std,
        "items_without_ingredients": no_ing,
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT.name} ({OUT.stat().st_size/1e6:.0f} MB)")
    print(f"  -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--mapping", help="override the std_term mapping "
                   "(e.g. std_term_mapping_reviewed.json after the LLM review)")
    sys.exit(main(p.parse_args()))
