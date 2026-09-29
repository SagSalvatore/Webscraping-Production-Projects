"""Resolve the last landmark/street values using gpt-5-mini + OpenAI web_search.

Only 52 DISTINCT values sit behind the 151 rows, so we search once per value.

Honest scope: a mall, airport or metro station HAS one containing district and
is resolvable. A long arterial road (Sheikh Zayed Road, Emirates Road, Al Khail
Road) genuinely runs through many districts - there is no correct single answer,
and the prompt instructs the model to return null rather than pick one. Do not
"improve" that behaviour later; a plausible wrong district is worse than a null.

    python websearch_resolve.py --dry-run    list what would be searched
    python websearch_resolve.py --limit 6    small live test
    python websearch_resolve.py              full run
"""
import argparse
import asyncio
import csv
import json
import re
import shutil
import sys
import time

import orjson
from loguru import logger
from openai import AsyncOpenAI
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from config import CACHE_DIR, INPUT_JSON, LOG_DIR, MODEL, OPENAI_KEY, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "websearch_resolve.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"
REVIEW = OUTPUT_DIR / "area_needs_review.csv"
CACHE = CACHE_DIR / "websearch_area_cache.json"

PRICE_IN, PRICE_OUT = 0.25, 2.00
CONCURRENCY = 6

PROMPT = """You are identifying the UAE community/district that contains a place.

PLACE: {place}
CITY: {city}

Search the web if needed, then answer.

Rules:
- Return the specific community/district/area that CONTAINS this place.
  e.g. "Dalma Mall" in Abu Dhabi -> "Mussafah"
       "Ibn Battuta Mall" in Dubai -> "Jebel Ali Village"
- If the place is a long road or highway that runs through MANY districts
  (e.g. Sheikh Zayed Road, Emirates Road, Al Khail Road, Airport Road,
  Muroor Road), there is NO single correct area: return null.
- If the place is too generic to locate ("City Center", "Metro Station",
  "area", "uptown"), return null.
- Never return a bare city or emirate (not "Dubai", "Abu Dhabi", "Al Ain").
- Never return a zone code.
- Return the bare area name only: no parentheses, no citations, no city suffix.

Respond ONLY with JSON: {{"area": <string|null>, "confidence": <0-1>, "source": "<url or ''>"}}"""

CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "uae", "fujairah",
          "umm al quwain", "ras al khaimah", "al ain", "united arab emirates"}
BAD = re.compile(r"\b(street|st|road|rd|highway|metro|terminal|unnamed)\b", re.I)
ZONE = re.compile(r"^(zone|sector|plot|block)\s*\d+[a-z]?$|^[a-z]{1,3}\s?\d{1,3}$", re.I)


def validate(a):
    """A prompt rule is not a guarantee - gate the output deterministically."""
    if not a or not isinstance(a, str):
        return None
    a = re.sub(r"\s*\(\[?[^)]*\)?\]?\s*$", "", a).strip(" ,-.")  # strip citations
    a = re.sub(r"\s{2,}", " ", a)
    if len(a) < 3 or a.lower() in CITIES or ZONE.match(a) or BAD.search(a):
        return None
    return a


class Spend:
    def __init__(self):
        self.i = self.o = self.n = 0

    @property
    def usd(self):
        return self.i / 1e6 * PRICE_IN + self.o / 1e6 * PRICE_OUT


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(Exception), reraise=True)
async def search_one(client, place, city):
    return await client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=PROMPT.format(place=place, city=city or "UAE"),
    )


def load_cache():
    if CACHE.exists():
        try:
            return orjson.loads(CACHE.read_bytes())
        except Exception:
            pass
    return {}


def save_cache(c):
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(c, option=orjson.OPT_INDENT_2))
    tmp.replace(CACHE)


async def main(args):
    rows = list(csv.DictReader(open(REVIEW, encoding="utf-8-sig")))
    # one search per DISTINCT (area, city) - 151 rows collapse to ~52 values
    targets = {}
    for r in rows:
        targets.setdefault((r["area_current"], r.get("city") or "UAE"), 0)
        targets[(r["area_current"], r.get("city") or "UAE")] += 1
    todo = sorted(targets.items(), key=lambda t: -t[1])
    logger.info(f"{len(rows)} rows -> {len(todo)} distinct (place, city) searches")

    cache = load_cache()
    pending = [t for t in todo if f"{t[0][0]}|{t[0][1]}" not in cache]
    logger.info(f"cached {len(todo)-len(pending)} | to search {len(pending)}")
    if args.limit:
        pending = pending[:args.limit]
        logger.warning(f"TEST MODE - {len(pending)} searches")

    if args.dry_run:
        for (place, city), n in todo[:25]:
            logger.info(f"    {n:>4} rows  {place}  [{city}]")
        logger.warning("--dry-run: no spend")
        return

    spend = Spend()
    client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=180)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    t0 = time.time()

    async def run(place, city, nrows):
        nonlocal done
        key = f"{place}|{city}"
        async with sem:
            try:
                r = await search_one(client, place, city)
            except Exception as exc:
                logger.error(f"failed {place!r}: {type(exc).__name__}: {str(exc)[:90]}")
                cache[key] = {"area": None, "note": "search failed"}
                return
        u = getattr(r, "usage", None)
        if u:
            spend.i += getattr(u, "input_tokens", 0)
            spend.o += getattr(u, "output_tokens", 0)
            spend.n += 1
        txt = (getattr(r, "output_text", "") or "").strip()
        area = conf = src = None
        try:
            m = re.search(r"\{.*\}", txt, re.S)
            j = json.loads(m.group(0)) if m else {}
            area, conf, src = j.get("area"), j.get("confidence"), j.get("source")
        except Exception:
            logger.warning(f"unparseable for {place!r}: {txt[:90]}")
        cache[key] = {"area": validate(area), "raw": area, "confidence": conf,
                      "source": src, "rows": nrows}
        done += 1
        if done % 5 == 0:
            save_cache(cache)
            el = time.time() - t0
            logger.info(f"  {done}/{len(pending)} | ${spend.usd:.3f} | "
                        f"ETA {(len(pending)-done)*el/max(done,1):.0f}s")

    try:
        await asyncio.gather(*(run(p, c, n) for (p, c), n in pending))
    finally:
        save_cache(cache)

    hits = {k: v for k, v in cache.items() if v.get("area")}
    logger.success(f"searched {spend.n} | resolved {len(hits)}/{len(cache)} | "
                   f"${spend.usd:.3f}")

    # ---- apply ----
    records = orjson.loads(FINAL.read_bytes())
    bycity = {}
    for r in records:
        loc = r["location"]
        bycity[(loc.get("area"), loc.get("city") or "UAE")] = None
    applied = 0
    changes = []
    for r in records:
        loc = r["location"]
        key = f"{loc.get('area')}|{loc.get('city') or 'UAE'}"
        hit = cache.get(key)
        if hit and hit.get("area"):
            changes.append((loc["area"], hit["area"], hit.get("source")))
            loc["area"] = hit["area"]
            applied += 1

    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak10"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    with open(OUTPUT_DIR / "websearch_resolution_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "rows", "confidence", "source"])
        seen = {}
        for k, v in cache.items():
            if v.get("area"):
                seen[k] = v
        for k, v in sorted(seen.items(), key=lambda t: -(t[1].get("rows") or 0)):
            w.writerow([k.split("|")[0], v["area"], v.get("rows"),
                        v.get("confidence"), v.get("source")])

    from collections import Counter
    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    logger.success(f"applied to {applied:,} rows | distinct -> {len(after):,}")

    original = orjson.loads(INPUT_JSON.read_bytes())
    assert len(records) == len(original) == 17164
    assert len({r["_id"]["$oid"] for r in records}) == 17164
    for x, y in zip(original, records):
        assert x["_id"] == y["_id"]
        assert list(x["location"].keys()) == list(y["location"].keys())
    logger.success("integrity OK")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    asyncio.run(main(p.parse_args()))
