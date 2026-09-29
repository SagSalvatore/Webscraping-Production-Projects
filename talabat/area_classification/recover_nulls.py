"""Recover the rows the main pass left null, cheapest source first.

2,403 rows had no confident taxonomy match. Three signals we already hold or can
get, in cost order - each tier only sees what the previous could not resolve:

  1  URL SLUG        free. Talabat's own slug ENDS with the area:
                     'marrybrown-muwaileh-commercial' -> Muwaileh Commercial
                     'shababeek-al-khan-sharjah'      -> Al Khan
                     Coverage measured: 2,049 / 2,049 source_ids have one.
  2  FULL ADDRESS    free. unified location.raw carries the WHOLE address, while
                     Tech's file only has the truncated `area` fragment:
                       Tech : 'Maleha St - inside Emarat Gas Station'
                       raw  : 'Hoshi Area - Maleha St - ... - Sharjah - UAE'
                     Same matcher, far more to match against. 2,049 / 2,049.
  3  SERPER + LLM    paid, last. Query '{name} uae restaurant' - the template
                     measured 15/15 in the classification pipeline - then the
                     LLM picks FROM the taxonomy candidates. Still a closed set,
                     so it cannot invent an area.

Anything all three miss stays null.

THE SLUG IS MATCHED FROM THE END. Talabat builds it as {brand}-{area}, so the
tail is the area and the head is the restaurant name. Matching the longest
trailing run of tokens that hits the taxonomy avoids 'noon-kabab' matching
something on the brand half.

    python recover_nulls.py --dry-run      tiers 1-2 only, no spend
    python recover_nulls.py --limit 50     small live test of tier 3
    python recover_nulls.py
"""
import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter

import orjson
from dotenv import load_dotenv
from openai import AsyncOpenAI
from rapidfuzz import fuzz, process

import config as C
from classify_to_taxonomy import PROMPT, candidates
from taxonomy import build_index, key, load, match

load_dotenv(C.ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")

TAXONOMY = C.HERE / "area_list.csv"
ASSIGN = C.DATA / "taxonomy_assignments.json"
OUT = C.DATA / "recovered_nulls.json"
SERPER_CACHE = C.CACHE / "recover_serper.json"
LLM_CACHE = C.CACHE / "recover_llm.json"

CRAWLS = [C.ROOT / "data" / "urls" / "talabat_restaurant_urls.jsonl",
          C.ROOT / "data" / "urls" / "talabat_restaurant_urls_run2.jsonl"]


def slug_to_area(slug, index, areas):
    """Longest TRAILING token run that is a taxonomy area.

    The slug is {brand}-{area}, so scanning from the end finds the area and
    never matches on the brand half. Longest-first so
    'downtown-burj-khalifa' beats a shorter tail.
    """
    if not slug:
        return None
    toks = [t for t in re.split(r"[-_/]+", str(slug).lower()) if t]
    for start in range(len(toks)):
        cand = " ".join(toks[start:])
        k = key(cand)
        if k and k in index:
            return index[k]
    return None


async def run_llm(items, cache, model, batch, concurrency):
    api = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY")
    if not api:
        raise SystemExit("no OpenAI key: set OPEN_AI_API in talabat/.env")
    client = AsyncOpenAI(api_key=api)
    sem = asyncio.Semaphore(concurrency)
    todo = [i for i in items if i["ck"] not in cache]
    print(f"    llm: cached {len(items)-len(todo):,} | to classify {len(todo):,}")
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    st = Counter()

    async def one(chunk):
        lines = []
        for n, it in enumerate(chunk):
            opts = " ".join(f"[{j+1}] {a}" for j, a in enumerate(it["cands"]))
            lines.append(f"{n}|||RAW={it['evidence'][:110]}|||CITY={it['city']}|||"
                         f"HINT={it['hint'] or '-'}|||OPTIONS={opts}")
        for attempt in range(4):
            try:
                async with sem:
                    resp = await client.chat.completions.create(
                        model=model, temperature=0, max_tokens=900,
                        messages=[{"role": "system", "content": PROMPT},
                                  {"role": "user", "content": "\n".join(lines)}])
                for line in (resp.choices[0].message.content or "").splitlines():
                    p = line.split("|||")
                    if len(p) < 2:
                        continue
                    try:
                        it = chunk[int(p[0].strip())]
                    except (ValueError, IndexError):
                        continue
                    ans = p[1].strip().upper()
                    if ans == "NONE":
                        cache[it["ck"]] = None
                        st["none"] += 1
                        continue
                    try:
                        cache[it["ck"]] = it["cands"][int(ans.strip("[]. ")) - 1]
                        st["chosen"] += 1
                    except (ValueError, IndexError):
                        cache[it["ck"]] = None
                        st["bad_index"] += 1
                return
            except Exception:
                st["api_error"] += 1
                await asyncio.sleep(2 * (attempt + 1))
        st["batch_failed"] += 1

    for i in range(0, len(batches), concurrency):
        await asyncio.gather(*(one(b) for b in batches[i:i + concurrency]))
        LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                             encoding="utf-8")
        print(f"    llm {min(i+concurrency, len(batches))}/{len(batches)}",
              flush=True)
    return st


async def run_serper(rows, cache, concurrency=8):
    import httpx
    key_ = os.environ.get("SERPER_API_KEY")
    if not key_:
        raise SystemExit("SERPER_API_KEY missing in talabat/.env")
    todo = [r for r in rows if r["_q"] not in cache]
    print(f"    serper: cached {len(rows)-len(todo):,} | to fetch {len(todo):,}")
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30) as client:
        async def one(r):
            async with sem:
                for attempt in range(3):
                    try:
                        resp = await client.post(
                            C.SERPER_ENDPOINT,
                            headers={"X-API-KEY": key_,
                                     "Content-Type": "application/json"},
                            json={"q": r["_q"], "gl": "ae", "num": 5})
                        if resp.status_code == 429:
                            await asyncio.sleep(3 * (attempt + 1))
                            continue
                        d = resp.json()
                        s = []
                        for k in ("places", "organic", "knowledgeGraph"):
                            v = d.get(k)
                            if isinstance(v, list):
                                for x in v[:4]:
                                    s.append(" ".join(filter(None, [
                                        x.get("title"), x.get("address"),
                                        x.get("snippet")])))
                            elif isinstance(v, dict):
                                s.append(" ".join(filter(None, [
                                    v.get("title"), v.get("address")])))
                        cache[r["_q"]] = " | ".join(x for x in s if x)[:900]
                        return
                    except Exception:
                        await asyncio.sleep(2 * (attempt + 1))
                cache[r["_q"]] = ""
        for i in range(0, len(todo), 40):
            await asyncio.gather(*(one(r) for r in todo[i:i + 40]))
            SERPER_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                                    encoding="utf-8")
            print(f"    serper {min(i+40, len(todo)):,}/{len(todo):,}", flush=True)


def main(args):
    print("=" * 78)
    print("  RECOVER NULLS  (slug -> full address -> serper+llm -> null)")
    print("=" * 78)

    areas, TAX = load(TAXONOMY)
    index = build_index(areas)
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    assign = {int(k): v for k, v in
              json.loads(ASSIGN.read_text(encoding="utf-8")).items()}
    todo = [i for i in range(len(src)) if i not in assign]
    print(f"  null rows: {len(todo):,}")

    # ---- signals ---------------------------------------------------------
    slugs, urls = {}, {}
    for p in CRAWLS:
        if not p.exists():
            continue
        for line in open(p, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                b = str(r.get("branch_id"))
                if r.get("branch_slug"):
                    slugs.setdefault(b, r["branch_slug"])
    raws = {}
    for line in open(C.UNIFIED, "rb"):
        r = orjson.loads(line)
        b = str(r["source_id"])
        v = (r.get("location") or {}).get("raw")
        if v:
            raws.setdefault(b, v)
    print(f"  signals: slugs {len(slugs):,} | full addresses {len(raws):,}")

    # EVERY signal here is keyed by source_id - the slug, the unified address,
    # and a name-based Serper query all describe the RESTAURANT, not the branch.
    # A source_id with several rows is a chain whose branches sit in different
    # areas, so applying any of them would give every branch the same answer.
    # Measured when unguarded: 47 of 342 collapsed onto one area, and the
    # originals plainly differed -
    #     The Spectre  'Madinat Khalifa - A' + 'Rabdan - RB11' -> both the first
    # For those rows the only per-BRANCH evidence is their own area text, which
    # the main pass already tried. So they are left null rather than smeared.
    rows_per_sid = Counter(str(r.get("source_id")) for r in src)

    recovered, st = {}, Counter()
    remaining = []
    for i in todo:
        rec = src[i]
        sid = str(rec.get("source_id"))
        loc = rec.get("location") or {}

        if rows_per_sid[sid] > 1:
            st["0_multi_row_skipped"] += 1
            continue

        # TIER 1 - slug
        got = slug_to_area(slugs.get(sid), index, areas)
        if got:
            recovered[i] = got
            st["1_slug"] += 1
            continue
        # TIER 2 - full address
        full = raws.get(sid)
        if full:
            got, how = match(full, index, TAX)
            if got:
                recovered[i] = got
                st["2_full_address"] += 1
                continue
        st["3_needs_serper"] += 1
        remaining.append({
            "i": i, "sid": sid, "name": (rec.get("name") or "").strip(),
            "city": loc.get("city"),
            "hint": (loc.get("area") or "").strip() or None,
            "evidence": full or (loc.get("area") or ""),
            "_q": C.SERPER_QUERY.format(name=(rec.get("name") or "").strip()),
        })

    print(f"\n  {'TIER':22}{'ROWS':>9}")
    for k in sorted(st):
        print(f"    {k:22}{st[k]:>9,}   ({st[k]/len(todo)*100:5.1f}%)")
    free = st["1_slug"] + st["2_full_address"]
    print(f"\n  FREE recovery {free:,} of {len(todo):,} ({free/len(todo)*100:.1f}%)")
    print(f"  to Serper+LLM {len(remaining):,}"
          f"  ({len({r['_q'] for r in remaining}):,} distinct queries)")

    if args.dry_run:
        print("\n  --dry-run: tiers 1-2 only, nothing written")
        return 0

    if remaining:
        if args.limit:
            remaining = remaining[: args.limit]
            print(f"  --limit {len(remaining):,}")
        sc = json.loads(SERPER_CACHE.read_text(encoding="utf-8")) \
            if SERPER_CACHE.exists() else {}
        asyncio.run(run_serper(remaining, sc))
        SERPER_CACHE.write_text(json.dumps(sc, ensure_ascii=False),
                                encoding="utf-8")

        items = []
        for r in remaining:
            snip = sc.get(r["_q"], "")
            ev = (snip or r["evidence"])[:200]
            ck = f"{r['sid']}|{r['name']}|{ev[:80]}"
            items.append({**r, "evidence": ev, "ck": ck,
                          "cands": candidates(ev, r["hint"], areas)})
        lc = json.loads(LLM_CACHE.read_text(encoding="utf-8")) \
            if LLM_CACHE.exists() else {}
        s = asyncio.run(run_llm(items, lc, args.model, args.batch,
                                args.concurrency))
        LLM_CACHE.write_text(json.dumps(lc, ensure_ascii=False), encoding="utf-8")
        print(f"    {dict(s)}")
        for it in items:
            v = lc.get(it["ck"])
            if v and v in TAX:
                recovered[it["i"]] = v
                st["3_serper_llm"] += 1

    OUT.write_text(json.dumps({str(k): v for k, v in recovered.items()},
                              ensure_ascii=False), encoding="utf-8")
    print(f"\n  RECOVERED {len(recovered):,} of {len(todo):,} "
          f"({len(recovered)/len(todo)*100:.1f}%)  | still null "
          f"{len(todo)-len(recovered):,}")
    for k in sorted(st):
        if k.startswith(("1_", "2_", "3_s")):
            print(f"    {k:22}{st[k]:>9,}")
    print(f"  -> {OUT.name}")
    print("\n  merge with: python apply_taxonomy.py --with-recovered")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=C.LLM_MODEL)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--concurrency", type=int, default=C.LLM_CONCURRENCY)
    sys.exit(main(p.parse_args()))
