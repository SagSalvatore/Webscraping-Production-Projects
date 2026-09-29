"""Fill location.city for the August export - by AREA, not by record.

WHY THIS IS CHEAP. 4,369 records lack a city, but they share only 996 distinct
areas, and 56 of those are already resolvable from August rows that DO have a
Google address. So the model is asked about ~940 areas, not 4,369 restaurants.

WHY IT IS ACCURATE. Each area is sent WITH its median lat/lon, computed from
Talabat's own coordinates (100% coverage). A bare name like "Al Jahili" or
"Al Mahatta" is ambiguous across emirates; a name plus a coordinate is not.
This is the fix for every method that failed earlier:

    kNN on July's labelled coords      69-79%   July's own city disagrees
                                                with July's own coordinates
    kNN on Google-address labels       72-81%   labels are brand-level, so a
                                                chain's branches all inherit
                                                one emirate
    geometric rule on coordinates         62%   emirates interleave

VOCABULARY IS CLOSED to the 7 emirates plus Al Ain and Khor Fakkan - the exact
set July shipped - so nothing new appears in a field Tech already parses.
Al Ain sits inside Abu Dhabi emirate but July ships it separately; that
convention is kept rather than "corrected".

Anything the model will not commit to is left NULL. A wrong city is worse than
an absent one.

    python resolve_area_cities.py --dry-run     # sizes the job, spends nothing
    python resolve_area_cities.py               # ~$0.02
    python resolve_area_cities.py --apply       # write cities into the export
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env", override=True)

EXPORT = HERE / "data" / "August_export.json"
CACHE = HERE / "data" / "area_city_cache.json"

MODEL = "gpt-4.1-mini"
BATCH = 25
CONCURRENCY = 10

CITIES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Umm Al Quwain",
          "Ras Al Khaimah", "Fujairah", "Al Ain", "Khor Fakkan"]

SYSTEM = f"""You map UAE locality names to the city/emirate they belong to.

You will be given lines of the form:
  <id>|<area name>|<latitude>,<longitude>

The coordinates are authoritative - they come from the restaurant listing
itself. When the name is ambiguous across emirates, TRUST THE COORDINATES.

Answer with one JSON object per line, nothing else:
  {{"id": <id>, "city": "<one of the allowed values>", "confidence": "High|Medium|Low"}}

Allowed city values, and NOTHING else: {", ".join(CITIES)}

Rules:
- "Al Ain" for localities in the Al Ain region, even though it is
  administratively part of Abu Dhabi emirate.
- "Khor Fakkan" for Khor Fakkan itself; other Sharjah east-coast exclaves are
  "Sharjah".
- If the coordinates fall outside the UAE, or you cannot tell, use
  "city": null with confidence "Low". Do not guess.
"""

sys.stdout.reconfigure(encoding="utf-8")


def collect(export):
    """area -> (median lat, median lon, record count), for areas lacking a city.
    Also returns cities already known per area from records that DO have one."""
    need = defaultdict(list)
    known = defaultdict(Counter)
    for r in export:
        area = (r["location"] or {}).get("area")
        city = (r["location"] or {}).get("city")
        g = r.get("geo") or {}
        if not area:
            continue
        if city:
            known[area.strip().lower()][city] += 1
        elif g.get("lat") is not None:
            need[area].append((g["lat"], g["lng"]))
    return need, known


async def run(areas, cache):
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API"])
    sem = asyncio.Semaphore(CONCURRENCY)
    todo = [a for a in areas if a[1] not in cache]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    stats = Counter()

    async def one(batch):
        payload = "\n".join(f"{i}|{name}|{lat:.5f},{lon:.5f}"
                            for i, name, lat, lon in batch)
        idx = {i: name for i, name, _, _ in batch}
        for attempt in range(4):
            try:
                async with sem:
                    r = await client.chat.completions.create(
                        model=MODEL, temperature=0, max_tokens=2000,
                        messages=[{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content":
                                   f"Map these {len(batch)} UAE areas:\n{payload}"}])
                txt = r.choices[0].message.content or ""
                stats["prompt_tokens"] += r.usage.prompt_tokens
                stats["completion_tokens"] += r.usage.completion_tokens
                for line in txt.splitlines():
                    line = line.strip().strip("`")
                    if not line.startswith("{"):
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        stats["bad_json"] += 1
                        continue
                    name = idx.get(rec.get("id"))
                    city = rec.get("city")
                    if name is None:
                        continue
                    if city not in CITIES:
                        city = None            # closed vocabulary, no exceptions
                        stats["rejected_value"] += 1
                    cache[name] = {"city": city,
                                   "confidence": rec.get("confidence", "Low")}
                return
            except Exception as exc:
                stats[f"err_{type(exc).__name__}"] += 1
                await asyncio.sleep(2 ** attempt)

    print(f"  {len(todo):,} areas to resolve in {len(batches)} batches")
    for i in range(0, len(batches), 40):
        await asyncio.gather(*(one(b) for b in batches[i:i + 40]))
        print(f"    {min(i+40, len(batches))}/{len(batches)} batches", flush=True)
    return stats


def main(args):
    export = json.loads(EXPORT.read_text(encoding="utf-8"))
    need, known = collect(export)
    print("=" * 68)
    print("  AREA -> CITY")
    print("=" * 68)
    print(f"  records missing city : "
          f"{sum(1 for r in export if not r['location']['city']):,}")
    print(f"  distinct areas       : {len(need):,}")

    # free tier: an area another August record already resolved from a real address
    free = {a: known[a.strip().lower()].most_common(1)[0][0]
            for a in need if a.strip().lower() in known}
    print(f"  resolvable from our own rows: {len(free):,} areas "
          f"({sum(len(need[a]) for a in free):,} records)")

    todo = [(i, a, median([p[0] for p in pts]), median([p[1] for p in pts]))
            for i, a in enumerate(sorted(a for a in need if a not in free))
            for pts in [need[a]]]
    print(f"  to send to {MODEL}   : {len(todo):,} areas "
          f"({sum(len(need[a]) for _, a, _, _ in todo):,} records)")
    est = len(todo) / BATCH * (len(SYSTEM) / 4 + 25 * 20) / 1e6 * 0.40
    print(f"  estimated cost       : ~${est:.3f}")

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    print(f"  cached already       : {len(cache):,}")

    if args.dry_run:
        print("\n  --dry-run: nothing spent")
        for _, a, la, lo in todo[:6]:
            print(f"     {a[:40]:42} {la:.4f},{lo:.4f}")
        return 0

    if not os.getenv("OPEN_AI_API"):
        print("  OPEN_AI_API missing from talabat/.env")
        return 1

    stats = asyncio.run(run(todo, cache))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    got = sum(1 for v in cache.values() if v.get("city"))
    print(f"\n  resolved {got:,}/{len(cache):,} areas")
    conf = Counter(v.get("confidence") for v in cache.values() if v.get("city"))
    print(f"  confidence: {dict(conf)}")
    print(f"  cities: {dict(Counter(v['city'] for v in cache.values() if v.get('city')).most_common())}")
    cost = (stats["prompt_tokens"] / 1e6 * 0.40
            + stats["completion_tokens"] / 1e6 * 1.60)
    print(f"  tokens: {stats['prompt_tokens']:,} in / "
          f"{stats['completion_tokens']:,} out  = ${cost:.3f}")
    for k in stats:
        if k.startswith(("err_", "bad_", "rejected")):
            print(f"    {k}: {stats[k]}")

    if not args.apply:
        print("\n  not applied. re-run with --apply to write into the export.")
        return 0

    filled = 0
    for r in export:
        L = r["location"]
        if L.get("city") or not L.get("area"):
            continue
        a = L["area"]
        city = free.get(a) or (cache.get(a) or {}).get("city")
        if city:
            L["city"] = city
            filled += 1
    with open(EXPORT, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False)
    have = sum(1 for r in export if r["location"]["city"])
    print(f"\n  filled {filled:,} records")
    print(f"  city coverage now: {have:,}/{len(export):,} "
          f"({have/len(export)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    sys.exit(main(p.parse_args()))
