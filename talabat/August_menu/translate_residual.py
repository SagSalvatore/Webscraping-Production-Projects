"""Last text pass: translate the pure-Arabic strings, repair the one mojibake.

WHY THESE SURVIVED finalize_export.py. That pass strips Arabic from BILINGUAL
strings, where the English side carries the meaning on its own. These 103 are
pure Arabic - "مقابل مسجد - الشيخ زايد", "شارع الشيخ صقر بن خالد القاسمي" -
so stripping would blank the field, and finalize deliberately refuses to blank
a value to satisfy a cleanliness check. They have to be TRANSLATED instead,
which is what Sagar asked for.

Volume is tiny (about 80 distinct strings), so this costs well under a cent.

The one mojibake, "chefâ€TMs sauce", is double-encoded past what ftfy will
undo: U+2019 -> UTF-8 -> cp1252 -> the literal "â€TM". Handled by an explicit
rule rather than a model, because the correct output is not in doubt.

    python translate_residual.py --dry-run
    python translate_residual.py
"""
import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env", override=True)

EXPORT = HERE / "data" / "August_export.json"
CACHE = HERE / "data" / "arabic_location_translations.json"

MODEL = "gpt-4.1-mini"
BATCH = 25

ARABIC = re.compile("[؀-ۿﭐ-﷽ﹰ-﻿]")

# double-encoded U+2019 that ftfy will not undo
MOJIBAKE_FIXES = {"â€TM": "'", "â€™": "'", "â€œ": '"', "â€\x9d": '"',
                  "â€“": "-", "â€”": "-"}

SYSTEM = """You translate Arabic UAE location text into English.

Input lines: <id>|<arabic text>
Output one JSON object per line, nothing else:
  {"id": <id>, "en": "<english>"}

Rules:
- These are addresses, streets, landmarks and neighbourhood names in the UAE.
- Transliterate proper nouns the way they are normally written in UAE English
  usage (شارع الشيخ صقر -> "Sheikh Saqr Street", القطارة -> "Al Qattara").
- Translate descriptive words ("مقابل" -> "opposite", "خلف" -> "behind",
  "مسجد" -> "Mosque").
- Keep the " - " separators exactly where they are.
- Return English only. No Arabic characters in the output.
- If you cannot translate it, return "en": null. Do not guess.
"""

sys.stdout.reconfigure(encoding="utf-8")

FIELDS = ("raw", "city", "area", "sublocality")


def fix_mojibake(s):
    if not isinstance(s, str):
        return s
    out = s
    for bad, good in MOJIBAKE_FIXES.items():
        out = out.replace(bad, good)
    return out


def collect(d):
    vals = Counter()
    for r in d:
        L = r.get("location") or {}
        for k in FIELDS:
            v = L.get(k)
            if isinstance(v, str) and ARABIC.search(v):
                vals[v] += 1
        for it in r.get("menu_items") or []:
            for k in ("name", "section", "description", "std_term"):
                v = it.get(k)
                if isinstance(v, str) and ARABIC.search(v):
                    vals[v] += 1
            for g in it.get("ingredients") or []:
                if isinstance(g, str) and ARABIC.search(g):
                    vals[g] += 1
    return vals


async def run(todo, cache):
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
    sem = asyncio.Semaphore(6)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    async def one(batch):
        idx = {i: t for i, t in batch}
        payload = "\n".join(f"{i}|{t}" for i, t in batch)
        for attempt in range(4):
            try:
                async with sem:
                    r = await client.chat.completions.create(
                        model=MODEL, temperature=0, max_tokens=1600,
                        messages=[{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content": payload}])
                for line in (r.choices[0].message.content or "").splitlines():
                    line = line.strip().strip("`")
                    if not line.startswith("{"):
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    src, en = idx.get(rec.get("id")), rec.get("en")
                    # a translation that still contains Arabic is not a
                    # translation - reject rather than ship it
                    if src and en and not ARABIC.search(str(en)):
                        cache[src] = str(en).strip()
                return
            except Exception:
                await asyncio.sleep(2 ** attempt)

    await asyncio.gather(*(one(b) for b in batches))


def main(args):
    d = json.loads(EXPORT.read_text(encoding="utf-8"))
    vals = collect(d)
    print("=" * 68)
    print("  RESIDUAL ARABIC + MOJIBAKE")
    print("=" * 68)
    print(f"  strings containing Arabic : {sum(vals.values()):,} "
          f"({len(vals):,} distinct)")
    for v, c in vals.most_common(6):
        print(f"     x{c}  {v[:56]}")

    moji = sum(1 for r in d for it in r.get("menu_items") or []
               for g in it.get("ingredients") or []
               if isinstance(g, str) and any(b in g for b in MOJIBAKE_FIXES))
    print(f"  mojibake occurrences      : {moji}")

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [(i, v) for i, v in enumerate(vals) if v not in cache]
    print(f"  cached {len(cache):,} | to translate {len(todo):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing spent, nothing written")
        return 0

    if todo:
        asyncio.run(run(todo, cache))
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    got = sum(1 for v in vals if v in cache)
    print(f"  translated {got:,}/{len(vals):,}")

    n_tx = n_mj = 0

    def apply(obj, key):
        nonlocal n_tx, n_mj
        v = obj.get(key)
        if not isinstance(v, str) or not v:
            return
        f = fix_mojibake(v)
        if f != v:
            n_mj += 1
            v, obj[key] = f, f
        if ARABIC.search(v) and cache.get(v):
            obj[key] = cache[v]
            n_tx += 1

    for r in d:
        L = r.get("location") or {}
        for k in FIELDS:
            apply(L, k)
        for it in r.get("menu_items") or []:
            for k in ("name", "section", "description", "std_term"):
                apply(it, k)
            if it.get("ingredients"):
                new = []
                for g in it["ingredients"]:
                    g2 = fix_mojibake(g)
                    if g2 != g:
                        n_mj += 1
                    if ARABIC.search(g2) and cache.get(g2):
                        g2 = cache[g2]
                        n_tx += 1
                    new.append(g2)
                it["ingredients"] = new

    left = sum(vals[v] for v in vals if v not in cache)
    print(f"  applied: {n_tx:,} translations | {n_mj:,} mojibake repairs")
    print(f"  Arabic strings still present: {left:,}")

    tmp = EXPORT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    tmp.replace(EXPORT)
    print(f"\n  -> {EXPORT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(main(p.parse_args()))
