"""
location_reports.py - tier 1/2 of Number of Outlets: dated UK outlet counts
published by location-report sites (ScrapeHero, xmap), which build them from
each brand's own store locator - so concessions the brand lists are included.

    "There are 516 Pret a Manger locations in the United Kingdom as of June 03, 2026."

One Tavily search per brand restricted to those sites (free-tier credits),
plus a re-read of everything research.py already fetched. Apify is then only
needed for brands left without a count dated 2025 or later.

    python location_reports.py            # all brands
    python location_reports.py --dry-run  # credit estimate only

Output: data/location_reports.jsonl - one record per brand: the best (most
recent) matching statement, its date, URL and the sentence itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from urllib.parse import unquote

from loguru import logger

import config
from research import (TAVILY_CACHE, Researcher, _fold, _load, brand_regex_for, load_rows,
                      queries_for)

REPORT_DOMAINS = ["scrapehero.com", "xmap.ai"]
OUT = config.DATA_DIR / "location_reports.jsonl"
FRESH_FROM_YEAR = 2025

SENTENCE = re.compile(
    r"there (?:are|is) (?P<n>[\d,]+) (?P<what>[^.]{0,120}?)"
    r"(?:locations|stores|restaurants|shops|pubs|outlets|cafes|bakeries)"
    r"(?P<mid>[^.]{0,60}?)in " + config.R.get("report_regex", r"(?!x)x") + r"\b"
    r"(?:[^.]{0,20}?as of (?P<date>[a-z]+ \d{1,2},? 20\d\d))?")


def report_query(name: str) -> str:
    return f"number of {name} locations in {config.R.get('report_place')}"


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s.title(), fmt)
        except ValueError:
            pass
    return None


def find_reports(name: str, canonical: str | None, payloads: list[dict]) -> list[dict]:
    """Every 'There are N <brand> ... in the United Kingdom [as of <date>]'
    statement on a location-report page, kept only when the brand is named
    in the sentence itself or in the report's URL."""
    brand = brand_regex_for(name, canonical)
    # "PizzaExpress" is written "pizza express" on its report page: also
    # compare with all spaces removed
    compact = {re.sub(r"[^a-z0-9]", "", _fold(n)) for n in (name, canonical or name)}
    found, seen = [], set()
    for p in payloads:
        for r in p.get("results", []) or []:
            url = r.get("url") or ""
            if not any(d in url for d in REPORT_DOMAINS):
                continue
            text = _fold(f"{r.get('title') or ''}. {r.get('content') or ''}")
            url_words = _fold(re.sub(r"[-_/+]+", " ", unquote(url)))
            for m in SENTENCE.finditer(text):
                sentence = m.group(0)
                if " without " in sentence:
                    continue   # "there are 5 states ... without burger king restaurants" is not a count
                squashed = re.sub(r"[^a-z0-9]", "", m.group("what"))
                if not (re.search(brand, sentence) or re.search(brand, url_words)
                        or any(c and c in squashed for c in compact)):
                    continue
                key = (url, m.group("n"))
                if key in seen:
                    continue
                seen.add(key)
                d = parse_date(m.group("date"))
                found.append({"count": int(m.group("n").replace(",", "")),
                              "as_of": d.strftime("%Y-%m-%d") if d else None,
                              "url": url, "sentence": sentence[:220]})
    return found


async def main_async(args) -> None:
    rows = load_rows()
    research = {}
    rp = config.DATA_DIR / "research.jsonl"
    if rp.exists():
        research = {r["row_index"]: r for r in map(json.loads, rp.read_text(encoding="utf-8").splitlines())}
    tcache = _load(TAVILY_CACHE)
    todo = [n for _, n in rows if report_query(n) not in tcache]
    logger.info(f"{len(rows)} brands | {len(todo)} uncached report searches | ~{len(todo) * 2} Tavily credits")
    if args.dry_run:
        return

    rs = Researcher()
    sem = asyncio.Semaphore(4)

    async def fetch(name: str) -> None:
        q = report_query(name)
        if q in rs.tcache:
            return
        async with sem:
            raw = await rs.pool.search(q, include_domains=REPORT_DOMAINS)
        rs.tcache[q] = {"query": q, "answer": raw.get("answer"),
                        "results": [{"title": x.get("title"), "url": x.get("url"),
                                     "content": x.get("content"), "score": x.get("score")}
                                    for x in raw.get("results", []) or []]}
        rs.searches_paid += 1
        rs._touch()

    try:
        await asyncio.gather(*(fetch(n) for n in todo))
    finally:
        rs.flush()

    out, fresh = [], 0
    for idx, name in rows:
        r = research.get(idx, {}) if research.get(idx, {}).get("input_name") == name else {}
        payloads = [rs.tcache[q] for q in queries_for(name) + [report_query(name)] if q in rs.tcache]
        reports = find_reports(name, r.get("canonical_name"), payloads)
        dated = [x for x in reports if x["as_of"]]
        best = max(dated, key=lambda x: x["as_of"]) if dated else (reports[0] if reports else None)
        # Two providers often both hold a 2025-26 count and can disagree a lot
        # (Burger King US: xmap 8,041 vs ScrapeHero 6,581, company ~6,700).
        # Where the region sets a preference, the first provider with a fresh
        # count wins instead of whichever happens to be a few days newer.
        pref = config.R.get("report_preference")
        fresh_dated = [x for x in dated if int(x["as_of"][:4]) >= FRESH_FROM_YEAR]
        for dom in (pref or []):
            cand = [x for x in fresh_dated if dom in x["url"]]
            if cand:
                best = max(cand, key=lambda x: x["as_of"])
                break
        is_fresh = bool(best and best["as_of"] and int(best["as_of"][:4]) >= FRESH_FROM_YEAR)
        fresh += is_fresh
        out.append({"row_index": idx, "input_name": name, "best": best, "fresh": is_fresh,
                    "all_reports": reports})
    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n", encoding="utf-8")
    logger.info(f"searches paid {rs.searches_paid} (~{rs.searches_paid * 2} credits) | "
                f"brands with a report: {sum(1 for x in out if x['best'])} | fresh (>= {FRESH_FROM_YEAR}): {fresh}")
    logger.info(f"written -> {OUT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
