"""
region_counts.py - "Number of Outlets" for multi-country regions (EU-27, GCC):
per-country figures, summed over the region's own countries only.

Per brand, two free sources:
  A. dated per-country location reports (ScrapeHero / xmap):
     "There are 1,420 McDonald's locations in Germany as of June 2, 2026."
     Countries asked: GCC - all six; Europe - the brand's home country and
     confirmed outlet countries (from research.py), and all 27 EU countries
     for a brand that is International.
  B. Tavily + gpt-4.1-mini: per-country and regional figures stated by the
     company / franchisee / press. Every number must appear in a cited result.

Per country the dated report wins over a web figure; the region total is the
sum over the countries that have one (the countries counted are recorded).
A stated regional total ("EU", "GCC") is kept as a second candidate; where the
two disagree the client's estimate breaks the tie, and the row is flagged.

    CHAIN_REGION=europe python region_counts.py            # all brands
    CHAIN_REGION=gcc python region_counts.py --names KFC   # one brand
    CHAIN_REGION=gcc python region_counts.py --dry-run     # search count only

Output: data/region_counts.jsonl, one record per brand.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

import config
from countries import canonical_country
from location_reports import parse_date
from research import (OPENAI_MODEL, TAVILY_CACHE, Evidence, Researcher, _fold, _load, brand_regex_for,
                      load_rows, number_results)

REPORT_DOMAINS = ["scrapehero.com", "xmap.ai"]
FRESH_FROM_YEAR = 2025
COUNTRIES = config.R["countries"]
SCOPE = config.R["scope_label"]
OUT = config.DATA_DIR / "region_counts.jsonl"
BIG_EU = {"Germany", "France", "Spain", "Italy", "Netherlands", "Poland", "Belgium", "Austria"}

# How a report names each country (strict: a city such as "Dubai" is not the UAE)
COUNTRY_TEXT = {
    "United Arab Emirates": r"(?:the )?(?:united arab emirates|uae)",
    "Czech Republic": r"(?:the )?(?:czech republic|czechia)",
    "Netherlands": r"(?:the )?netherlands",
}


def country_regex(c: str) -> str:
    return COUNTRY_TEXT.get(c, r"(?:the )?" + re.escape(c.lower()))


def report_query(name: str, country: str) -> str:
    the = "the " if country in ("United Arab Emirates", "Netherlands", "Czech Republic") else ""
    return f"number of {name} locations in {the}{country}"


def web_queries(name: str) -> list[str]:
    if config.REGION == "gcc":
        return [f"{name} number of stores Saudi Arabia UAE Kuwait Qatar Bahrain Oman",
                f"{name} GCC Middle East franchise number of restaurants 2025 2026"]
    return [f"{name} number of restaurants per country in Europe 2025",
            f"{name} European restaurants Germany France Spain Italy count annual report 2025"]


def parse_country_reports(name: str, canonical: str | None, payload: dict, country: str) -> list[dict]:
    brand = brand_regex_for(name, canonical)
    compact = {re.sub(r"[^a-z0-9]", "", _fold(n)) for n in (name, canonical or name)}
    rx = re.compile(r"there (?:are|is) (?P<n>[\d,]+) (?P<what>[^.]{0,120}?)"
                    r"(?:locations|stores|restaurants|shops|outlets|cafes|bakeries)"
                    r"(?P<mid>[^.]{0,60}?)in " + country_regex(country) + r"\b"
                    r"(?:[^.]{0,20}?as of (?P<date>[a-z]+ \d{1,2},? 20\d\d))?")
    found = []
    for r in payload.get("results", []) or []:
        url = r.get("url") or ""
        if not any(d in url for d in REPORT_DOMAINS):
            continue
        text = _fold(f"{r.get('title') or ''}. {r.get('content') or ''}")
        for m in rx.finditer(text):
            s = m.group(0)
            if " without " in s:
                continue
            squashed = re.sub(r"[^a-z0-9]", "", m.group("what"))
            if not (re.search(brand, s) or any(c and c[:6] in squashed for c in compact)):
                continue
            d = parse_date(m.group("date"))
            found.append({"count": int(m.group("n").replace(",", "")), "as_of": d.strftime("%Y-%m-%d") if d else None,
                          "url": url, "sentence": s[:200]})
    return found


# -- web extraction ---------------------------------------------------------------

class Figure(BaseModel):
    scope: str = Field(description="A single country name (e.g. 'Germany', 'Saudi Arabia'), or an aggregate as "
                                   "written: 'EU', 'Europe', 'Europe incl. UK', 'GCC', 'Middle East', 'MENA', "
                                   "'worldwide', 'international'.")
    count: int
    kind: Literal["current outlets", "planned or target", "other"] = Field(
        description="'current outlets' = restaurants/cafes/stores trading now. Openings, pipelines and "
                    "targets are 'planned or target'; employees, franchisees, kiosks counted separately are 'other'.")
    as_of: Optional[str] = Field(default=None, description="Year or date the figure refers to, if stated.")
    evidence: Evidence


class RegionFigures(BaseModel):
    figures: list[Figure] = Field(default_factory=list, description="Every outlet count in the results for THIS brand.")
    notes: Optional[str] = None


FIG_PROMPT = f"""You extract outlet counts for one restaurant brand from web search results.
Use ONLY the numbered results; never your own knowledge. For every count give its scope (one
country, or the aggregate exactly as the source words it), whether it is current outlets or a
plan/target, its date, the result numbers and a short verbatim quote containing the number.
List every count you see, old and new - do not choose between them. Counts for sister brands of
the same group, or for the group as a whole, are not counts for this brand. The region of
interest is {SCOPE}: {', '.join(COUNTRIES)}."""


def verified_figures(f: RegionFigures, flat: list[dict]) -> list[dict]:
    text = {r["n"]: _fold(f"{r['title']} {r['content']}") for r in flat}
    url = {r["n"]: r["url"] for r in flat}
    out = []
    for fig in f.figures:
        pat = rf"\b({re.escape(f'{fig.count:,}')}|{fig.count})\b"
        refs = [n for n in fig.evidence.src if n in text and re.search(pat, text[n])]
        if not refs or not re.search(pat, _fold(fig.evidence.quote)):
            continue
        out.append({"scope": fig.scope.strip(), "count": fig.count, "kind": fig.kind, "as_of": fig.as_of,
                    "url": url[refs[0]], "quote": fig.evidence.quote[:200]})
    return out


def year(s: str | None) -> int:
    ys = [int(y) for y in re.findall(r"(?:19|20)\d\d", s or "")]
    return max(ys) if ys else 0


def is_region_aggregate(scope: str) -> bool:
    s = _fold(scope)
    if config.REGION == "gcc":
        return bool(re.search(r"\bgcc\b|gulf cooperation|gulf states", s))
    return bool(re.search(r"\beu\b|eu-27|eu27|european union", s))


# -- pipeline -----------------------------------------------------------------------

async def main_async(args) -> None:
    rows = load_rows()
    if args.names:
        rows = [r for r in rows if r[1] in args.names]
    research = {}
    rp = config.DATA_DIR / "research.jsonl"
    if rp.exists():
        research = {r["input_name"]: r for r in map(json.loads, rp.read_text(encoding="utf-8").splitlines())}

    def countries_for(name: str) -> list[str]:
        if config.REGION == "gcc":
            return COUNTRIES
        r = research.get(name, {})
        hq = r.get("hq_country")
        found = {c["country"] for c in r.get("countries_outside_uk", [])}
        if not hq or hq not in COUNTRIES:
            return COUNTRIES          # McDonald's, Costa ...: may trade in any EU country
        if r.get("outlet_type") == "International":
            # an EU brand abroad: home, confirmed countries and the biggest EU markets
            # (all 27 for every brand was 732 searches, mostly empty for small states)
            return [c for c in COUNTRIES if c == hq or c in found or c in BIG_EU]
        return [c for c in COUNTRIES if c == hq or c in found] or [hq]

    plan = {name: countries_for(name) for _, name in rows}
    tcache = _load(TAVILY_CACHE)
    todo_rep = [(n, c) for n, cs in plan.items() for c in cs if report_query(n, c) not in tcache]
    todo_web = [(n, q) for n in plan for q in web_queries(n) if q not in tcache]
    logger.info(f"{len(rows)} brands | {len(todo_rep)} report searches + {len(todo_web)} web searches uncached "
                f"| ~{2 * (len(todo_rep) + len(todo_web))} Tavily credits")
    if args.dry_run:
        return

    rs = Researcher()
    sem = asyncio.Semaphore(8)   # 18 keys; the UK runs used 4

    async def fetch(q: str, **extra) -> dict:
        if q in rs.tcache:
            return rs.tcache[q]
        async with sem:
            raw = await rs.pool.search(q, **extra)
        rs.tcache[q] = {"query": q, "answer": raw.get("answer"),
                        "results": [{"title": x.get("title"), "url": x.get("url"), "content": x.get("content")}
                                    for x in raw.get("results", []) or []]}
        rs.searches_paid += 1
        rs._touch()
        return rs.tcache[q]

    async def web_figures(name: str) -> list[dict]:
        payloads = [await fetch(q) for q in web_queries(name)]
        flat, _ = number_results(payloads)
        evidence = "\n\n".join(f"[{r['n']}] {r['title']}\nURL: {r['url']}\n{r['content']}" for r in flat)
        prompt = f"Brand: {name}\n\nSearch results:\n{evidence or '(no results)'}"
        key = f"figs|v1|{name}|{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
        if key not in rs.xcache:
            async with rs.osem:
                c = await rs.oa.chat.completions.parse(
                    model=OPENAI_MODEL, temperature=0, response_format=RegionFigures, max_tokens=3000,
                    messages=[{"role": "system", "content": FIG_PROMPT}, {"role": "user", "content": prompt}])
            if c.usage:
                rs.tok_in += c.usage.prompt_tokens
                rs.tok_out += c.usage.completion_tokens
            parsed = c.choices[0].message.parsed
            rs.xcache[key] = parsed.model_dump() if parsed else {"figures": []}
            rs._touch()
        return verified_figures(RegionFigures(**rs.xcache[key]), flat)

    async def one(idx: int, name: str) -> dict:
        canonical = research.get(name, {}).get("canonical_name")
        per_country = {}
        for c in plan[name]:
            reps = parse_country_reports(name, canonical, await fetch(report_query(name, c), include_domains=REPORT_DOMAINS), c)
            dated = [x for x in reps if x["as_of"]]
            pref = config.R.get("report_preference") or []
            best = None
            for dom in pref:
                cand = [x for x in dated if dom in x["url"] and int(x["as_of"][:4]) >= FRESH_FROM_YEAR]
                if cand:
                    best = max(cand, key=lambda x: x["as_of"])
                    break
            best = best or (max(dated, key=lambda x: x["as_of"]) if dated else None)
            if best:
                per_country[c] = {**best, "source": "report", "fresh": int(best["as_of"][:4]) >= FRESH_FROM_YEAR}
        try:
            figs = await web_figures(name)
        except Exception as exc:
            logger.warning(f"{name}: web figures failed: {exc}")
            figs = []
        current = [f for f in figs if f["kind"] == "current outlets"]
        for f in current:
            c = canonical_country(f["scope"])
            if c in COUNTRIES:
                have = per_country.get(c)
                # a fresh dated report beats a web figure; otherwise the more recent wins
                if not have or (not have.get("fresh") and year(f["as_of"]) >= year(have.get("as_of"))):
                    per_country[c] = {"count": f["count"], "as_of": f["as_of"], "url": f["url"],
                                      "sentence": f["quote"], "source": "web", "fresh": year(f["as_of"]) >= FRESH_FROM_YEAR}
        aggregates = [f for f in current if is_region_aggregate(f["scope"])]
        agg = max(aggregates, key=lambda f: (year(f["as_of"]), f["count"])) if aggregates else None
        total = sum(v["count"] for v in per_country.values()) if per_country else None
        return {"row_index": idx, "input_name": name, "countries_searched": plan[name],
                "per_country": per_country, "country_sum": total, "countries_counted": sorted(per_country),
                "region_aggregate": agg, "other_figures": [f for f in figs if f not in aggregates][:12]}

    try:
        results = await asyncio.gather(*(one(i, n) for i, n in rows))
    finally:
        rs.flush()
    done = {}
    if OUT.exists():
        done = {r["row_index"]: r for r in map(json.loads, OUT.read_text(encoding="utf-8").splitlines())}
    done.update({r["row_index"]: r for r in results})
    OUT.write_text("\n".join(json.dumps(done[k], ensure_ascii=False) for k in sorted(done)) + "\n", encoding="utf-8")
    cost = rs.tok_in / 1e6 * 0.40 + rs.tok_out / 1e6 * 1.60
    logger.info(f"done: {len(results)} brands | Tavily searches paid {rs.searches_paid} | OpenAI ${cost:.4f} "
                f"| with a country sum {sum(1 for r in results if r['country_sum'])} "
                f"| with a {SCOPE} aggregate {sum(1 for r in results if r['region_aggregate'])}")


async def fill_async(args) -> None:
    """Second pass for brands whose country sum is clearly partial (Starbucks EU
    had France, Germany and Spain only): one web search per region country with
    no figure yet, one extraction per brand, numbers verified in their source.
    A filled country is marked source 'web' like the first pass."""
    recs = {r["input_name"]: r for r in map(json.loads, OUT.read_text(encoding="utf-8").splitlines())}
    rs = Researcher()
    sem = asyncio.Semaphore(8)

    async def fetch(q: str) -> dict:
        if q in rs.tcache:
            return rs.tcache[q]
        async with sem:
            raw = await rs.pool.search(q)
        rs.tcache[q] = {"query": q, "answer": raw.get("answer"),
                        "results": [{"title": x.get("title"), "url": x.get("url"),
                                     "content": (x.get("content") or "")[:800]}
                                    for x in (raw.get("results") or [])[:4]]}
        rs.searches_paid += 1
        rs._touch()
        return rs.tcache[q]

    async def one(name: str) -> None:
        r = recs[name]
        missing = [c for c in COUNTRIES if c not in r["per_country"]]
        payloads = await asyncio.gather(*(fetch(f"{name} number of restaurants in {c} 2025") for c in missing))
        flat, _ = number_results(list(payloads))
        evidence = "\n\n".join(f"[{x['n']}] {x['title']}\nURL: {x['url']}\n{x['content']}" for x in flat)
        prompt = f"Brand: {name}\n\nSearch results:\n{evidence or '(no results)'}"
        key = f"figs_fill|v1|{name}|{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
        if key not in rs.xcache:
            async with rs.osem:
                c = await rs.oa.chat.completions.parse(
                    model=OPENAI_MODEL, temperature=0, response_format=RegionFigures, max_tokens=3000,
                    messages=[{"role": "system", "content": FIG_PROMPT}, {"role": "user", "content": prompt}])
            if c.usage:
                rs.tok_in += c.usage.prompt_tokens
                rs.tok_out += c.usage.completion_tokens
            parsed = c.choices[0].message.parsed
            rs.xcache[key] = parsed.model_dump() if parsed else {"figures": []}
            rs._touch()
        added = []
        for f in verified_figures(RegionFigures(**rs.xcache[key]), flat):
            ctry = canonical_country(f["scope"])
            if f["kind"] == "current outlets" and ctry in missing and ctry not in r["per_country"]:
                r["per_country"][ctry] = {"count": f["count"], "as_of": f["as_of"], "url": f["url"],
                                          "sentence": f["quote"], "source": "web",
                                          "fresh": year(f["as_of"]) >= FRESH_FROM_YEAR, "fill_pass": True}
                added.append(ctry)
        r["country_sum"] = sum(v["count"] for v in r["per_country"].values()) or None
        r["countries_counted"] = sorted(r["per_country"])
        logger.info(f"{name}: +{len(added)} countries {added} -> sum {r['country_sum']}")

    try:
        await asyncio.gather(*(one(n) for n in args.fill))
    finally:
        rs.flush()
    OUT.write_text("\n".join(json.dumps(recs[k], ensure_ascii=False)
                             for k in sorted(recs, key=lambda k: recs[k]["row_index"])) + "\n", encoding="utf-8")
    cost = rs.tok_in / 1e6 * 0.40 + rs.tok_out / 1e6 * 1.60
    logger.info(f"fill done | Tavily searches paid {rs.searches_paid} | OpenAI ${cost:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", nargs="*")
    ap.add_argument("--fill", nargs="*", help="brands whose country sum is partial: search the missing countries")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(fill_async(args) if args.fill else main_async(args))


if __name__ == "__main__":
    main()
