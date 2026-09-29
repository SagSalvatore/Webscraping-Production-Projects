"""Map every row onto the 686-area Talabat taxonomy. One of those, or null.

THE LADDER
  1  exact / normalised / segment   deterministic, free      66.5%
  2  LLM CHOOSING FROM the taxonomy  batched, cached          the rest
  3  null                            no confident match

WHY THE LLM IS ASKED A CLOSED QUESTION. "Is this a real area?" invites
invention - it produced approvals for values Talabat does not use and rejected
genuine ones like Dibba. "WHICH of these candidates does this text mean?" cannot
invent, because every allowed answer is drawn from the taxonomy. The model picks
an index or says NONE.

CANDIDATES, NOT THE WHOLE LIST. Sending all 686 per row would be expensive and
would bury the answer. rapidfuzz surfaces the ~12 nearest by character
similarity and the model chooses among those - similarity FINDS candidates, the
model DECIDES. Neither does both, which is the rule that keeps 'Al Barsha 1' and
'Al Barsha 3' from being merged by a threshold.

THE JULY HINT. Where a row has a July-cleaned area, it is passed as evidence
even when that value is not itself in the taxonomy. 'Abu Dhabi Grand Mosque' is
not a Talabat area, but it says where the restaurant is, and the model can use it
to choose the taxonomy area that contains it. It is a HINT, never an answer -
the output is still constrained to the 686.

CITY GUARD. The row's own city is passed too, so a Dubai row is not handed an
Abu Dhabi area.

    python classify_to_taxonomy.py --dry-run     plan + cost, no calls
    python classify_to_taxonomy.py --limit 200   small live test
    python classify_to_taxonomy.py
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from openai import AsyncOpenAI
from rapidfuzz import fuzz, process

import config as C
from taxonomy import build_index, key, load, match, segments

load_dotenv(C.ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")

TAXONOMY = C.HERE / "area_list.csv"
OUT = C.DATA / "taxonomy_assignments.json"
LLM_CACHE = C.CACHE / "taxonomy_llm_cache.json"
N_CAND = 12

PROMPT = """You map a UAE restaurant's raw address text onto ONE area from a \
fixed list of Talabat delivery areas.

For each item you get:
  RAW      the raw address text
  CITY     the emirate the restaurant is in
  HINT     an area label from an earlier cleaning pass - useful evidence about
           WHERE the restaurant is, but it may not itself be on the list
  OPTIONS  numbered candidate areas - you MUST choose one of these, or NONE

Choose the option that the raw text and hint actually refer to.

Rules:
  - Answer with the OPTION NUMBER only, or NONE.
  - Never invent an area. Only the listed options are allowed.
  - Prefer the most specific option that is correct (Al Barsha 1 over Al Barsha)
    but NEVER swap one number for another - Al Barsha 1 and Al Barsha 3 are
    different places.
  - If the text only names a city, a street, a highway or a building and gives
    no area, answer NONE.
  - If no option is clearly right, answer NONE. A NONE is correct and useful;
    a wrong pick silently corrupts the record.

One line per item, no commentary:
INDEX|||OPTION_NUMBER or NONE"""


def candidates(raw, hint, areas, n=N_CAND):
    """Nearest taxonomy areas by character similarity, over the raw text, its
    segments and the July hint. token_sort_ratio, never token_set_ratio - a
    token SUBSET scores 100 and would match 'Al Barsha' to anything."""
    pool = [raw] + segments(raw) + ([hint] if hint else [])
    scored = {}
    for q in pool:
        if not q or len(str(q)) < 3:
            continue
        for name, score, _ in process.extract(
                str(q), areas, scorer=fuzz.token_sort_ratio, limit=n):
            scored[name] = max(scored.get(name, 0), score)
    return [a for a, _ in sorted(scored.items(), key=lambda x: -x[1])[:n]]


async def run_llm(items, cache, model, batch, concurrency):
    api = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY")
    if not api:
        raise SystemExit("no OpenAI key: set OPEN_AI_API in talabat/.env")
    client = AsyncOpenAI(api_key=api)
    sem = asyncio.Semaphore(concurrency)
    todo = [i for i in items if i["ck"] not in cache]
    print(f"    cached {len(items)-len(todo):,} | to classify {len(todo):,}")
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    st = Counter()

    async def one(chunk):
        lines = []
        for n, it in enumerate(chunk):
            opts = " ".join(f"[{j+1}] {a}" for j, a in enumerate(it["cands"]))
            lines.append(f"{n}|||RAW={it['raw'][:90]}|||CITY={it['city']}|||"
                         f"HINT={it['hint'] or '-'}|||OPTIONS={opts}")
        payload = "\n".join(lines)
        for attempt in range(4):
            try:
                async with sem:
                    resp = await client.chat.completions.create(
                        model=model, temperature=0, max_tokens=900,
                        messages=[{"role": "system", "content": PROMPT},
                                  {"role": "user", "content": payload}])
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
                        pick = it["cands"][int(ans.strip("[]. ")) - 1]
                    except (ValueError, IndexError):
                        cache[it["ck"]] = None
                        st["bad_index"] += 1
                        continue
                    cache[it["ck"]] = pick
                    st["chosen"] += 1
                return
            except Exception:
                st["api_error"] += 1
                await asyncio.sleep(2 * (attempt + 1))
        st["batch_failed"] += 1

    for i in range(0, len(batches), concurrency):
        await asyncio.gather(*(one(b) for b in batches[i:i + concurrency]))
        LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                             encoding="utf-8")
        print(f"    {min(i+concurrency, len(batches))}/{len(batches)} batches",
              flush=True)
    return st


def main(args):
    print("=" * 78)
    print("  CLASSIFY TO TAXONOMY")
    print("=" * 78)

    areas, S = load(TAXONOMY)
    idx = build_index(areas)
    records = json.loads(C.INPUT.read_text(encoding="utf-8"))
    print(f"  taxonomy {len(areas):,} areas | records {len(records):,}")

    # July's cleaned value, as a HINT only - it is evidence about location even
    # where the label itself is not a Talabat area (498 of July's 716 are not).
    july = {}
    if C.JULY_CLEAN.exists():
        for r in json.loads(C.JULY_CLEAN.read_text(encoding="utf-8")):
            a = (r.get("location") or {}).get("area")
            if a:
                july.setdefault(str(r["source_id"]), a)
    rows_per_sid = Counter(str(r.get("source_id")) for r in records)
    print(f"  July hints available: {len(july):,}")

    assigned, need = {}, []
    st = Counter()
    for i, r in enumerate(records):
        loc = r.get("location") or {}
        raw = (loc.get("area") or "").strip()
        sid = str(r.get("source_id"))
        # the hint is per-restaurant, so only for source_ids with ONE row -
        # a chain's branches share source_id but sit in different areas
        hint = july.get(sid) if rows_per_sid[sid] == 1 else None

        got, how = match(raw, idx, S)
        if not got and hint:
            got, how = match(hint, idx, S)
            if got:
                how = f"hint_{how}"
        if got:
            assigned[i] = got
            st[how] += 1
        else:
            st["needs_llm"] += 1
            need.append({"i": i, "raw": raw, "city": loc.get("city"),
                         "hint": hint, "name": r.get("name"),
                         "ck": f"{raw}|{loc.get('city')}|{hint}"})

    tot = len(records)
    print(f"\n  {'METHOD':22}{'ROWS':>9}")
    for k, v in st.most_common():
        print(f"    {k:22}{v:>9,}   ({v/tot*100:5.1f}%)")
    det = tot - st["needs_llm"]
    print(f"\n  deterministic {det:,} ({det/tot*100:.1f}%) | "
          f"to LLM {st['needs_llm']:,}")
    print(f"  distinct LLM keys: {len({n['ck'] for n in need}):,}")

    if args.dry_run:
        print("\n  --dry-run: no calls")
        return 0

    if need:
        print("\n  building candidates ...")
        seen = {}
        for n in need:
            if n["ck"] not in seen:
                seen[n["ck"]] = candidates(n["raw"], n["hint"], areas)
            n["cands"] = seen[n["ck"]]
        uniq = list({n["ck"]: n for n in need}.values())
        if args.limit:
            uniq = uniq[: args.limit]
            print(f"  --limit {len(uniq):,}")
        cache = json.loads(LLM_CACHE.read_text(encoding="utf-8")) \
            if LLM_CACHE.exists() else {}
        s = asyncio.run(run_llm(uniq, cache, args.model, args.batch,
                                args.concurrency))
        LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                             encoding="utf-8")
        print(f"    {dict(s)}")
        got = 0
        for n in need:
            v = cache.get(n["ck"])
            if v:
                assigned[n["i"]] = v
                got += 1
        print(f"  LLM resolved {got:,} of {len(need):,} rows")

    OUT.write_text(json.dumps({str(k): v for k, v in assigned.items()},
                              ensure_ascii=False), encoding="utf-8")
    print(f"\n  ASSIGNED {len(assigned):,} / {tot:,} "
          f"({len(assigned)/tot*100:.1f}%) | null {tot-len(assigned):,}")
    print(f"  distinct areas used: {len(set(assigned.values())):,} of {len(areas):,}")
    print(f"  -> {OUT.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=C.LLM_MODEL)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--concurrency", type=int, default=C.LLM_CONCURRENCY)
    sys.exit(main(p.parse_args()))
