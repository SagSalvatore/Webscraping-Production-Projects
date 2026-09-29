"""Last tier - extract the area from a row's OWN text, even when it is not in
area_list.csv.

WHO ARRIVES HERE. The 478 sibling rows still null: a source_id with several rows
is a chain whose branches sit in different areas, and EVERY signal we hold for
them - the URL slug, the unified address, a name-based search - is keyed by
source_id, so all of them would give the branches the same answer. Measured when
unguarded: 47 of 342 collapsed onto one value. The row's own area text is the
only per-BRANCH evidence there is.

WHY OFF-TAXONOMY IS ALLOWED HERE, per Sagar. 477 of the 478 carry text that is
NOT in the 686, and the nearest taxonomy match is actively wrong:

    Wadi Al Safa 4        -> Al Safa 2             70    different place
    Dubai Production City -> Dubai Motor City      76    different place
    Al Danah - Zone 1     -> Saif Zone             67    wrong
    Rabdan - RB11         -> Rams                  25    nonsense

area_list.csv is Talabat's DELIVERY ZONES, not every UAE area. Rabdan,
Wadi Al Safa 4, China Cluster, Dubai Production City and Al Ain Oasis are all
real places that simply are not zones. Forcing them onto the nearest zone would
corrupt the field - the numeric-suffix trap (Al Safa 2 vs Al Safa 4) is exactly
the failure the whole pipeline is built to avoid.

So these rows get the area NAMED IN THEIR OWN TEXT, tagged as off-taxonomy, and
written to their own CSV so Tech can see them and decide whether to extend
area_list.csv next month.

THE MODEL EXTRACTS, IT DOES NOT INVENT. It is asked to return a substring of the
text, and a deterministic gate then rejects cities, streets, codes and anything
that does not actually appear in the input.

    python extract_offtaxonomy.py --dry-run
    python extract_offtaxonomy.py --limit 40
    python extract_offtaxonomy.py
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from openai import AsyncOpenAI

import config as C
from taxonomy import build_index, key, load, match, segments

load_dotenv(C.ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")

TAXONOMY = C.HERE / "area_list.csv"
ASSIGN = C.DATA / "taxonomy_assignments.json"
RECOVERED = C.DATA / "recovered_nulls.json"
OUT = C.DATA / "offtaxonomy_areas.json"
REPORT_CSV = C.DATA / "offtaxonomy_areas_for_tech.csv"
CACHE = C.CACHE / "offtaxonomy_llm.json"

ZONE = re.compile(r"^(zone|sector|block|plot|phase)\s*[-]?\s*\w{1,4}$", re.I)
CODE = re.compile(r"^[a-z]{0,3}[\s-]?\d{1,5}[a-z]?$", re.I)
STREET_ONLY = re.compile(r"^(unnamed\s+road|.*\b(st|street|rd|road|highway)\b.*)$",
                         re.I)
PLUS = re.compile(r"\b[23456789CFGHJMPQRVWX]{4,}\+[23456789CFGHJMPQRVWX]{2,}\b")

PROMPT = """You extract the AREA name from a UAE restaurant's raw address text.

Return the substring of the input that names the neighbourhood, district, \
community, suburb or locality. Do NOT translate, expand, correct or invent - \
return the words as they appear in the input.

Examples:
  'Rabdan - RB11'                  -> Rabdan
  '3894+QF2 - Wadi Al Safa 4'      -> Wadi Al Safa 4
  'E-02 - China Cluster'           -> China Cluster
  '128 Al Nahda St - Hay Al Nahda' -> Hay Al Nahda
  'Central District - Al Ain Oasis'-> Al Ain Oasis
  'Unnamed Road'                   -> NONE
  'Al Danah - Zone 1'              -> Al Danah

Return NONE when the text names only a street, a building, a plot or zone code, \
or a city with no area. A NONE is correct and useful; an invented area is not.

One line per item, no commentary:
INDEX|||AREA or NONE"""


def valid(area, raw, city):
    """Gate: must be real, must be a substring of the input, must not be noise."""
    if not area:
        return None
    a = " ".join(str(area).split()).strip(" -,،.")
    if len(a) < 3 or a.upper() == "NONE":
        return None
    if a.lower() in C.CITY_TOKENS:
        return None
    if ZONE.match(a) or CODE.match(a) or PLUS.search(a):
        return None
    if STREET_ONLY.match(a):
        return None
    # it must actually APPEAR in the source text - this is extraction, not
    # generation, and it is what stops the model inventing a plausible area
    if key(a) not in key(raw or ""):
        return None
    return a


async def run(items, cache, model, batch, concurrency):
    api = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY")
    if not api:
        raise SystemExit("no OpenAI key: set OPEN_AI_API in talabat/.env")
    client = AsyncOpenAI(api_key=api)
    sem = asyncio.Semaphore(concurrency)
    todo = [i for i in items if i["ck"] not in cache]
    print(f"    cached {len(items)-len(todo):,} | to extract {len(todo):,}")
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    st = Counter()

    async def one(chunk):
        payload = "\n".join(f"{n}|||{it['raw'][:110]}|||CITY={it['city']}"
                            for n, it in enumerate(chunk))
        for attempt in range(4):
            try:
                async with sem:
                    resp = await client.chat.completions.create(
                        model=model, temperature=0, max_tokens=800,
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
                    got = valid(p[1], it["raw"], it["city"])
                    cache[it["ck"]] = got
                    st["extracted" if got else "none"] += 1
                return
            except Exception:
                st["api_error"] += 1
                await asyncio.sleep(2 * (attempt + 1))
        st["batch_failed"] += 1

    for i in range(0, len(batches), concurrency):
        await asyncio.gather(*(one(b) for b in batches[i:i + concurrency]))
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"    {min(i+concurrency, len(batches))}/{len(batches)} batches",
              flush=True)
    return st


def main(args):
    print("=" * 78)
    print("  EXTRACT OFF-TAXONOMY AREAS  (sibling rows, own text only)")
    print("=" * 78)

    areas, TAX = load(TAXONOMY)
    idx = build_index(areas)
    src = json.loads(C.INPUT.read_text(encoding="utf-8"))
    assigned = set(int(k) for k in
                   json.loads(ASSIGN.read_text(encoding="utf-8")))
    if RECOVERED.exists():
        assigned |= {int(k) for k, v in
                     json.loads(RECOVERED.read_text(encoding="utf-8")).items() if v}

    rows_per_sid = Counter(str(r.get("source_id")) for r in src)
    todo = []
    for i, r in enumerate(src):
        if i in assigned:
            continue
        raw = ((r.get("location") or {}).get("area") or "").strip()
        if not raw:
            continue
        todo.append({"i": i, "raw": raw,
                     "city": (r.get("location") or {}).get("city"),
                     "name": r.get("name"),
                     "sid": str(r.get("source_id")),
                     "multi": rows_per_sid[str(r.get("source_id"))] > 1,
                     "ck": raw})
    print(f"  still-null rows with text: {len(todo):,}")
    print(f"    of which sibling rows   : {sum(1 for t in todo if t['multi']):,}")
    print(f"    distinct texts          : {len({t['ck'] for t in todo}):,}")

    if args.dry_run:
        print("\n  --dry-run: no calls")
        return 0

    uniq = list({t["ck"]: t for t in todo}.values())
    if args.limit:
        uniq = uniq[: args.limit]
        print(f"  --limit {len(uniq):,}")
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    st = asyncio.run(run(uniq, cache, args.model, args.batch, args.concurrency))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"    {dict(st)}")

    raw_out = {}
    for t in todo:
        v = cache.get(t["ck"])
        if not v:
            continue
        # if it turns out to BE a taxonomy area after all, prefer the canonical
        hit, _ = match(v, idx, TAX)
        raw_out[t["i"]] = hit or v

    # ---- post-pass 1: a MALL is a building, not an area ------------------
    # 'Al Ain Mall' and 'Ibn Batutta Mall' are fine - they are ON the taxonomy,
    # so Talabat itself treats them as areas. An off-taxonomy mall is just a
    # building: the area for 'Al Seef Village Mall' is 'Al Seef'. Try stripping
    # the building word and matching again; drop it if that fails.
    MALLISH = re.compile(
        r"\b(mall|centre|center|plaza|tower|hotel|souk|building|bldg|"
        r"residence|residences|residential|apartments|villa)\b", re.I)
    dropped = 0
    for i, v in list(raw_out.items()):
        if v in TAX or not MALLISH.search(v):
            continue
        stripped = MALLISH.sub(" ", v)
        stripped = re.sub(r"\s+", " ", stripped).strip(" -,.")
        hit, _ = match(stripped, idx, TAX)
        if hit:
            raw_out[i] = hit
        else:
            del raw_out[i]
            dropped += 1
    if dropped:
        print(f"  dropped {dropped} off-taxonomy building names (mall/tower/hotel)")

    # ---- post-pass 2: ONE surface form per area --------------------------
    # The model returns a substring verbatim, so it inherits whatever casing the
    # source text used - 'Dubai South' and 'Dubai south' both appeared. Elect the
    # most FREQUENT form, never .title(), which would turn 'DIFC' into 'Difc'.
    forms = defaultdict(Counter)
    for v in raw_out.values():
        if v not in TAX:
            forms[v.strip().lower()][v] += 1
    canon = {k: c.most_common(1)[0][0] for k, c in forms.items()}
    fixed = 0
    for i, v in raw_out.items():
        if v not in TAX:
            c = canon.get(v.strip().lower())
            if c and c != v:
                raw_out[i] = c
                fixed += 1
    if fixed:
        print(f"  canonicalised casing on {fixed} rows "
              f"({sum(1 for c in forms.values() if len(c) > 1)} areas had variants)")

    out, offtax = raw_out, Counter()
    for v in out.values():
        if v not in TAX:
            offtax[v] += 1

    OUT.write_text(json.dumps({str(k): v for k, v in out.items()},
                              ensure_ascii=False), encoding="utf-8")
    print(f"\n  EXTRACTED {len(out):,} of {len(todo):,} rows")
    print(f"    onto the taxonomy      : {len(out)-sum(offtax.values()):,}")
    print(f"    OFF-taxonomy (new)     : {sum(offtax.values()):,} rows, "
          f"{len(offtax):,} distinct")
    print(f"  -> {OUT.name}")

    with open(REPORT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["area_not_in_area_list", "rows",
                    "note"])
        for a, n in offtax.most_common():
            w.writerow([a, n, "real UAE area, not a Talabat delivery zone"])
    print(f"  -> {REPORT_CSV.name}  ({len(offtax):,} values for Tech to review)")
    for a, n in offtax.most_common(12):
        print(f"     {n:>4}  {a}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=C.LLM_MODEL)
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--concurrency", type=int, default=C.LLM_CONCURRENCY)
    sys.exit(main(p.parse_args()))
