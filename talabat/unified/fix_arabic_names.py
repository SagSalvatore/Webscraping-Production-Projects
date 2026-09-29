"""D3 - recover the item names and sections that scrub() emptied to NULL.

THE DEFECT. build_july_update.py:107 ends scrub() with `out = out or None`.
scrub strips Arabic and CJK per the no-Arabic rule, so a name written ENTIRELY
in Arabic leaves an empty string and becomes None. 140 item names and 194
sections in July_menu_update.json are null for this reason; talabat_export.json
has zero, because it never scrubbed.

WHY NOT JUST DROP THEM. They are real menu items, not junk:

    بيبسي                  = Pepsi
    توشكا                  = Toshka
    كيكة جوز الهند الطازجة  = Fresh Coconut Cake

Dropping real products over a formatting rule loses genuine data. They are
translated instead, with brand names transliterated - the same treatment the
August deliverable's Arabic text received.

HOW THE ORIGINAL IS RECOVERED. The Arabic is gone from the update, so it is read
back from the scrape (menu_items.jsonl) and re-derived through the SAME
functions the builder used, so the match is exact rather than approximate:
a scrape row belongs to a null name when scrub(smart_title(key)) is None for it.
Ambiguity (two Arabic-only items at one price in one restaurant) is reported and
skipped, never guessed.

WRITES A SIDECAR. July_menu_update.json is already with Tech; patching it in
place would leave two different files sharing one name. The builder consumes
the sidecar instead, and stays deterministic and API-free.

    python fix_arabic_names.py --dry-run     # find + report, no API spend
    python fix_arabic_names.py
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson
from dotenv import load_dotenv
from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env", override=True)

sys.path.insert(0, str(ROOT / "export"))
sys.path.insert(0, str(ROOT / "menu"))
sys.path.insert(0, str(ROOT / "menu_refresh"))
sys.path.insert(0, str(ROOT / "August_menu"))

from export_to_json import smart_title                    # noqa: E402
from clean_menu_data import clean_item_key, clean_text    # noqa: E402
from build_july_update import scrub                       # noqa: E402
from translate_arabic import (                            # noqa: E402
    NONLATIN_RX, STRICT_PROMPT, MODEL, CACHE)

UPDATE = ROOT / "menu_refresh" / "data" / "July_menu_update.json"
SCRAPE = ROOT / "menu_refresh" / "data" / "scrape" / "menu_items.jsonl"
OUT = DATA / "arabic_fixes.json"

BATCH = 30
sys.stdout.reconfigure(encoding="utf-8")


def derive(key):
    """Exactly what build_july_update does to produce `name` / `section`."""
    return scrub(smart_title(str(key or "").replace("_", " ")))


async def translate(strings, cache):
    """Translate the distinct strings not already cached. STRICT prompt only -
    [SKIP] is not an acceptable answer here, an untranslated name is useless."""
    todo = [s for s in strings if s not in cache]
    if not todo:
        print("    every string already in the translation cache")
        return {}, Counter()
    # this repo's .env names it OPEN_AI_API; accept the standard name too
    key = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("no OpenAI key: set OPEN_AI_API in talabat/.env")
    client = AsyncOpenAI(api_key=key)
    stats = Counter()
    got = {}
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        payload = "\n".join(f"{n}|||{s}" for n, s in enumerate(chunk))
        for attempt in range(4):
            try:
                resp = await client.chat.completions.create(
                    model=MODEL, temperature=0, max_tokens=1500,
                    messages=[{"role": "system", "content": STRICT_PROMPT},
                              {"role": "user", "content": payload}])
                for line in (resp.choices[0].message.content or "").splitlines():
                    if "|||" not in line:
                        continue
                    idx_s, _, val = line.partition("|||")
                    try:
                        src = chunk[int(idx_s.strip())]
                    except (ValueError, IndexError):
                        continue
                    val = val.strip()
                    if not val or val == "[SKIP]":
                        stats["refused"] += 1
                        continue
                    # the prompt is an instruction, not a guarantee - verify
                    if NONLATIN_RX.search(val):
                        stats["still_nonlatin"] += 1
                        continue
                    got[src] = val
                    stats["translated"] += 1
                break
            except Exception as exc:
                stats["api_error"] += 1
                print(f"    batch {i//BATCH} failed ({type(exc).__name__}), "
                      f"attempt {attempt+1}")
                await asyncio.sleep(2 * (attempt + 1))
    return got, stats


def main(args):
    print("=" * 74)
    print("  RECOVER NULL item names / sections  (Arabic-only, emptied by scrub)")
    print("=" * 74)

    # ---- 1. locate the nulls, by (source_id, item index) ------------------
    holes = {}
    per_branch = defaultdict(list)
    n_rec = n_item = 0
    for rec in ijson.items(open(UPDATE, "rb"), "item", use_float=True):
        n_rec += 1
        sid = str(rec["source_id"])
        for idx, it in enumerate(rec.get("menu_items") or []):
            n_item += 1
            miss = [f for f in ("name", "section") if it.get(f) is None]
            if miss:
                holes[f"{sid}:{idx}"] = {"source_id": sid, "index": idx,
                                         "missing": miss,
                                         "price": it.get("price"),
                                         "std_term": it.get("std_term"),
                                         "name": it.get("name"),
                                         "section": it.get("section")}
                per_branch[sid].append(f"{sid}:{idx}")
    print(f"  update: {n_rec:,} records | {n_item:,} items")
    print(f"  items with a null name or section : {len(holes):,}"
          f"  across {len(per_branch):,} restaurants")
    miss_n = sum(1 for h in holes.values() if "name" in h["missing"])
    miss_s = sum(1 for h in holes.values() if "section" in h["missing"])
    print(f"    null name    : {miss_n:,}")
    print(f"    null section : {miss_s:,}")

    # ---- 2. recover the Arabic from the scrape ---------------------------
    cand = defaultdict(list)     # source_id -> [(price, raw_key, raw_cat)]
    for line in open(SCRAPE, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        sid = str(r.get("branch_id"))
        if sid not in per_branch:
            continue
        key = (clean_item_key(r.get("item_key"))
               or clean_text(r.get("item_name") or "")
               or r.get("item_key"))
        cand[sid].append({"price": r.get("price_aed"),
                          "key": key, "raw_key": r.get("item_key"),
                          "cat": r.get("category"),
                          "name_out": derive(key),
                          "sect_out": derive(r.get("category"))})

    def match_rows(h, rows):
        """Narrow to the scrape row(s) this item came from.

        Every surviving field constrains the match, not just the missing one.
        Matching a null SECTION on "rows whose section scrubs to None" alone
        collides on every Arabic-sectioned row in the restaurant - but the
        item's own name and price identify it exactly.
        """
        c = rows
        if h["price"] is not None:
            c = [r for r in c if r["price"] == h["price"]]
        if h["name"] is not None:
            c = [r for r in c if r["name_out"] == h["name"]]
        else:
            c = [r for r in c if r["name_out"] is None]
        if h["section"] is not None:
            c = [r for r in c if r["sect_out"] == h["section"]]
        else:
            c = [r for r in c if r["sect_out"] is None]
        return c

    resolved, ambiguous, unmatched = {}, [], []
    need_strings = set()
    for k, h in holes.items():
        hits = match_rows(h, cand.get(h["source_id"], []))
        if not hits:
            unmatched.append((k, ",".join(h["missing"])))
            continue
        for field in h["missing"]:
            col = "raw_key" if field == "name" else "cat"
            uniq = {r[col] for r in hits if r[col]}
            if len(uniq) != 1:
                # several DIFFERENT source strings fit - guessing would be
                # worse than leaving it null, so report and skip
                ambiguous.append((k, field, len(uniq)))
                continue
            src = uniq.pop()
            resolved.setdefault(k, {})[field] = src
            need_strings.add(src)

    print(f"\n  recovered originals   : {len(resolved):,}")
    print(f"  ambiguous (skipped)   : {len(ambiguous):,}")
    print(f"  no scrape row matched : {len(unmatched):,}")
    print(f"  distinct strings to translate: {len(need_strings):,}")
    for k, v in list(resolved.items())[:6]:
        print(f"     {k:16} {v}")

    if args.dry_run:
        print("\n  --dry-run: no API call, nothing written")
        return 0
    if not need_strings:
        print("\n  nothing to translate")
        return 0

    # ---- 3. translate ----------------------------------------------------
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    print(f"\n  translation cache: {len(cache):,} entries")
    got, stats = asyncio.run(translate(sorted(need_strings), cache))
    cache.update(got)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"  {dict(stats)}")

    # ---- 4. sidecar ------------------------------------------------------
    fixes, failed = {}, []
    for k, fields in resolved.items():
        out = {}
        for field, src in fields.items():
            val = cache.get(src)
            if val:
                out[field] = smart_title(val)
            else:
                failed.append((k, field, src))
        if out:
            fixes[k] = out
    OUT.write_text(json.dumps({
        "generated_for": "July_menu_update.json",
        "reason": "scrub() emptied all-Arabic strings to None "
                  "(build_july_update.py:107)",
        "fixes": fixes,
        "ambiguous": [list(a) for a in ambiguous],
        "unmatched": [list(u) for u in unmatched],
        "untranslated": [list(f) for f in failed],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  fixes written : {len(fixes):,}")
    print(f"  untranslated  : {len(failed):,}")
    for k, v in list(fixes.items())[:8]:
        print(f"     {k:16} {v}")
    print(f"  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
