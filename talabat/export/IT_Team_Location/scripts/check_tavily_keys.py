"""Probe every Tavily key for remaining credit before relying on them.

Each key gets one cheap search. Classifies into usable / exhausted / invalid so
the rotator only ever cycles through keys that actually work.

Run: python check_tavily_keys.py
"""
import asyncio
import csv
import sys

import httpx
from loguru import logger

from config import LOG_DIR, OUTPUT_DIR, PROJECT

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "tavily_keys.log", level="DEBUG", encoding="utf-8")

KEYS_CSV = PROJECT / "talabat" / "tavily" / "tav_keys.csv"
ENDPOINT = "https://api.tavily.com/search"
GOOD_FILE = OUTPUT_DIR / "tavily_usable_keys.txt"


def load_keys():
    keys = []
    with open(KEYS_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            k = (row.get("Keys") or "").strip()
            if k.startswith("tvly"):
                keys.append((row.get("S.No", "?").strip(), k))
    return keys


async def probe(client, sem, idx, key):
    payload = {"query": "talabat uae restaurant", "max_results": 1,
               "search_depth": "basic"}
    async with sem:
        try:
            r = await client.post(ENDPOINT, json=payload,
                                  headers={"Authorization": f"Bearer {key}"})
        except Exception as exc:
            return idx, key, "ERROR", f"{type(exc).__name__}: {exc}"[:80]

    if r.status_code == 200:
        n = len((r.json() or {}).get("results", []))
        return idx, key, "USABLE", f"{n} result(s)"
    if r.status_code in (401, 403):
        return idx, key, "INVALID", f"HTTP {r.status_code}"
    if r.status_code == 429:
        return idx, key, "EXHAUSTED", "rate/credit limit"
    body = (r.text or "")[:70].replace("\n", " ")
    if "credit" in body.lower() or "usage" in body.lower():
        return idx, key, "EXHAUSTED", body
    return idx, key, f"HTTP {r.status_code}", body


async def main():
    keys = load_keys()
    logger.info(f"probing {len(keys)} Tavily keys from {KEYS_CSV.name}")
    sem = asyncio.Semaphore(10)
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(
            *(probe(client, sem, i, k) for i, k in keys))

    buckets = {}
    usable = []
    for idx, key, status, note in sorted(results, key=lambda t: int(t[0]) if t[0].isdigit() else 0):
        buckets.setdefault(status, []).append(idx)
        tag = {"USABLE": "SUCCESS", "EXHAUSTED": "WARNING"}.get(status, "ERROR")
        getattr(logger, {"SUCCESS": "success", "WARNING": "warning",
                         "ERROR": "error"}[tag])(f"key #{idx:>2}  {status:<10} {note}")
        if status == "USABLE":
            usable.append(key)

    print()
    for status, idxs in buckets.items():
        logger.info(f"{status:<10} {len(idxs):>3} keys  -> #{','.join(idxs)}")

    if usable:
        GOOD_FILE.write_text("\n".join(usable), encoding="utf-8")
        logger.success(f"{len(usable)} usable keys written to {GOOD_FILE.name}")
    else:
        logger.error("NO usable Tavily keys - stage 3 (web search) is not possible")
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
