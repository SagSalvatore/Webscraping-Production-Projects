"""Stage 3 - decide which of the 596 candidate new areas are genuine UAE areas.

596 is too many to approve by hand, so the LLM does the judging - but it is
NOT trusted on its own. Two deterministic gates bracket it:

  PRE-GATE   rejects what needs no model: bare city names, zone codes, street
             and landmark tokens, values that are too short. These cost nothing
             and remove the errors the July pipeline already learned to catch
             ('Ajman' is a CITY, not an area - it appeared 66 times).

  LLM        judges only what survives, batched and cached, asked a narrow
             question: is this a genuine UAE neighbourhood / district /
             community name, given the emirate it was found in.

  POST-GATE  re-validates every APPROVE the model returns. A prompt instruction
             is not a guarantee: in July the model returned bare cities and zone
             codes despite being told not to. Anything failing the same rules as
             the pre-gate is downgraded to REJECT regardless of what it said.

D6 - UAE ONLY. A candidate that is not a UAE location is rejected outright, not
flagged. The dataset is UAE-only by definition.

The model is instructed to answer UNSURE rather than guess. An honest UNSURE
routes to the small human queue; a confident wrong answer silently corrupts the
vocabulary for every future month, because this file becomes next month's
reference.

    python approve_new_areas.py --dry-run     pre-gate only, no API spend
    python approve_new_areas.py --limit 40    small live test
    python approve_new_areas.py               full run
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
from collections import Counter

from dotenv import load_dotenv
from openai import AsyncOpenAI

import config as C

load_dotenv(C.ROOT / ".env", override=True)
sys.stdout.reconfigure(encoding="utf-8")

APPROVED = C.DATA / "new_areas_approved.csv"
CACHE = C.CACHE / "new_area_verdicts.json"

ZONE = re.compile(r"^(zone|sector|block|plot|phase|w|e|s|n)\s*[-]?\s*\d+$", re.I)
CODE = re.compile(r"^[a-z]{0,3}\s*\d{1,5}[a-z]?$", re.I)

# `corniche` and `mall` are NOT rejected here, per Sagar: 'Ajman Corniche' and
# 'Al Ain Mall' are genuine UAE locality names people use as addresses. A blanket
# token rule cannot tell those from 'Khalifa Bin Zayed Al Awwal Street', so the
# LLM judges them instead - which is what the LLM stage is for. The tokens below
# are only the ones that are NEVER an area on their own.
STREET = re.compile(
    r"\b(st|street|rd|road|ave|avenue|highway|blvd|floor|shop|unit|office|"
    r"bldg|building|villa|hotel|parking|basement)\b", re.I)
DIRECTIONAL = re.compile(r"\b(opposite|beside|behind|near|next to|inside|"
                         r"in front of|across)\b", re.I)

# An area whose name names a DIFFERENT city than the row's own is a
# contradiction worth surfacing: all 11 'Al Ain Mall' rows carry city='Abu Dhabi'
# while Al Ain is a separate city value in this dataset. Reported, not rejected -
# Al Ain does sit inside Abu Dhabi emirate, so it is a labelling inconsistency
# rather than a wrong area.
CITY_IN_NAME = {c.lower(): c for c in
                ("Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah",
                 "Fujairah", "Umm Al Quwain", "Al Ain", "Khor Fakkan")}


def city_conflict(area, city):
    """-> the city named INSIDE the area string, when it differs from `city`."""
    if not area or not city:
        return None
    a = area.lower()
    for k, proper in CITY_IN_NAME.items():
        if k in a and proper != city:
            return proper
    return None

PROMPT = """You validate UAE LOCALITY names for a restaurant location dataset.

For each candidate you are given the emirate it was observed in. Decide whether \
the candidate is a real, named PLACE in the UAE that people use as the location \
part of an address.

APPROVE any genuine named UAE locality, including:
  - neighbourhoods, districts, communities, suburbs (Al Barsha 1, Hor Al Anz)
  - INDUSTRIAL areas and zones (Mussafah Sanaiya, Al Jurf Industrial Area 1,
    Ajman Industrial Area 1) - these are normal addresses here
  - FREE ZONES (Ajman Free Zone, Dubai Airport Free Zone)
  - towns and coastal localities (Dibba, Khor Fakkan, Hatta)
  - master-planned developments (Dubai Festival City, DAMAC Hills)
  - a named corniche or mall that is used locally AS the area
    (Ajman Corniche, Al Ain Mall)
  - university / campus districts used as an address (University City)

REJECT only if it is:
  - an emirate or city on its own (Dubai, Ajman, Sharjah, Abu Dhabi, Fujairah)
  - a street, road or highway (Khalifa Bin Zayed Al Awwal Street)
  - a specific building, tower, villa, shop or hotel
  - a business or company name
  - a bare code, zone number or plot number with no place name
  - not in the UAE at all

Being industrial, being a town rather than a neighbourhood, or containing the \
word Mall or Corniche is NOT a reason to reject. If it names a real place in the \
UAE, approve it.

UNSURE if you genuinely cannot tell. Do not guess - an honest UNSURE is more \
useful than a wrong APPROVE.

Return exactly one line per input, no commentary:
INDEX|||APPROVE or REJECT or UNSURE|||short reason"""


def pre_gate(area, city):
    """Reject what needs no model. Returns a reason, or None to continue."""
    a = (area or "").strip()
    if len(a) < 3:
        return "too short"
    if a.lower() in C.CITY_TOKENS:
        return "is a city, not an area"
    if ZONE.match(a) or CODE.match(a):
        return "bare zone/code"
    if STREET.search(a):
        return "street or building token"
    if DIRECTIONAL.search(a):
        return "directional phrase"
    if city and city not in C.UAE_CITIES:
        return f"city '{city}' is not a UAE city"
    return None


async def judge(cands, cache, model, concurrency, batch):
    key = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("no OpenAI key: set OPEN_AI_API in talabat/.env")
    client = AsyncOpenAI(api_key=key)
    sem = asyncio.Semaphore(concurrency)
    todo = [c for c in cands if c["candidate_area"] not in cache]
    print(f"    cached {len(cands)-len(todo):,} | to judge {len(todo):,}")
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    stats = Counter()

    async def one(bi, chunk):
        payload = "\n".join(
            f"{i}|||{c['candidate_area']}|||emirate={c['city']}"
            for i, c in enumerate(chunk))
        for attempt in range(4):
            try:
                async with sem:
                    resp = await client.chat.completions.create(
                        model=model, temperature=0, max_tokens=1200,
                        messages=[{"role": "system", "content": PROMPT},
                                  {"role": "user", "content": payload}])
                for line in (resp.choices[0].message.content or "").splitlines():
                    parts = line.split("|||")
                    if len(parts) < 2:
                        continue
                    try:
                        c = chunk[int(parts[0].strip())]
                    except (ValueError, IndexError):
                        continue
                    verdict = parts[1].strip().upper()
                    if verdict not in ("APPROVE", "REJECT", "UNSURE"):
                        stats["bad_verdict"] += 1
                        continue
                    cache[c["candidate_area"]] = {
                        "verdict": verdict,
                        "reason": (parts[2].strip() if len(parts) > 2 else ""),
                    }
                    stats[verdict] += 1
                return
            except Exception as exc:
                stats["api_error"] += 1
                await asyncio.sleep(2 * (attempt + 1))
        stats["batch_failed"] += 1

    # checkpoint as we go - never lose paid work
    for i in range(0, len(batches), concurrency):
        await asyncio.gather(*(one(bi, b) for bi, b in
                               enumerate(batches[i:i + concurrency])))
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"    {min(i+concurrency, len(batches))}/{len(batches)} batches",
              flush=True)
    return stats


def main(args):
    print("=" * 76)
    print("  APPROVE NEW AREAS")
    print("=" * 76)

    if not C.NEW_AREAS.exists():
        print("  ABORT: run resolve_areas.py first")
        return 2
    with open(C.NEW_AREAS, encoding="utf-8-sig") as f:
        cands = list(csv.DictReader(f))
    print(f"  candidates: {len(cands):,}")

    # ---- pre-gate --------------------------------------------------------
    rejected, remaining = [], []
    for c in cands:
        why = pre_gate(c["candidate_area"], c.get("city"))
        if why:
            rejected.append({**c, "verdict": "REJECT", "reason": why,
                             "decided_by": "rule"})
        else:
            remaining.append(c)
    print(f"\n  PRE-GATE rejected {len(rejected):,} without an API call:")
    for r in Counter(x["reason"] for x in rejected).most_common():
        print(f"     {r[1]:>5}  {r[0]}")
    print(f"  to judge with the LLM: {len(remaining):,}")

    if args.limit:
        remaining = remaining[: args.limit]
        print(f"  --limit: {len(remaining):,}")

    if args.dry_run:
        print("\n  --dry-run: no API call made, nothing written")
        return 0

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    stats = asyncio.run(judge(remaining, cache, args.model, args.concurrency,
                              args.batch))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n  LLM: {dict(stats)}")

    # ---- post-gate -------------------------------------------------------
    out, downgraded = list(rejected), 0
    for c in remaining:
        v = cache.get(c["candidate_area"])
        if not v:
            out.append({**c, "verdict": "UNSURE", "reason": "no verdict returned",
                        "decided_by": "missing"})
            continue
        verdict, reason, by = v["verdict"], v.get("reason", ""), "llm"
        if verdict == "APPROVE":
            why = pre_gate(c["candidate_area"], c.get("city"))
            if why:
                verdict, reason, by = "REJECT", f"post-gate: {why}", "rule_override"
                downgraded += 1
        conflict = city_conflict(c["candidate_area"], c.get("city"))
        out.append({**c, "verdict": verdict, "reason": reason, "decided_by": by,
                    "city_named_in_area": conflict or ""})

    if downgraded:
        print(f"  POST-GATE overrode {downgraded} LLM approvals")

    tally = Counter(x["verdict"] for x in out)
    rows = Counter()
    for x in out:
        rows[x["verdict"]] += int(x.get("rows") or 0)
    print(f"\n  {'VERDICT':10}{'AREAS':>8}{'ROWS':>10}")
    for v in ("APPROVE", "REJECT", "UNSURE"):
        print(f"    {v:10}{tally[v]:>8,}{rows[v]:>10,}")

    # MERGE onto the previous verdicts, never replace them.
    #
    # Each resolve pass grows the vocabulary, so the NEXT pass sees a different
    # (smaller) candidate set. Writing only the current set drops every verdict
    # from earlier passes, the vocabulary shrinks back, and the loop oscillates
    # instead of converging - measured: pass 3 landed exactly on pass 1
    # (882 -> 1,128 -> 882 areas). This union is what makes it terminate.
    merged = {}
    if APPROVED.exists():
        with open(APPROVED, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                merged[r["candidate_area"]] = r
    fresh = 0
    for r in out:
        k = r["candidate_area"]
        if k not in merged:
            fresh += 1
        merged[k] = r            # a re-judged verdict supersedes the old one
    print(f"\n  merged with previous verdicts: {len(merged):,} total "
          f"({fresh:,} new this pass)")

    fields = ["candidate_area", "rows", "city", "example_source_id",
              "example_name", "example_raw", "verdict", "reason", "decided_by",
              "city_named_in_area"]
    with open(APPROVED, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(merged.values(),
                           key=lambda x: (x.get("verdict", ""),
                                          -int(x.get("rows") or 0))))
    tally = Counter(x.get("verdict") for x in merged.values())
    print(f"  cumulative: " + " | ".join(f"{v} {tally[v]:,}"
                                         for v in ("APPROVE", "REJECT", "UNSURE")))
    print(f"  -> {APPROVED.name}")
    print("\n  sample APPROVED:")
    for x in [y for y in out if y["verdict"] == "APPROVE"][:8]:
        print(f"     {x['rows']:>5}  {x['candidate_area'][:34]:36} "
              f"{x['city'][:16]:18} {x['reason'][:30]}")
    print("\n  sample REJECTED:")
    for x in [y for y in out if y["verdict"] == "REJECT"][:8]:
        print(f"     {x['rows']:>5}  {x['candidate_area'][:34]:36} "
              f"{x['reason'][:40]}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=C.LLM_MODEL)
    p.add_argument("--batch", type=int, default=C.LLM_BATCH)
    p.add_argument("--concurrency", type=int, default=C.LLM_CONCURRENCY)
    sys.exit(main(p.parse_args()))
