"""Resolve values that are landmarks/streets rather than areas.

The audit flagged ~90 distinct values containing a non-area token, but they are
NOT all wrong: 'Trade Center 1', 'Trade Center 2' and 'Trade Center Area' are
official Dubai community names, as are 'University City' and 'Dubai Airport
Free Zone'. So this does not bulk-delete - it asks the model to decide per
value, with an explicit instruction to return a genuine community unchanged.

Deterministic fixes are applied first (typography, obvious junk) so the model
only sees what actually needs judgement.

    python fix_non_areas.py --dry-run    show what would change
    python fix_non_areas.py              apply
"""
import argparse
import asyncio
import csv
import json
import re
import shutil
import sys

import orjson
from loguru import logger
from openai import AsyncOpenAI
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from config import INPUT_JSON, LOG_DIR, MODEL, OPENAI_KEY, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "fix_non_areas.log", level="DEBUG", encoding="utf-8")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"

# --- deterministic typography / obvious junk, no model needed ---
TYPO = [
    (r"[‘’]", "'"),          # curly -> straight apostrophe
    (r"\bInt'l\b", "International"),
    (r"\s*,\s*", " - "),               # 'The Golden Mile, Palm Jumeirah'
    (r"\s{2,}", " "),
]
# values that carry no locatable signal at all
DROP = {"area", "metro station", "city center", "m 36", "79 street", "6a st",
        "36a street", "street 112", "old airport"}

SYSTEM = (
    "You normalise UAE location values. For EACH numbered input decide:\n"
    "1. If it is ALREADY a genuine UAE community/district/area name, return it "
    "UNCHANGED. Examples that are real areas and must be kept as-is: "
    "'Trade Center 1', 'Trade Center 2', 'Trade Center Area', 'University City', "
    "'Dubai Airport Free Zone', 'Dubai Land Residence Complex', "
    "'Al Souk Al Kabir', 'Abu Dhabi Gate City'.\n"
    "2. If it is a MALL, TOWER, AIRPORT, TERMINAL, STREET, ROAD, METRO STATION "
    "or other landmark, return the community/district it sits in. "
    "'Mall of the Emirates' -> 'Al Barsha 1'; 'The Dubai Mall' -> "
    "'Downtown Dubai'; 'Ibn Battuta Mall' -> 'Jebel Ali Village'; "
    "'Dubai International Airport' -> 'Al Garhoud'.\n"
    "3. If it names no specific area (too generic, or a bare city), return null.\n"
    "Rules: bare area name only - no parentheses, no qualifiers, no city suffix; "
    "keep sub-area numbers ('Al Barsha 1' stays 'Al Barsha 1'); never return a "
    "bare city or emirate ('Dubai', 'Abu Dhabi', 'Al Ain', 'Sharjah'); never "
    "return a zone code.\n"
    'Return JSON: {"results":[{"i":<index>,"area":<string|null>,'
    '"confidence":<0-1>}]} - one per input, same order.'
)

NON_AREA = re.compile(
    r"\b(floor|level|shop|unit|building|tower|plaza|mall|centre|center|hotel|"
    r"complex|souk|street|st|road|rd|avenue|ave|blvd|highway|petrol|station|"
    r"enoc|adnoc|emarat|near|inside|opposite|food court|parking|gate|college|"
    r"university|hospital|terminal|airport|unnamed)\b", re.I)
CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "uae", "fujairah",
          "umm al quwain", "ras al khaimah", "al ain", "united arab emirates"}
ZONE = re.compile(r"^(zone|sector|plot|block)\s*\d+[a-z]?$|^[a-z]{1,3}\s?\d{1,3}[a-z]?$", re.I)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=40),
       retry=retry_if_exception_type(Exception), reraise=True)
async def ask(client, batch):
    payload = "\n".join(f"{i}. {v}" for i, v in enumerate(batch))
    r = await client.chat.completions.create(
        model=MODEL, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": payload}])
    return r


def valid(a):
    if not a or not isinstance(a, str):
        return None
    a = a.strip(" ,-")
    if len(a) < 3 or a.lower() in CITIES or ZONE.match(a):
        return None
    if NON_AREA.search(a) and not re.search(r"\btrade cent|university city|"
                                            r"free zone|residence complex|"
                                            r"souk al kabir|gate city\b", a, re.I):
        return None          # model handed back another landmark - reject
    return a


async def main(args):
    records = orjson.loads(FINAL.read_bytes())
    from collections import Counter
    areas = Counter((r["location"] or {}).get("area") or "" for r in records)
    areas.pop("", None)

    # 1. deterministic typography
    typo_map = {}
    for a in areas:
        n = a
        for pat, rep in TYPO:
            n = re.sub(pat, rep, n)
        n = n.strip(" -,")
        if n != a:
            typo_map[a] = n
    logger.info(f"typography fixes: {len(typo_map)}  {list(typo_map.items())[:4]}")

    # 2. everything still suspect goes to the model
    suspect = sorted({typo_map.get(a, a) for a in areas
                      if NON_AREA.search(a) or a.strip().lower() in CITIES
                      or a.strip().lower() in DROP or ZONE.match(a.strip())
                      or a.islower() or len(a.strip()) > 40})
    logger.info(f"values needing judgement: {len(suspect)}")

    resolved = {}
    if not args.dry_run and suspect:
        client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=90)
        batches = [suspect[i:i + 20] for i in range(0, len(suspect), 20)]
        sem = asyncio.Semaphore(8)

        async def run(b):
            async with sem:
                r = await ask(client, b)
            try:
                res = json.loads(r.choices[0].message.content)["results"]
                by_i = {x.get("i"): x for x in res if isinstance(x, dict)}
            except Exception as exc:
                logger.error(f"unparseable: {exc}")
                return
            for i, v in enumerate(b):
                resolved[v] = valid((by_i.get(i) or {}).get("area"))

        await asyncio.gather(*(run(b) for b in batches))
        logger.success(f"model resolved {sum(1 for v in resolved.values() if v)}"
                       f"/{len(suspect)}")

    # 3. build final map and apply
    changes, dropped = {}, []
    for a in areas:
        t = typo_map.get(a, a)
        if t in resolved:
            r = resolved[t]
            if r and r != a:
                changes[a] = r
            elif r is None:
                dropped.append(a)
        elif t != a:
            changes[a] = t

    logger.info(f"renames {len(changes)} | model returned null for {len(dropped)}")
    for a, b in list(changes.items())[:18]:
        logger.info(f"    {a[:34]:36} -> {b}")
    if dropped:
        logger.warning(f"no area determinable (left unchanged, flagged): {dropped[:10]}")

    if args.dry_run:
        logger.warning("--dry-run: nothing written")
        return

    n = 0
    for rec in records:
        loc = rec["location"]
        a = loc.get("area")
        if a in changes:
            loc["area"] = changes[a]
            n += 1
    shutil.copy2(FINAL, FINAL.with_suffix(".json.bak9"))
    FINAL.write_bytes(orjson.dumps(records, option=orjson.OPT_INDENT_2))

    with open(OUTPUT_DIR / "non_area_fix_map.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["from", "to"])
        for a, b in sorted(changes.items()):
            w.writerow([a, b])

    after = Counter((r["location"] or {}).get("area") or "" for r in records)
    after.pop("", None)
    logger.success(f"{n:,} rows updated | distinct {len(areas):,} -> {len(after):,}")

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
    asyncio.run(main(p.parse_args()))
