"""
research.py - stage 1: Tavily web evidence + gpt-4.1-mini extraction of the
descriptive columns for every brand in chain_uk.csv:

    Parent Company | Website | Headquarters | Cuisine/Concept | Outlet Type

Number of Outlets is NOT taken from here - it comes from the Apify Google Maps
count (apify_outlets.py). The only outlet number read here is a rough size
hint, used to size and cost the Apify runs; it never reaches the output.

Three Tavily searches per brand (advanced = 2 credits each), then one
structured-output call that may ONLY use the numbered search results. The
model proposes, code verifies (see Verifier): the value must be inside its own
quote, the quote must be in a cited result, and a country must sit near the
brand's name. The pilot showed why: Greggs was given Saudi/UAE/Kuwait outlets
citing a LinkedIn page that names none of them.

    python research.py --names "Pret A Manger" "Greggs"   # named rows only
    python research.py --limit 10                        # first N rows
    python research.py                                   # everything
    python research.py --dry-run                         # plan + credit estimate only

Caches (never re-pay): data/tavily_cache.json keyed by query,
data/extract_cache.json keyed by EXTRACT_VERSION + row name + evidence hash.
Output: data/research.jsonl, one record per input row, keyed on row_index.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
import unicodedata
from typing import Literal, Optional
from urllib.parse import urlparse

import pandas as pd
from loguru import logger
from openai import (AsyncOpenAI, APIConnectionError, APITimeoutError, LengthFinishReasonError,
                    RateLimitError)
from pydantic import BaseModel, Field
from tavily import AsyncTavilyClient
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

import config
from countries import canonical_country, text_pattern, us_state_code

OPENAI_MODEL = "gpt-4.1-mini"
OPENAI_IN_PER_1M, OPENAI_OUT_PER_1M = 0.40, 1.60
TAVILY_CONCURRENCY = 4          # 12 collapsed throughput and got every key limited
TAVILY_CREDITS_PER_SEARCH = 2   # search_depth="advanced"
OPENAI_CONCURRENCY = 8
EXTRACT_VERSION = "v6"          # bump when the schema or prompt changes

TAVILY_CACHE = config.DATA_DIR / "tavily_cache.json"
EXTRACT_CACHE = config.DATA_DIR / "extract_cache.json"
OUT_JSONL = config.DATA_DIR / "research.jsonl"

# Region settings (config.REGIONS): L is how the home market is written in
# queries and prompts. HOME_NAMES never count as a foreign country; ADJACENT
# territories (Crown dependencies for the UK, Puerto Rico/Guam for the US)
# never make a chain International on their own.
L = config.R["label"]
HOME = config.R["country"]
HOME_NAMES = config.R["home_names"]
ADJACENT = config.R["adjacent"]
# what "abroad" means: the region, or (Europe) each brand's own home country
OUTSIDE = config.R.get("outside_phrase", f"the {L}")


FOLLOWUP_FILE = config.DATA_DIR / "followup_brands.json"


def _followup() -> dict:
    return json.loads(FOLLOWUP_FILE.read_text(encoding="utf-8")) if FOLLOWUP_FILE.exists() else {}


def queries_for(name: str) -> list[str]:
    qs = [
        f"{name} {L} chain owner parent company headquarters official website",
        f"{name} number of restaurants stores {L} and international locations",
        # Ownership churns (private equity, rescues); without this the model
        # only saw LEON's 2021 sale to the Issa brothers.
        f"{name} {L} restaurant chain acquired sold new owner 2025 2026",
    ]
    # Targeted follow-up (see --make-followup) for brands with no verified
    # head office - it is what gives McDonald's its Chicago HQ, and so its
    # International type. A second follow-up, "{name} restaurants outside the
    # UK", was tried and REMOVED: it mostly surfaced same-named businesses
    # abroad (Bill's -> Bill Granger's "bills" in Japan, Cake Box -> a Dubai
    # shop, Piccolino -> a restaurant in Israel) and a planned Greggs opening.
    fu = _followup()
    # Re-enabled per region: for the US top 30 (giants like Subway, Burger
    # King) the same-name risk that sank it in the UK is negligible, and
    # US-focused searches rarely name their outlets abroad.
    if config.R.get("intl_followup") and name in fu.get("intl", []):
        qs.append(f"{name} international locations outside the {L} countries")
    if name in fu.get("hq", []):
        qs.append(f"{name} company headquarters head office address")
    return qs


CUISINES = Literal[
    "Cafe", "Bakery", "Sandwiches", "Pizza", "Italian", "Burgers", "Fried chicken",
    "Peri-peri chicken", "Chicken wings", "Steakhouse", "Grill", "Pub", "Pub & carvery", "Carvery",
    "Bar", "Mexican", "Tex-Mex", "Latin American", "American", "British", "French", "Greek",
    "Lebanese", "Middle Eastern", "Indian", "Pan-Asian", "Japanese", "Sushi", "Chinese", "Thai",
    "Vietnamese", "Kebab", "Fish & chips", "Healthy", "Juice bar", "Desserts", "Doughnuts",
    "Cookies", "Pretzels", "Ice cream", "Casual dining",
]

# An owner quote in this shape names the UK operator, not the brand's owner
# (Slim Chickens -> "UK outlets are owned and operated by Boparan Restaurant
# Group"; TGI Fridays -> "buy the UK arm of TGI Fridays").
FRANCHISEE_SIGNS = (r"franchisee|master franchis|licensee|\buk arm\b|"
                    r"\buk (outlets|restaurants|business|operations) (are|is) (owned and )?operated by")


# -- Tavily with key rotation -------------------------------------------------

class TavilyPool:
    """Round-robin over the tracker's keys.

    A 429 / "excessive requests" is a burst limit, not a dead key: back off and
    retry. Only an auth failure or exhausted credits removes a key.
    """

    def __init__(self, keys: list[tuple[str, str]]):
        self.keys = keys
        self.dead: set[str] = set()
        self.i = 0
        self.lock = asyncio.Lock()

    async def _next(self) -> tuple[str, str]:
        async with self.lock:
            for _ in range(len(self.keys)):
                name, key = self.keys[self.i % len(self.keys)]
                self.i += 1
                if name not in self.dead:
                    return name, key
        raise RuntimeError("every Tavily key is dead or out of credits")

    async def search(self, query: str, **extra) -> dict:
        backoff = 5
        for _ in range(8):
            name, key = await self._next()
            try:
                geo = {"country": config.R["tavily_country"]} if config.R.get("tavily_country") else {}
                return await AsyncTavilyClient(api_key=key).search(
                    query,
                    search_depth="advanced",
                    max_results=6,
                    chunks_per_source=3,
                    include_answer="advanced",
                    timeout=40,
                    **geo,
                    **extra,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if any(s in msg for s in ("401", "403", "invalid api key", "deactivated", "unauthorized",
                                          "usage limit", "exceeds your plan", "432")):
                    logger.warning(f"[tavily] dropping key {name}: {str(exc)[:90]}")
                    self.dead.add(name)
                    continue
                if any(s in msg for s in ("429", "excessive", "rate", "timeout", "timed out", "connection")):
                    logger.warning(f"[tavily] transient ({str(exc)[:60]}), sleeping {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                raise
        raise RuntimeError(f"tavily search failed after retries: {query}")


# -- Extraction schema --------------------------------------------------------

class Evidence(BaseModel):
    src: list[int] = Field(default_factory=list, description="Result numbers that state this.")
    quote: str = Field(description="One continuous passage copied verbatim from one of those results (max ~25 words).")


class CountryPresence(BaseModel):
    country: str
    status: Literal["open", "planned", "closed", "unclear"]
    evidence: Evidence


class ChainFacts(BaseModel):
    brand_found: bool = Field(description=f"True only if the results clearly describe THIS brand (the {L} business the name refers to), not a same-named business elsewhere.")
    canonical_name: Optional[str] = Field(default=None, description="Brand name as the brand itself writes it, e.g. \"Nando's\", \"GAIL's Bakery\".")
    business_type: Optional[str] = Field(default=None, description="One of: Restaurant chain, Cafe chain, Bakery chain, Pub chain, Bar chain, Takeaway chain, Dessert chain, Other.")
    trading_status: Literal["trading", "administration_or_liquidation", "closed", "unknown"] = "unknown"
    trading_status_evidence: Optional[Evidence] = None
    ultimate_owner: Optional[str] = Field(default=None, description=f"The company, group, fund or family that ultimately OWNS the brand today, e.g. 'JAB Holdings' for Pret A Manger, 'Mitchells & Butlers' for Harvester. If nobody else owns it, its own operating company, e.g. 'Greggs plc'. A franchisee that runs the {L} outlets is not the owner of the brand.")
    owner_via: Optional[str] = Field(default=None, description="Intermediate company between the brand and the ultimate owner, if stated, e.g. 'The Fulham Shore'.")
    owner_evidence: Optional[Evidence] = None
    website: Optional[str] = Field(default=None, description=f"Official {L} brand website domain, e.g. 'pret.co.uk', from a result URL or text.")
    website_src: list[int] = Field(default_factory=list)
    hq_city: Optional[str] = Field(default=None, description=f"City of the BRAND's global head office (for a foreign brand, its home HQ, not the {L} franchisee or subsidiary office).")
    hq_country: Optional[str] = None
    hq_state: Optional[str] = Field(default=None, description="If the head office is in the United States: its state as a 2-letter code, e.g. 'IL'.")
    hq_evidence: Optional[Evidence] = None
    cuisine_concept: Optional[CUISINES] = Field(default=None, description="The ONE label that best describes what the brand sells.")
    cuisine_src: list[int] = Field(default_factory=list)
    countries_outside_uk: list[CountryPresence] = Field(default_factory=list, description=f"Countries outside {OUTSIDE} where results say the brand has OUTLETS - at most 15, best-evidenced first.")
    uk_outlets_hint: Optional[int] = Field(default=None, description=f"Most recent {L} outlet count stated in the results, if any. Rough size only.")
    uk_outlets_hint_src: list[int] = Field(default_factory=list)
    confidence: float = Field(description="0.0-1.0 that the facts above are correct for this brand.")
    notes: Optional[str] = Field(default=None, description="Short note on conflicts or ambiguity.")


SYSTEM_PROMPT = f"""You extract company facts about {L} food & drink brands from web search results.

You get a brand name as it appears on a {L} listing, plus numbered search results [1]..[N].
Use ONLY the numbered results. Never fill a field from your own knowledge, even when you are
sure - every value is checked against the cited results, and an unsupported value is deleted.

- Every fact cites the result numbers that state it. Quotes are ONE continuous passage copied
  verbatim; never join separate passages with "...". The quote must itself contain the value:
  the HQ quote names the city, the owner quote names the owner, a country quote names the
  country AND is about this brand (not a sister brand or the parent group's other chains).
- The name may be informal or a sub-brand ("Pizza Hut Delivery", "Greene King Pubs", "Cote").
  Match it to the {L} brand it refers to. If results are about a different, same-named business,
  set brand_found=false.
- The Tavily synthesized answer is a hint, never evidence.
- countries_outside_uk: outlets outside {OUTSIDE} only. Exporting, sourcing, head offices or
  online sales abroad do not count. Announced or future openings are "planned".
  {config.R.get("home_note", "Northern Ireland, Scotland and Wales are in the UK.")}
- ultimate_owner: follow ownership to the top as far as the results state it (a brand owned by
  a company that is itself owned by a fund -> the fund, with the company in owner_via).
  For a foreign franchise brand (e.g. KFC, Subway, Slim Chickens, TGI Fridays) the owner is the
  brand's own parent (e.g. Yum! Brands), never the company that runs the {L} outlets. Brands
  are bought and sold often: use the MOST RECENT sale, rescue or acquisition in the results,
  and say in notes when an older owner appears too.
- Head office: the BRAND's own head office. Taco Bell's is Irvine, not its parent Yum!'s
  Louisville; a pub brand inside a group may share the group's head office only if a result
  says so.
- Head office: a Companies House "registered office" is often an accountant's or liquidator's
  address. Use it only if nothing states where the brand is based, never when the company is
  in liquidation or administration.
- trading_status: administration_or_liquidation / closed only if a result says the WHOLE brand
  is in that state now; individual site closures do not count.
- cuisine_concept: pick what the brand is known for. Coffee shops and coffee-and-food shops
  (Pret, Costa, Caffe Nero, Starbucks) are "Cafe"; "Sandwiches" is for sandwich-first shops
  (Subway); peri-peri / flame-grilled chicken (Nando's, Pepe's) is "Peri-peri chicken";
  battered/fried chicken (KFC, Popeyes) is "Fried chicken"; pub chains are "Pub", or
  "Pub & carvery" when the carvery is the draw.
"""


# -- helpers ------------------------------------------------------------------

def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path, data):
    """Atomic write. UK/ lives inside OneDrive, which locks a file for a moment
    while syncing it; the replace is retried rather than crashing the run
    (a full run once died on WinError 5 this way)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    tmp.replace(path)


def _norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def number_results(payloads: list[dict]) -> tuple[list[dict], str]:
    """Flatten both searches into one numbered list, de-duplicated by URL."""
    seen, flat = set(), []
    for p in payloads:
        for r in p.get("results", []) or []:
            url = r.get("url") or ""
            if url in seen:
                continue
            seen.add(url)
            flat.append({"n": len(flat) + 1, "title": r.get("title"), "url": url,
                         "content": (r.get("content") or "")[:2000]})
    answers = " | ".join(a for a in (p.get("answer") for p in payloads) if a)
    return flat, answers


def domain_of(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    host = urlparse(s if "://" in s else f"http://{s}").netloc or s
    host = host.split("/")[0]
    return host[4:] if host.startswith("www.") else host


_OWNER_STOP = {"the", "plc", "ltd", "limited", "holdings", "holding", "group", "company", "co", "inc",
               "llc", "sarl", "s.a", "sa", "and", "&", "family", "his", "her", "of", "uk", "partners",
               "international", "enterprises", "corporation", "corp", "brands", "restaurants"}


def owner_token(owner: str) -> str | None:
    """The distinctive word an owner name must show in its source:
    'JAB Holding Company' -> 'jab', 'J D Wetherspoon plc' -> 'wetherspoon'."""
    words = [w for w in re.findall(r"[a-z0-9!']+", _norm(owner)) if w not in _OWNER_STOP]
    long_words = [w for w in words if len(w) >= 3]
    return (long_words or words or [None])[0]


class Verifier:
    """A fact survives only if (1) the value is inside the model's own quote,
    (2) the value appears in a cited result, and for countries (3) the brand is
    named within 400 characters of that mention (or in the page title).

    (1) caught Taco Bell: its quote said Irvine while the field said
    Louisville, which appeared elsewhere on the page. (3) catches a group
    article crediting one brand with a sister brand's sites (All Bar One was
    given Popworld's Australian venues). Whole-quote matching was tried and
    rejected: pages carry table pipes and markup the model's clean quote does
    not reproduce, so it deleted true facts (Pret -> JAB, Nando's -> Enthoven).
    All text is accent- and apostrophe-folded first ("Caffè" = "caffe").
    """

    def __init__(self, flat: list[dict], brand_regex: str):
        self.by_n = {r["n"]: r for r in flat}
        self.text = {n: _fold(f"{r['title']} {r['content']}") for n, r in self.by_n.items()}
        self.title = {n: _fold(r["title"] or "") for n, r in self.by_n.items()}
        self.brand_regex = brand_regex
        self.flags: list[str] = []

    def grounded(self, ev: dict | None, value_regex: str, what: str, near_brand: bool = False) -> list[int]:
        if not ev:
            self.flags.append(f"{what}: no evidence")
            return []
        refs = [n for n in ev.get("src") or [] if n in self.by_n]
        if not re.search(value_regex, _fold(ev.get("quote", ""))):
            self.flags.append(f"{what}: value not in its own quote")
            return []

        def supporting(candidates):
            ok = []
            for n in candidates:
                hits = [m.start() for m in re.finditer(value_regex, self.text[n])]
                if not hits:
                    continue
                if near_brand:
                    brand_at = [m.start() for m in re.finditer(self.brand_regex, self.text[n])]
                    if not any(abs(h - b) <= 400 for h in hits for b in brand_at):
                        continue
                ok.append(n)
            return ok

        ok = supporting(refs)
        if not ok:
            # The model's result NUMBERS are often off (McDonald's quoted
            # "Chicago" but cited the wrong results). The value is still in
            # its own quote, so accept it from any retrieved result.
            ok = supporting([n for n in self.by_n if n not in refs])
            if ok:
                self.flags.append(f"{what}: citation corrected {refs} -> {ok}")
        if not ok:
            self.flags.append(f"{what}: not stated in any result"
                              + (" near the brand name" if near_brand else ""))
        return ok

    def hq_mention(self, city: str) -> list[int]:
        """Fallback when the HQ quote is unusable: the city within 100
        characters of a head-office phrase in any result."""
        c = re.escape(_fold(city))
        pat = (rf"(headquarter|head office|hq\b|based in|head-quartered)[^.]{{0,100}}\b{c}\b"
               rf"|\b{c}\b[^.]{{0,60}}(headquarter|head office)")
        return [n for n in self.by_n if re.search(pat, self.text[n])]

    def passage(self, n: int, value_regex: str, width: int = 450) -> str:
        """The text around the value mention closest to a brand mention."""
        t = self.text[n]
        hits = [m.start() for m in re.finditer(value_regex, t)] or [0]
        brand_at = [m.start() for m in re.finditer(self.brand_regex, t)] or [hits[0]]
        h = min(hits, key=lambda h: min(abs(h - b) for b in brand_at))
        return t[max(0, h - width): h + width]


def _fold(s: str) -> str:
    """_norm plus accent folding and no apostrophes: "Caffè Nero" -> "caffe nero",
    "Nando's" -> "nandos", on both the brand name and the page text."""
    s = unicodedata.normalize("NFKD", _norm(s)).encode("ascii", "ignore").decode()
    return s.replace("'", "")


def brand_regex_for(name: str, canonical: str | None) -> str:
    """Pattern for the brand's name on a page (folded text): the whole name
    when it is short ('all bar one'), else its first two words. Words may be
    separated by any punctuation or '&'/'and', so "Chef & Brewer" matches
    "chef and brewer" and "chef & brewer" (the '&' once made it match nothing)."""
    pats = []
    for n in {name, canonical or name}:
        words = [w for w in re.findall(r"[a-z0-9]+", _fold(n)) if w not in ("the", "and")]
        if not words:
            continue
        words = words if len(words) <= 3 else words[:2]
        sep = r"[\W_]*(?:(?:and|the)[\W_]+)*"
        pats.append(r"\b" + sep.join(re.escape(w) for w in words) + r"\b")
    return "|".join(pats) or r"$^"


def format_hq(city: str, country: str, state: str | None = None) -> str | None:
    """'Solihull, England' + 'UK' -> 'Solihull, United Kingdom';
    'Seattle' + 'USA' -> 'Seattle, United States' - or, where the client file
    writes US head offices as 'Chicago, IL' (hq_style city_state), 'Seattle, WA'."""
    parts = [p.strip() for p in (city or "").split(",") if p.strip()]
    if not parts:
        return None
    # the model sometimes puts "England, UK" in the country field
    cparts = [p.strip() for p in (country or "").split(",") if p.strip()] or parts[1:]
    if any(_norm(p) in HOME_NAMES for p in cparts):
        c = HOME
    elif cparts:
        c = canonical_country(cparts[-1]) or cparts[-1]
    else:
        return None
    if config.R["hq_style"] == "city_state" and c == "United States":
        code = us_state_code(state) or next((us_state_code(p) for p in parts[1:] if us_state_code(p)), None)
        if code:
            return f"{parts[0]}, {code}"
    return f"{parts[0]}, {c}"


def hq_country_of(country: str | None, city: str | None) -> str | None:
    """Canonical country of a verified head office (HOME for any home-market name)."""
    cparts = [p.strip() for p in (country or "").split(",") if p.strip()] or \
             [p.strip() for p in (city or "").split(",") if p.strip()][1:]
    if any(_norm(p) in HOME_NAMES for p in cparts):
        return HOME
    return (canonical_country(cparts[-1]) or cparts[-1]) if cparts else None


def verify(f: ChainFacts, flat: list[dict], input_name: str) -> dict:
    d = f.model_dump()
    v = Verifier(flat, brand_regex_for(input_name, d["canonical_name"]))
    url = {r["n"]: r["url"] for r in flat}
    urls = lambda refs: [url[n] for n in refs]
    out = {k: d[k] for k in ("brand_found", "canonical_name", "business_type", "confidence", "notes")}

    # trading status - only a cited administration/closure changes it
    ts = d["trading_status"]
    refs = []
    if ts in ("administration_or_liquidation", "closed"):
        refs = v.grounded(d["trading_status_evidence"],
                          r"administration|liquidat|collapse|closed|closure|insolven", "trading_status")
    out["trading_status"] = ts if (ts in ("trading", "unknown") or refs) else "unknown"
    out["trading_status_urls"] = urls(refs)

    # parent company - the owner's distinctive word must be in the quote
    owner, refs = d["ultimate_owner"], []
    tok = owner_token(owner) if owner else None
    if tok:
        refs = v.grounded(d["owner_evidence"], rf"\b{re.escape(_fold(tok))}", f"owner '{owner}'")
        quote = _fold((d["owner_evidence"] or {}).get("quote", ""))
        # Review flag only. As a hard drop it deleted correct owners whose
        # quote merely mentions the UK franchisee (Domino's -> Domino's Pizza
        # Inc., Burger King -> RBI); the prompt rule now prevents the original
        # error (Slim Chickens -> Boparan).
        if refs and re.search(FRANCHISEE_SIGNS, quote):
            v.flags.append(f"REVIEW owner '{owner}': its quote mentions a UK franchisee/operator ({quote[:90]!r})")
    out["parent_company"] = owner if refs else None
    out["owner_via"] = d["owner_via"] if refs else None
    out["parent_company_urls"] = urls(refs)
    # passages for the owner confirmation in Researcher.one (removed before saving)
    out["_owner_passages"] = [v.passage(n, rf"\b{re.escape(_fold(tok))}") for n in refs][:2] if refs else []

    # website - the domain must appear in the evidence and not be a third-party site
    dom = domain_of(d["website"])
    if dom:
        blob = " ".join(f"{r['url']} {r['content']}" for r in flat).lower()
        third_party = ("wikipedia.org", "deliveroo", "just-eat", "justeat", "ubereats", "tripadvisor",
                       "facebook.com", "instagram.com", "linkedin.com", "companieshouse", "company-information",
                       "companycheck", "endole", "yell.com", "google.com", "bloomberg", "crunchbase",
                       "scrapehero", "xmap.ai", "zoominfo")
        if dom not in blob:
            v.flags.append(f"website '{dom}' not in evidence, dropped")
            dom = None
        elif any(t in dom for t in third_party):
            v.flags.append(f"website '{dom}' is a third-party site, dropped")
            dom = None
    out["website"] = dom

    # headquarters -> "City, Country", UK nations folded into United Kingdom;
    # the city itself must be inside the HQ quote
    city = ((d["hq_city"] or "").split(",")[0]).strip()
    refs = []
    if city:
        refs = v.grounded(d["hq_evidence"], re.escape(_fold(city)), f"headquarters '{city}'")
        if not refs:
            refs = v.hq_mention(city)
            if refs:
                v.flags.append(f"headquarters '{city}' accepted from a head-office mention in {refs}")
    hq = format_hq(d["hq_city"], d["hq_country"], d.get("hq_state")) if refs else None
    if hq and out["trading_status"] == "administration_or_liquidation":
        v.flags.append(f"headquarters '{hq}' withheld: company in liquidation/administration")
        hq, refs = None, []
    out["headquarters"] = hq
    out["hq_country"] = hq_country_of(d["hq_country"], d["hq_city"]) if hq else None
    out["headquarters_urls"] = urls(refs)

    # cuisine / concept - a summary label, so a citation is enough
    csrc = [n for n in d["cuisine_src"] if n in url]
    out["cuisine_concept"] = d["cuisine_concept"] if csrc else None
    if d["cuisine_concept"] and not csrc:
        v.flags.append(f"cuisine '{d['cuisine_concept']}' uncited, dropped")

    # outlet type - International needs a cited OPEN outlet in a named country
    open_c, adjacent, not_counted = [], [], []
    for c in d["countries_outside_uk"]:
        raw_name = c["country"].strip()
        if _norm(raw_name) in HOME_NAMES:
            continue
        name = canonical_country(raw_name)
        if not name:
            not_counted.append(f"{raw_name} (not a country)")
            continue
        if c["status"] != "open":
            not_counted.append(f"{name} ({c['status']})")
            continue
        refs = v.grounded(c["evidence"], text_pattern(name), f"country '{name}'", near_brand=True)
        if refs and name not in {x["country"] for x in open_c + adjacent}:
            passages = [v.passage(n, text_pattern(name)) for n in refs]
            (adjacent if name in ADJACENT else open_c).append(
                {"country": name, "urls": urls(refs), "passages": passages})
    # outlet_type is set in Researcher.one, after each country claim is confirmed
    out["countries_outside_uk"] = open_c
    out["uk_adjacent_territories"] = adjacent
    out["countries_not_counted"] = not_counted

    # size hint for Apify planning only: the number must appear in a cited result
    hint = d["uk_outlets_hint"]
    if hint:
        pat = rf"\b({re.escape(f'{hint:,}')}|{hint})\b"
        refs = [n for n in d["uk_outlets_hint_src"] if n in v.text and re.search(pat, v.text[n])]
        if not refs:
            v.flags.append(f"size hint {hint}: number not in cited result(s)")
            hint = None
        out["uk_outlets_hint_urls"] = urls(refs)
    out["uk_outlets_hint"] = hint
    out["flags"] = v.flags
    return out


# -- country-claim confirmation -----------------------------------------------

class OutletClaim(BaseModel):
    states_outlet_in_country: bool
    reason: str = Field(description="One short sentence.")


CONFIRM_PROMPT = """You check one claim against one passage from a web page. Answer ONLY from the passage.

Answer true only if the passage itself says that THIS brand has at least one outlet (restaurant,
cafe, shop, bar, pub, kiosk) trading in the named country. These do NOT count: the website's own
menu or list of cities it covers, the publisher's or a supplier's office addresses, other brands
of the same group, planned or future openings, and products sold abroad."""


class OwnerClaim(BaseModel):
    states_owner_of_brand: bool
    reason: str = Field(description="One short sentence.")


OWNER_PROMPT = """You check one claim against one passage from a web page. Answer ONLY from the passage.

Answer true only if the passage itself says that the named owner owns, acquired, or is the parent
company of THIS brand. False if the ownership statement is about another chain mentioned nearby
(articles often list several chains), about a franchisee buying some of the brand's restaurants,
or about a deal that is only proposed."""


# -- pipeline -----------------------------------------------------------------

class Researcher:
    def __init__(self):
        dead = self._dead_tavily_keys()
        keys = [(n, k) for n, k in config.load_keys("tavily") if n not in dead]
        logger.info(f"Tavily keys usable: {len(keys)} (skipping {len(dead)} known-dead)")
        self.pool = TavilyPool(keys)
        self.oa = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.tcache = _load(TAVILY_CACHE)
        self.xcache = _load(EXTRACT_CACHE)
        self.tsem = asyncio.Semaphore(TAVILY_CONCURRENCY)
        self.osem = asyncio.Semaphore(OPENAI_CONCURRENCY)
        self.tok_in = self.tok_out = 0
        self.searches_paid = 0
        self._writes = 0

    def _touch(self) -> None:
        """Caches are flushed every 10 new entries, not on every call: each
        rewrite of a large JSON inside OneDrive invites a sync lock."""
        self._writes += 1
        if self._writes % 10 == 0:
            self.flush()

    def flush(self) -> None:
        _save(TAVILY_CACHE, self.tcache)
        _save(EXTRACT_CACHE, self.xcache)

    @staticmethod
    def _dead_tavily_keys() -> set[str]:
        p = config.KEY_BALANCES
        if not p.exists():
            return set()
        rows = json.loads(p.read_text(encoding="utf-8")).get("tavily", [])
        return {r["name"] for r in rows if str(r.get("status")).startswith(("HTTP 401", "HTTP 403"))}

    async def search(self, q: str) -> dict:
        if q in self.tcache:
            return self.tcache[q]
        async with self.tsem:
            raw = await self.pool.search(q)
        self.searches_paid += 1
        payload = {"query": q, "answer": raw.get("answer"),
                   "results": [{"title": r.get("title"), "url": r.get("url"),
                                "content": r.get("content"), "score": r.get("score")}
                               for r in raw.get("results", []) or []]}
        self.tcache[q] = payload
        self._touch()
        return payload

    @retry(reraise=True, stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=20),
           retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)))
    async def _extract_call(self, user_prompt: str) -> ChainFacts:
        # max_tokens: one brand once ran to the 32,768-token limit (an endless
        # list) and the parse error killed the whole batch. A normal answer is
        # ~500 tokens; LengthFinishReasonError is retried once, then recorded.
        for attempt in range(2):
            # the retry says why: Wendy's (US) ran past the limit twice with an
            # identical prompt - it kept listing countries
            prompt = user_prompt if attempt == 0 else (
                user_prompt + "\n\nIMPORTANT: your previous answer was cut off for length. List at most 8 "
                "countries in countries_outside_uk and keep every quote under 20 words.")
            try:
                async with self.osem:
                    c = await self.oa.chat.completions.parse(
                        model=OPENAI_MODEL, temperature=0, response_format=ChainFacts, max_tokens=3000,
                        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                  {"role": "user", "content": prompt}])
                break
            except LengthFinishReasonError:
                self.tok_out += 3000
                if attempt == 1:
                    return ChainFacts(brand_found=False, confidence=0.0,
                                      notes="extraction failed: model output ran past the length limit twice")
        if c.usage:
            self.tok_in += c.usage.prompt_tokens
            self.tok_out += c.usage.completion_tokens
        return c.choices[0].message.parsed or ChainFacts(brand_found=False, confidence=0.0,
                                                          notes="no parseable response")

    @retry(reraise=True, stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=20),
           retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)))
    async def _confirm_call(self, brand: str, country: str, passage: str) -> dict:
        key = f"confirm|v1|{brand}|{country}|{hashlib.sha256(passage.encode()).hexdigest()[:16]}"
        if key in self.xcache:
            return self.xcache[key]
        async with self.osem:
            c = await self.oa.chat.completions.parse(
                model=OPENAI_MODEL, temperature=0, response_format=OutletClaim,
                messages=[{"role": "system", "content": CONFIRM_PROMPT},
                          {"role": "user", "content": f"Brand: {brand}\nCountry: {country}\n\nPassage:\n{passage}"}])
        if c.usage:
            self.tok_in += c.usage.prompt_tokens
            self.tok_out += c.usage.completion_tokens
        parsed = c.choices[0].message.parsed
        res = parsed.model_dump() if parsed else {"states_outlet_in_country": False, "reason": "no response"}
        self.xcache[key] = res
        self._touch()
        return res

    @retry(reraise=True, stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=20),
           retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)))
    async def _confirm_owner_call(self, brand: str, owner: str, passage: str) -> dict:
        key = f"confirm_owner|v1|{brand}|{owner}|{hashlib.sha256(passage.encode()).hexdigest()[:16]}"
        if key in self.xcache:
            return self.xcache[key]
        async with self.osem:
            c = await self.oa.chat.completions.parse(
                model=OPENAI_MODEL, temperature=0, response_format=OwnerClaim,
                messages=[{"role": "system", "content": OWNER_PROMPT},
                          {"role": "user", "content": f"Brand: {brand}\nOwner: {owner}\n\nPassage:\n{passage}"}])
        if c.usage:
            self.tok_in += c.usage.prompt_tokens
            self.tok_out += c.usage.completion_tokens
        parsed = c.choices[0].message.parsed
        res = parsed.model_dump() if parsed else {"states_owner_of_brand": False, "reason": "no response"}
        self.xcache[key] = res
        self._touch()
        return res

    async def confirm_owner(self, brand: str, out: dict) -> None:
        """Keep the parent company only if a cited passage says it owns THIS
        brand. Wendy's (US) was given the TriArtisan/Yadav/Treville consortium
        from a chain-closures roundup - that $620m deal was Denny's."""
        passages = out.pop("_owner_passages", [])
        owner = out.get("parent_company")
        if not owner or not passages:
            return
        brand_key = re.sub(r"[^a-z0-9]", "", _fold(brand))
        own_company = lambda o: bool(o) and brand_key[:6] in re.sub(r"[^a-z0-9]", "", _fold(o))
        if own_company(owner):
            return   # "Chick-fil-A, Inc." owns Chick-fil-A - nothing to misattribute
        verdicts = [await self._confirm_owner_call(brand, owner, p) for p in passages]
        if any(vd["states_owner_of_brand"] for vd in verdicts):
            out["owner_passage"] = passages[0][:400]
            return
        # One follow-up search for the completed deal: Jersey Mike's -> Blackstone
        # was rejected only because its passage said the deal was "expected to close".
        tok = owner_token(owner)
        if tok:
            payload = await self.search(f"{brand} acquired by {owner} deal completed")
            flat2, _ = number_results([payload])
            v2 = Verifier(flat2, brand_regex_for(brand, brand))
            pat = rf"\b{re.escape(_fold(tok))}"
            more = [(r["url"], v2.passage(r["n"], pat)) for r in flat2 if re.search(pat, v2.text[r["n"]])][:2]
            for url, p in more:
                if (await self._confirm_owner_call(brand, owner, p))["states_owner_of_brand"]:
                    out["owner_passage"] = p[:400]
                    out["parent_company_urls"] = [url]
                    out["flags"].append(f"owner '{owner}' confirmed by a follow-up search")
                    return
        out["flags"].append(f"owner '{owner}' rejected: {verdicts[0]['reason'][:140]}")
        via = out.get("owner_via")
        if own_company(via):
            # the brand's own company, named in the same evidence (Wendy's ->
            # The Wendy's Company) - it cannot belong to another chain
            out["parent_company"], out["owner_via"] = via, None
            out["flags"].append(f"parent set to the brand's own company '{via}'")
            return
        out["parent_company"] = out["owner_via"] = None
        out["parent_company_urls"] = []

    async def confirm_countries(self, brand: str, out: dict) -> None:
        """Keep a country only if one of its cited passages states an outlet
        there. All Bar One's 'Australia' was DesignMyNight's site-wide city
        menu; Pizza GoGo's 'US' was the publisher's office footer."""
        kept = []
        for c in out["countries_outside_uk"]:
            verdicts = [await self._confirm_call(brand, c["country"], p) for p in c["passages"][:2]]
            yes = [i for i, vd in enumerate(verdicts) if vd["states_outlet_in_country"]]
            if yes:
                kept.append({"country": c["country"], "url": c["urls"][yes[0]],
                             "passage": c["passages"][yes[0]][:400]})
            else:
                out["countries_not_counted"].append(f"{c['country']} (passage does not state an outlet)")
                out["flags"].append(f"country '{c['country']}' rejected: {verdicts[0]['reason'][:120]}")
        out["countries_outside_uk"] = kept
        for c in out["uk_adjacent_territories"]:
            c.pop("passages", None)

    async def one(self, row_index: int, name: str) -> dict:
        payloads = [await self.search(q) for q in queries_for(name)]
        flat, answers = number_results(payloads)
        evidence = "\n\n".join(f"[{r['n']}] {r['title']}\nURL: {r['url']}\n{r['content']}" for r in flat)
        user_prompt = (f"Brand name on the {L} listing: {name}\n\n"
                       f"Tavily synthesized answer (hint only): {answers or '(none)'}\n\n"
                       f"Search results:\n{evidence or '(no results)'}")
        ckey = f"{EXTRACT_VERSION}|{name}|{hashlib.sha256(user_prompt.encode()).hexdigest()[:16]}"
        cached = self.xcache.get(ckey)
        if cached and not str(cached.get("notes") or "").startswith("extraction failed"):
            facts = ChainFacts(**cached)
        else:
            facts = await self._extract_call(user_prompt)
            # a failed extraction is never cached, so the next run retries it
            if not str(facts.notes or "").startswith("extraction failed"):
                self.xcache[ckey] = facts.model_dump()
                self._touch()
        out = verify(facts, flat, name)
        await self.confirm_owner(facts.canonical_name or name, out)
        await self.confirm_countries(facts.canonical_name or name, out)
        # A brand headquartered abroad trades in its home country by definition
        # (Taco Bell's US outlets were not spelled out in any UK-focused result).
        hq_country = out.get("hq_country")
        region_countries = set(config.R.get("countries") or [])
        if config.R.get("home_from_hq"):
            # Europe: International = outlets in any country other than the
            # brand's own home (HQ) country
            abroad = [c["country"] for c in out["countries_outside_uk"] if c["country"] != hq_country]
            if abroad:
                out["outlet_type"] = "International"
                out["outlet_type_basis"] = "outlets in " + ", ".join(abroad)
            elif hq_country and hq_country not in region_countries:
                out["outlet_type"] = "International"
                out["outlet_type_basis"] = f"headquartered in {hq_country}, outside the EU"
            else:
                out["outlet_type"] = "Regional"
                out["outlet_type_basis"] = f"no confirmed outlet outside {hq_country or 'its home country'}"
        elif HOME is None:
            # GCC-style region: International = headquartered, or trading, outside the region
            if out["countries_outside_uk"]:
                out["outlet_type"] = "International"
                out["outlet_type_basis"] = "outlets in " + ", ".join(c["country"] for c in out["countries_outside_uk"])
            elif hq_country and hq_country not in region_countries:
                out["outlet_type"] = "International"
                out["outlet_type_basis"] = f"headquartered in {hq_country}"
            else:
                out["outlet_type"] = "Regional"
                out["outlet_type_basis"] = f"no confirmed outlet outside {OUTSIDE}"
        elif out["countries_outside_uk"]:
            out["outlet_type"] = "International"
            out["outlet_type_basis"] = "outlets in " + ", ".join(c["country"] for c in out["countries_outside_uk"])
        elif hq_country and hq_country != HOME:
            out["outlet_type"] = "International"
            out["outlet_type_basis"] = f"headquartered in {hq_country}"
        else:
            out["outlet_type"] = "Regional"
            out["outlet_type_basis"] = f"no confirmed outlet outside the {L}"
        return {"row_index": row_index, "input_name": name, "version": EXTRACT_VERSION,
                **out, "n_results": len(flat), "raw_model_output": facts.model_dump()}


load_rows = config.load_rows   # [(row_index, name)] in the client's order


async def main_async(args) -> None:
    if args.make_followup:
        recs = [json.loads(l) for l in OUT_JSONL.read_text(encoding="utf-8").splitlines()]
        fu = {"intl": sorted(r["input_name"] for r in recs if r.get("outlet_type") == "Regional"),
              "hq": sorted(r["input_name"] for r in recs if not r.get("headquarters"))}
        FOLLOWUP_FILE.write_text(json.dumps(fu, indent=1, ensure_ascii=False), encoding="utf-8")
        logger.info(f"follow-up: {len(fu['intl'])} Regional brands, {len(fu['hq'])} without an HQ -> {FOLLOWUP_FILE}")
        return
    rows = load_rows()
    assert len({i for i, _ in rows}) == len(rows), "row_index not unique"
    if args.names:
        wanted = {n.lower() for n in args.names}
        rows = [r for r in rows if r[1].lower() in wanted]
        missing = wanted - {r[1].lower() for r in rows}
        if missing:
            raise SystemExit(f"names not in {config.INPUT_CSV.name}: {sorted(missing)}")
    if args.limit:
        rows = rows[: args.limit]

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tcache = _load(TAVILY_CACHE)
    new_q = sum(1 for _, n in rows for q in queries_for(n) if q not in tcache)
    logger.info(f"{len(rows)} brands | {new_q} uncached searches | "
                f"~{new_q * TAVILY_CREDITS_PER_SEARCH} Tavily credits")
    if args.dry_run:
        return

    r = Researcher()

    async def safe_one(i: int, n: str) -> dict | None:
        # one brand's failure must not discard the other 149
        try:
            return await r.one(i, n)
        except Exception as exc:
            logger.error(f"[{i}] {n}: {type(exc).__name__}: {str(exc)[:200]}")
            return None

    try:
        results = [x for x in await asyncio.gather(*(safe_one(i, n) for i, n in rows)) if x]
    finally:
        r.flush()   # keep everything already paid for
    if len(results) < len(rows):
        logger.warning(f"{len(rows) - len(results)} brand(s) failed - re-run to retry them (cached work is free)")

    # The input list can change between runs; a row_index from an older list
    # would point at a different brand, so rows whose name no longer matches
    # the current input - or that were built by an older schema - are dropped
    # rather than carried forward. (Wetherspoon sat at row 8 in both lists.)
    current = dict(load_rows())
    done = {}
    if OUT_JSONL.exists():
        for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if current.get(rec["row_index"]) == rec["input_name"] and rec.get("version") == EXTRACT_VERSION:
                done[rec["row_index"]] = rec
    for rec in results:
        done[rec["row_index"]] = rec
    OUT_JSONL.write_text("\n".join(json.dumps(done[k], ensure_ascii=False) for k in sorted(done)) + "\n",
                         encoding="utf-8")

    cost = r.tok_in / 1e6 * OPENAI_IN_PER_1M + r.tok_out / 1e6 * OPENAI_OUT_PER_1M
    logger.info(f"done: {len(results)} brands | Tavily searches paid {r.searches_paid} "
                f"(~{r.searches_paid * TAVILY_CREDITS_PER_SEARCH} credits) | "
                f"OpenAI {r.tok_in:,} in / {r.tok_out:,} out = ${cost:.4f} | "
                f"dead keys this run: {sorted(r.pool.dead) or 'none'}")
    logger.info(f"written -> {OUT_JSONL} ({len(done)} rows total)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", nargs="*", help="only these input names (exact, case-insensitive)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--make-followup", action="store_true",
                    help="list brands that are Regional or lack an HQ for one targeted search each, then exit")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
