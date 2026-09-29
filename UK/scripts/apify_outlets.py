"""
apify_outlets.py - stage 2: the "Number of Outlets" column, counted on Google
Maps with Apify's compass/crawler-google-places - the same machinery as the
UAE brand runs in talabat/map_version2. It imports rather than copies:

    run_group()                  talabat/Google_Place/apify/scripts/run_brand_scraper.py
    _normalize_quotes,
    is_restaurant_category,
    clean_maps_url               talabat/Google_Place/apify/scripts/process_brand.py

What differs for the UK:
  * one search per brand with locationQuery "United Kingdom" - the actor
    splits a country into sub-areas itself, so the per-city query list the
    UAE runs used is not needed;
  * brands too big for one key's budget are split by UK region, one run each;
  * an outlet counts if its title LEADS with the brand name, or if its Google
    website is the brand's official domain (from research.py). The domain rule
    is what finds pub-group outlets, which are named per pub ("The Red Lion").

Subcommands:
    python apify_outlets.py plan                           # runs, keys, cost - spends nothing
    python apify_outlets.py run --names "Honest Burgers"   # launch named brands' runs
    python apify_outlets.py run --all                      # launch every planned run not yet done
    python apify_outlets.py process                        # count outlets from saved raw files

Never enable an actor-side filter (searchMatching other than "all",
skipClosedPlaces, categoryFilterWords...): each one bills +$0.001 per place.
Everything is filtered locally instead, where each gate is auditable.
"""
from __future__ import annotations

import argparse
import json
import os
import logging
import math
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

APIFY_SCRIPTS = config.REPO / "talabat" / "Google_Place" / "apify" / "scripts"
sys.path.insert(0, str(APIFY_SCRIPTS))
import run_brand_scraper as rbs  # noqa: E402
from process_brand import _normalize_quotes, clean_maps_url, is_restaurant_category  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger("brand_scraper")        # run_group() logs to this name

COST_PER_PLACE = 0.004      # FREE tier, place-scraped event
KEY_RUN_BUDGET = 4.0        # never plan more than this onto one key (limit is $5)
KEY_MARGIN = 0.25           # left untouched on every key
NOISE_FACTOR = 1.3          # raw places per real outlet; re-measure from the pilot
DEFAULT_HINT = 60           # size assumed when the web gave no UK count
# Kept out of UK work: Venkatesh's cycle resets on Oct 24 (not Sept 29), so
# it is the reserve for USA/Europe; the other two are nearly empty.
EXCLUDED_KEYS = {"Venkatesh Bestha", "Prashant Bombe", "Simran Dash"}
MAX_RUNS_PER_KEY = 5         # FREE-tier concurrent actor runs per account
rbs.MAX_WAIT_MIN = 240      # country-wide runs outlast the UAE's 90-minute ceiling

APIFY_DIR = config.DATA_DIR / "apify"
RAW_DIR = APIFY_DIR / "raw"
LEDGER = APIFY_DIR / "runs.jsonl"
PLAN_JSON = APIFY_DIR / "plan.json"
OUTLETS_JSONL = config.DATA_DIR / "outlets.jsonl"
OUTLET_LIST_DIR = APIFY_DIR / "outlets"
OVERRIDES = config.OVERRIDES

# Split areas for the biggest chains, with approximate population share (used
# only to size each run to fit one key). The ITL1 region names were checked
# against Nominatim, which the actor resolves locations with. "West Midlands"
# and "East Midlands" resolve to the wrong polygon (the metropolitan county,
# and a Derbyshire-sized box), so those two regions are given as counties.
# Derbyshire resolves to the administrative county, which excludes Derby city.
UK_REGIONS = [
    ("London, United Kingdom", 0.131), ("South East England", 0.138),
    ("North West England", 0.111), ("East of England", 0.094),
    ("South West England", 0.085), ("Yorkshire and the Humber", 0.082),
    ("Scotland", 0.081), ("Wales", 0.046), ("North East England", 0.039),
    ("Northern Ireland", 0.028),
    # West Midlands region
    ("West Midlands, England", 0.044), ("Staffordshire, England", 0.017),
    ("Warwickshire, England", 0.009), ("Worcestershire, England", 0.009),
    ("Shropshire, England", 0.0075), ("Herefordshire, England", 0.003),
    # East Midlands region
    ("Derbyshire, England", 0.012), ("Derby, England", 0.004),
    ("Nottinghamshire, England", 0.0175), ("Leicestershire, England", 0.0165),
    ("Rutland, England", 0.0006), ("Lincolnshire, England", 0.016),
    ("Northamptonshire, England", 0.012),
]

TWO_LEVEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "co.nz", "co.za", "com.tr"}


# -- names & domains ----------------------------------------------------------

def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def norm_name(s: str) -> str:
    """Accent-, quote-, punctuation- and '&'-insensitive form for title matching:
    "Côte" == "cote", "McDonald's" == "mcdonalds", "Joe & The Juice" ==
    "joe and the juice", "P.F." == "pf"."""
    s = unicodedata.normalize("NFKD", _normalize_quotes(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "").replace(".", "").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def registrable_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/")[0].split(":")[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return None
    n = 3 if ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES and len(parts) >= 3 else 2
    return ".".join(parts[-n:])


def norm_name_ar(s: str | None) -> str:
    """norm_name, but Arabic letters survive: GCC branches listed only in Arabic
    ('د.كيف العليا') normalise to '' under norm_name and could never match."""
    s = "".join(c if "؀" <= c <= "ۿ" else
                unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
                for c in _normalize_quotes(s or ""))
    s = s.lower().replace("&", " and ").replace("'", "").replace(".", "").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def title_matches(title: str, keywords: list[str], exclude: list[str]) -> bool:
    """Leading-name rule: the brand must START the title (an optional leading
    'The' ignored on both sides). Words after it are branch suffixes; words
    before it mean a different business ('Haji Ali Juice Center' is not
    'Juice Center'). Tried on the ASCII form first, then on the Arabic-keeping
    form - the second only adds matches for Arabic keywords."""
    for norm in (norm_name, norm_name_ar):
        t = norm(title)
        if any(norm(x) and norm(x) in t for x in exclude):
            return False
        t_bare = re.sub(r"^the ", "", t)
        for kw in keywords:
            k = norm(kw)
            k_bare = re.sub(r"^the ", "", k)
            for tt, kk in ((t, k), (t_bare, k_bare)):
                if kk and tt.startswith(kk):
                    rest = tt[len(kk):]
                    if not rest or not rest[0].isalnum():
                        return True
    return False


# -- plan ---------------------------------------------------------------------

def load_research() -> dict[int, dict]:
    path = config.DATA_DIR / "research.jsonl"
    if not path.exists():
        raise SystemExit("data/research.jsonl missing - run research.py first")
    return {r["row_index"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


load_rows = config.load_rows   # [(row_index, name)] in the client's order (csv or xlsx)


def brand_specs() -> list[dict]:
    research = load_research()
    estimates, region = {}, {}
    if config.R.get("countries"):
        from build_output import parse_estimate
        client = config.load_input()
        estimates = {i: parse_estimate(v) for i, v in enumerate(client[config.R["estimate_col"]])}
        rp = config.DATA_DIR / "region_counts.jsonl"
        if rp.exists():
            region = {r["input_name"]: r for r in map(json.loads, rp.read_text(encoding="utf-8").splitlines())}
    overrides = {k: v for k, v in (json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}).items() if not k.startswith("_")}
    specs = []
    for idx, name in load_rows():
        r = research.get(idx, {})
        if r and r.get("input_name") != name:
            r = {}
        o = overrides.get(name, {})
        canonical = r.get("canonical_name") or name
        keywords = o.get("keywords") or sorted({name, canonical}, key=len)
        # planning only: an override corrects a web hint that is plainly a
        # misread (Coco di Mama read as 2,750); it never reaches the output
        hint = o.get("hint") or r.get("uk_outlets_hint") or DEFAULT_HINT
        if config.R.get("countries"):
            est = estimates.get(idx)
            hint = int(sum(est) / 2) if est else hint
            rc = region.get(name, {})
            apify_countries = o.get("countries") or rc.get("countries_searched") or config.R["countries"]
            country_hints = {c: v["count"] for c, v in (rc.get("per_country") or {}).items()}
        specs.append({
            "row_index": idx, "input_name": name, "slug": f"{idx:03d}_{slugify(name)}",
            "search": o.get("search") or canonical,
            "keywords": keywords, "exclude": o.get("exclude", []),
            "match": o.get("match", "title+domain"),
            "domain": registrable_domain(o.get("website") or r.get("website")),
            "hint": hint, "hint_from_web": bool(r.get("uk_outlets_hint")),
            "same_as": o.get("same_as"),
            **({"apify_countries": apify_countries, "country_hints": country_hints,
                "home": research.get(idx, {}).get("hq_country")} if config.R.get("countries") else {}),
            "require_category": o.get("require_category"),
        })
    return specs


def filter_ok(s: dict) -> bool:
    """Can the actor's own title filter ("only_includes") be trusted for this
    brand? Only when its listings are titled with the plain search term:
    Honest Burgers test, 2026-09-25 - 46 places, 46 real, 0 padding. Not for
    names whose spelling on Google varies (apostrophes, '&', accents) or for
    brands matched by website domain (their outlets carry other names)."""
    term = s["search"]
    # straight apostrophes and '&' are how Google titles UK chains (the UAE
    # McDonald's mismatch was a curly apostrophe in the INPUT list, not on
    # Google); accented names stay unfiltered.
    return (s["match"] != "domain" and re.fullmatch(r"[A-Za-z0-9 '&!+#.]+", term) is not None
            and any(norm_name(k).startswith(norm_name(term)) for k in s["keywords"]))


ISO2 = {"United Kingdom": "gb", "United Arab Emirates": "ae", "Saudi Arabia": "sa", "Kuwait": "kw", "Qatar": "qa",
        "Bahrain": "bh", "Oman": "om", "Austria": "at", "Belgium": "be", "Bulgaria": "bg", "Croatia": "hr",
        "Cyprus": "cy", "Czech Republic": "cz", "Denmark": "dk", "Estonia": "ee", "Finland": "fi", "France": "fr",
        "Germany": "de", "Greece": "gr", "Hungary": "hu", "Ireland": "ie", "Italy": "it", "Latvia": "lv",
        "Lithuania": "lt", "Luxembourg": "lu", "Malta": "mt", "Netherlands": "nl", "Poland": "pl", "Portugal": "pt",
        "Romania": "ro", "Slovakia": "sk", "Slovenia": "si", "Spain": "es", "Sweden": "se"}
REGION_CODES = {ISO2[c].upper() for c in (config.R.get("countries") or ["United Kingdom"])}


def plan_runs(specs: list[dict]) -> list[dict]:
    """Cost model, measured 2026-09-25: without the title filter the actor pads
    every run with unrelated nearby places up to its cap (Camden Food Co: 57 of
    60 unrelated), so a run costs its full cap. With the filter only matching
    places are billed, at $0.005 each, so the cap can stay generous."""
    runs = []
    for s in specs:
        if s.get("same_as"):
            continue   # counted by the row it duplicates
        filtered = filter_ok(s)
        price = COST_PER_PLACE + (0.001 if filtered else 0.0)
        if config.R.get("countries"):
            # multi-country region: one run per country the brand trades in
            ctries = s.get("apify_countries") or []
            for i, ctry in enumerate(ctries):
                share = s.get("country_hints", {}).get(ctry) or max(20, s["hint"] // max(1, len(ctries)))
                if filtered:
                    # only matches are billed: any country may hold most of the brand
                    cap = int(min(KEY_RUN_BUDGET / price, max(60, math.ceil(s["hint"] * 1.2))))
                    est = share * 1.05 * price
                else:
                    # unfiltered: the cap is the bill - home market large, others small
                    big = ctry == s.get("home") or len(ctries) == 1
                    cap = int(min(KEY_RUN_BUDGET / price, max(40, math.ceil(s["hint"] * (1.3 if big else 0.25)))))
                    est = cap * price
                runs.append({"label": f"{s['slug']}__{slugify(ctry)}", "slug": s["slug"], "search": s["search"],
                             "location": ctry, "country_code": ISO2[ctry], "cap": cap,
                             "matching": "only_includes" if filtered else "all", "est_cost": round(est, 2)})
            continue
        est_places = max(s["hint"], 10) * (1.05 if filtered else NOISE_FACTOR)
        if est_places * price <= KEY_RUN_BUDGET:
            # filtered: 2x hint costs nothing unless real outlets fill it.
            # unfiltered: the cap IS the bill, so 1.5x hint; a run whose real
            # outlets reach it is flagged "capped" by `process` and re-run.
            factor, floor = (2.0, 60) if filtered else (1.5, 30)
            cap = int(min(KEY_RUN_BUDGET / price, max(floor, math.ceil(s["hint"] * factor))))
            est = cap * price if not filtered else est_places * price
            runs.append({"label": f"{s['slug']}__uk", "slug": s["slug"], "search": s["search"],
                         "location": "United Kingdom", "cap": cap, "matching": "only_includes" if filtered else "all",
                         "est_cost": round(est, 2)})
        else:
            for region, share in UK_REGIONS:
                est = est_places * share
                cap = int(min(KEY_RUN_BUDGET / price, max(80, math.ceil(est * 2))))
                runs.append({"label": f"{s['slug']}__{slugify(region)}", "slug": s["slug"],
                             "search": s["search"], "location": region, "cap": cap,
                             "matching": "only_includes" if filtered else "all",
                             "est_cost": round(est * price, 2)})
    return runs


def load_balances() -> dict[str, float]:
    p = config.KEY_BALANCES
    if not p.exists():
        raise SystemExit("data/key_balances.json missing - run check_keys.py --apify first")
    rows = json.loads(p.read_text(encoding="utf-8")).get("apify", [])
    keys = {r["name"]: r["remaining"] for r in rows if r.get("status") == "ok" and r["name"] not in EXCLUDED_KEYS}
    # APIFY_KEY_SLICE="0:9" - a disjoint slice per region when two run at once
    sl = os.getenv("APIFY_KEY_SLICE")
    if sl:
        a, b = (int(x) if x else None for x in sl.split(":"))
        keys = dict(sorted(keys.items())[a:b])
    return keys


def assign_keys(runs: list[dict], balances: dict[str, float]) -> None:
    """Greedy: biggest run first, onto the key with the most headroom. A run
    never goes onto a key that could not pay its worst case (cap x price) -
    an over-budget run aborts partway and its spend is lost."""
    left = {k: v - KEY_MARGIN for k, v in balances.items()}
    # FREE accounts start at most 5 actor runs at once ("exceed your limit of
    # 5 concurrent Actor runs" - 9 of 82 GCC launches refused, 2026-09-28)
    slots = {k: MAX_RUNS_PER_KEY for k in balances}
    for run in sorted(runs, key=lambda r: -r["cap"]):
        # A title-filtered run bills matching places only, so its cap is not
        # its cost: reserve 1.3x the expected cost (GCC runs carry a whole-brand
        # cap per country, which as a "worst case" left 66 of 84 runs keyless);
        # the live-charge watchdog is the hard stop.
        worst = (run["cap"] * COST_PER_PLACE if run.get("matching", "all") == "all"
                 else max(0.2, 1.3 * run.get("est_cost", 0)))
        free = [k for k in left if slots[k] > 0]
        key = max(free, key=left.get) if free else None
        if key is None or left[key] < worst:
            run["key"] = None
            continue
        run["key"] = key
        left[key] -= worst
        slots[key] -= 1


def ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def cmd_plan(args) -> list[dict]:
    specs = brand_specs()
    if getattr(args, "from_plan", False):
        # only the brands count_plan.py routed to Apify (plus calibration pairs)
        cp = json.loads((config.DATA_DIR / "count_plan.json").read_text(encoding="utf-8"))
        specs = [s for s in specs if cp.get(s["input_name"], {}).get("apify")]
    if args.names:
        wanted = {n.lower() for n in args.names}
        specs = [s for s in specs if s["input_name"].lower() in wanted]
    runs = plan_runs(specs)
    done = {r["label"] for r in ledger() if r.get("status") == "SUCCEEDED"}
    todo = [r for r in runs if r["label"] not in done]
    assign_keys(todo, load_balances())
    APIFY_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_JSON.write_text(json.dumps({"specs": specs, "runs": runs}, indent=1, ensure_ascii=False), encoding="utf-8")

    unassigned = [r for r in todo if not r.get("key")]
    split = sorted({r["slug"] for r in runs if not r["label"].endswith("__uk")})
    print(f"brands {len(specs)} | runs {len(runs)} ({len(done & {r['label'] for r in runs})} already done) | "
          f"regionally split brands {len(split)}")
    print(f"estimated cost of remaining runs: ${sum(r['est_cost'] for r in todo):.2f} "
          f"(worst case at caps: ${sum(r['cap'] for r in todo) * COST_PER_PLACE:.2f})")
    print(f"runs with no key that can cover their worst case: {len(unassigned)}")
    if args.verbose:
        for r in todo:
            print(f"  {r['label']:<55} cap {r['cap']:>5}  est ${r['est_cost']:>5.2f}  key {r.get('key')}")
    return todo


# -- run ----------------------------------------------------------------------

def _run_one(run: dict, token: str) -> dict:
    actor_config = {
        "locationQuery": run["location"], "countryCode": run.get("country_code", "gb"), "language": "en",
        # "all" is the actor default (no fee). "only_includes" bills +$0.001/place
        # but drops non-matching places before billing - see cmd_run --matching.
        "searchMatching": run.get("matching", "all"),
        "maxCrawledPlacesPerSearch": run["cap"],
    }
    started = datetime.now().isoformat(timespec="seconds")
    _, items, run_id, cost = rbs.run_group(run["label"][-30:], [run["search"]], actor_config, token)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{run['label']}__{(run_id or 'none')[:8]}.json"
    raw_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    # run_group reports cost 0.0 for a run that ended ABORTED/FAILED/TIMED-OUT
    # but still returns its salvaged items; keep those, but mark them partial.
    partial = bool(items) and not cost
    return {**run, "run_id": run_id, "items": len(items), "cost_reported": cost,
            "status": "SUCCEEDED" if items or cost else "EMPTY_OR_FAILED",
            **({"partial": "run did not complete (aborted/failed); data salvaged"} if partial else {}),
            "raw_file": raw_path.name, "started": started,
            "finished": datetime.now().isoformat(timespec="seconds")}


def run_price(run: dict) -> float:
    return COST_PER_PLACE + (0.001 if run.get("matching", "all") != "all" else 0.0)


SHARED_LEDGERS = [config.UK_DIR / d / "data" / "apify" / "runs.jsonl"
                  for d in os.getenv("APIFY_SHARED_REGIONS", "").split(",") if d]


def spent_so_far() -> float:
    """Dollars already charged by runs in the ledger (places x price). With
    APIFY_SHARED_REGIONS=EUROPE,GCC the ledgers of all those regions count,
    so one hard limit covers regions launched in parallel."""
    if SHARED_LEDGERS:
        rows = [json.loads(l) for f in SHARED_LEDGERS if f.exists()
                for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        rows = ledger()
    return sum(r.get("items", 0) * run_price(r) for r in rows)


def cmd_run(args) -> None:
    if not (args.names or args.all or args.from_plan):
        raise SystemExit("pass --names ..., --from-plan or --all")
    if args.max_spend is None:
        raise SystemExit("--max-spend is required: the hard dollar limit for UK Apify spend")
    todo = cmd_plan(args)
    todo = [r for r in todo if r.get("key")]
    # Runs another launcher started that are still in flight on Apify (they
    # reach the ledger via `recover`); relaunching them would pay twice.
    in_flight_elsewhere = started_not_ledgered(args.skip_started or [])
    todo = [r for r in todo if r["label"] not in in_flight_elsewhere]
    if not todo:
        print("nothing to run")
        return
    for r in todo:
        if args.matching:
            r["matching"] = args.matching
    tokens = dict(config.load_keys("apify"))
    already = spent_so_far()
    if args.all_at_once:
        return run_all_at_once(args, todo, tokens, already, in_flight_elsewhere)
    print(f"{len(todo)} run(s) on {len({r['key'] for r in todo})} key(s) | spent so far ${already:.2f} "
          f"| hard limit ${args.max_spend:.2f}")
    APIFY_DIR.mkdir(parents=True, exist_ok=True)

    # Hard limit: a run is launched only if (already spent) + (worst case of
    # every run still in flight) + (this run's worst case) stays within the
    # limit. Worst case = its cap x price, the most the actor can bill it.
    spent, inflight, skipped = already, {}, []
    queue = sorted(todo, key=lambda r: r["cap"])       # small first: more brands done if we stop

    def settle(done_futs):
        nonlocal spent
        for f in done_futs:
            r = inflight.pop(f)
            try:
                rec = f.result()
            except Exception as exc:  # one failed run must not stop the others
                rec = {**r, "status": f"ERROR: {str(exc)[:200]}"}
            spent += rec.get("items", 0) * run_price(rec)
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.info(f"[ledger] {rec['label']}: {rec['status']} items={rec.get('items')} "
                     f"key={r['key']} | spent ${spent:.2f}")

    from concurrent.futures import FIRST_COMPLETED, wait
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in queue:
            worst = r["cap"] * run_price(r)
            while inflight and (len(inflight) >= args.workers or
                                spent + sum(x["cap"] * run_price(x) for x in inflight.values()) + worst > args.max_spend):
                settle(wait(inflight, return_when=FIRST_COMPLETED).done)
            if spent + worst > args.max_spend:
                skipped.append(r["label"])
                continue
            inflight[ex.submit(_run_one, r, tokens[r["key"]])] = r
        while inflight:
            settle(wait(inflight, return_when=FIRST_COMPLETED).done)
    print(f"done | spent ${spent:.2f} of ${args.max_spend:.2f}"
          + (f" | NOT RUN (limit): {len(skipped)} {skipped[:10]}" if skipped else ""))


def started_not_ledgered(logs: list[str]) -> set[str]:
    plan = json.loads(PLAN_JSON.read_text(encoding="utf-8"))
    by_suffix = {r["label"][-30:]: r["label"] for r in plan["runs"]}
    done = {r["label"] for r in ledger()}
    out = set()
    for lg in logs:
        for s, _ in re.findall(r"\[(.{1,30}?)\s*\] Run ID: (\w+)", Path(lg).read_text(encoding="utf-8")):
            label = by_suffix.get(s.strip())
            if label and label not in done:
                out.add(label)
    return out


EVENT_PRICE = {"place-scraped": COST_PER_PLACE, "filter-applied": 0.001}


def _field(obj, attr: str, key: str):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, attr, None)


def live_charges(tokens: dict[str, str], keys: set[str]) -> tuple[float, list[tuple[str, str]]]:
    """Dollars billed so far by every RUNNING actor run on these keys, read
    from Apify itself - the only number that cannot drift from the bill."""
    from apify_client import ApifyClient
    total, running = 0.0, []
    for k in keys:
        client = ApifyClient(tokens[k])
        for item in client.runs().list(status="RUNNING").items:
            rid = _field(item, "id", "id")
            info = client.run(rid).get()
            counts = _field(info, "charged_event_counts", "chargedEventCounts") or {}
            total += sum(EVENT_PRICE.get(e, 0.0) * n for e, n in counts.items())
            running.append((k, rid))
    return total, running


def run_all_at_once(args, todo, tokens, already, in_flight_elsewhere) -> None:
    """Every selected run starts immediately: a run takes ~13 min whatever its
    size (the actor sweeps the whole UK map), so batches of 16 made 64 runs
    take an hour. Budget is enforced twice:
      * selection - runs are chosen smallest-first by EXPECTED cost to fit
        what is left of the limit (worst-case caps would throttle everything);
      * watchdog  - every 30 s the live charges on Apify are read; if spent +
        live ever reaches the limit, every running run is aborted.
    """
    import threading
    from apify_client import ApifyClient
    plan = {r["label"]: r for r in json.loads(PLAN_JSON.read_text(encoding="utf-8"))["runs"]}
    expected_elsewhere = sum(plan[l]["est_cost"] for l in in_flight_elsewhere if l in plan)
    room = args.max_spend - already - expected_elsewhere
    chosen, skipped, acc = [], [], 0.0
    for r in sorted(todo, key=lambda r: r["est_cost"]):
        if acc + r["est_cost"] <= room:
            chosen.append(r)
            acc += r["est_cost"]
        else:
            skipped.append(r["label"])
    print(f"spent ${already:.2f} + in flight elsewhere ~${expected_elsewhere:.2f} | launching {len(chosen)} runs "
          f"at once (expected ${acc:.2f}) | hard limit ${args.max_spend:.2f}"
          + (f" | NOT LAUNCHED (would exceed the limit): {skipped}" if skipped else ""))

    keys = set(load_balances())
    stop = threading.Event()

    def watchdog():
        while not stop.wait(30):
            try:
                live, running = live_charges(tokens, keys)
            except Exception as exc:
                log.warning(f"[watchdog] could not read live charges: {exc}")
                continue
            total = spent_so_far() + live
            log.info(f"[watchdog] ledger ${spent_so_far():.2f} + live ${live:.2f} = ${total:.2f} "
                     f"| {len(running)} running | limit ${args.max_spend:.2f}")
            if total >= args.max_spend:
                log.error(f"[watchdog] LIMIT REACHED - aborting {len(running)} running run(s)")
                for k, rid in running:
                    try:
                        ApifyClient(tokens[k]).run(rid).abort()
                    except Exception as exc:
                        log.warning(f"[watchdog] abort {rid} failed: {exc}")
                stop.set()

    threading.Thread(target=watchdog, daemon=True).start()
    with ThreadPoolExecutor(max_workers=max(1, len(chosen))) as ex:
        futs = {ex.submit(_run_one, r, tokens[r["key"]]): r for r in chosen}
        for f in as_completed(futs):
            r = futs[f]
            try:
                rec = f.result()
            except Exception as exc:
                rec = {**r, "status": f"ERROR: {str(exc)[:200]}"}
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.info(f"[ledger] {rec['label']}: {rec['status']} items={rec.get('items')} key={r['key']} "
                     f"| spent ${spent_so_far():.2f}")
    stop.set()
    print(f"done | spent ${spent_so_far():.2f} of ${args.max_spend:.2f} (excluding runs still in flight elsewhere)"
          + (f" | NOT LAUNCHED: {skipped}" if skipped else ""))


# -- rerun ----------------------------------------------------------------------

def cmd_rerun(args) -> None:
    """Targeted re-runs from data/apify/reruns.json, each with its own search
    term, filter mode and cap - for brands whose first count was wrong for a
    known reason (e.g. curly vs straight apostrophes defeating the actor's
    title filter). A re-run gets its own label; `process` pools it with the
    brand's earlier runs and de-duplicates, so nothing already paid for is
    thrown away. Runs start all at once under the same live-charge watchdog."""
    specs = {s["input_name"]: s for s in brand_specs()}
    todo = []
    done = {r["label"] for r in ledger() if r.get("status") == "SUCCEEDED"}
    for e in json.loads((APIFY_DIR / "reruns.json").read_text(encoding="utf-8")):
        if args.names and e["input_name"] not in args.names:
            continue
        if e.get("key") in EXCLUDED_KEYS:
            # an explicit key must never bypass the reserve (2026-09-28: a
            # Steinecke re-run was launched on a reserved key this way)
            raise SystemExit(f"reruns.json: {e['input_name']} names excluded key {e['key']!r}")
        s = specs[e["input_name"]]
        loc = e.get("location", "United Kingdom")
        label = (f"{s['slug']}__uk_{e.get('tag', 'r2')}" if loc == "United Kingdom"
                 else f"{s['slug']}__{slugify(loc)}_{e.get('tag', 'r2')}")
        if label in done:
            continue
        price = COST_PER_PLACE + (0.001 if e["matching"] != "all" else 0.0)
        est = e["cap"] * price if e["matching"] == "all" else e.get("expect", e["cap"] / 2) * price
        todo.append({"label": label, "slug": s["slug"], "search": e["search"], "location": loc,
                     "country_code": ISO2.get(loc, "gb"), "cap": e["cap"], "matching": e["matching"],
                     "est_cost": round(est, 2), **({"key": e["key"]} if e.get("key") else {})})
    if not todo:
        print("nothing to re-run")
        return
    fixed = [r for r in todo if r.get("key")]
    assign_keys([r for r in todo if not r.get("key")], load_balances())
    todo = [r for r in todo if r.get("key")]
    tokens = dict(config.load_keys("apify"))
    run_all_at_once(args, todo, tokens, spent_so_far(), set())


# -- recover ------------------------------------------------------------------

def cmd_recover(args) -> None:
    """Collect runs that were started but never reached the ledger (the
    launcher was stopped while they were in flight). They keep running - and
    billing - on Apify, so their data must be downloaded, not re-bought.
    Run ids come from the launcher's log; the owning key is found by asking
    each key's account for the run."""
    import time
    from apify_client import ApifyClient
    plan = json.loads(PLAN_JSON.read_text(encoding="utf-8"))
    by_suffix = {r["label"][-30:]: r for r in plan["runs"]}
    done = {r["label"] for r in ledger()}
    started = re.findall(r"\[(.{1,30}?)\s*\] Run ID: (\w+)", Path(args.log).read_text(encoding="utf-8"))
    pending = [(by_suffix[s.strip()], rid) for s, rid in started
               if s.strip() in by_suffix and by_suffix[s.strip()]["label"] not in done]
    print(f"{len(started)} started in log | {len(pending)} not yet in the ledger")
    tokens = config.load_keys("apify")
    for run, rid in pending:
        owner = None
        for name, tok in tokens:
            try:
                info = ApifyClient(tok).run(rid).get()
            except Exception:
                continue
            if info:
                owner = (name, tok)
                break
        if not owner:
            print(f"  {run['label']}: run {rid} not found on any key")
            continue
        client = ApifyClient(owner[1])
        while (info := client.run(rid).get()).status not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            time.sleep(20)
        items = rbs.download_dataset_items(client, rbs.get_dataset_id(info), run["label"][-12:], log)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = RAW_DIR / f"{run['label']}__{rid[:8]}.json"
        raw_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        rec = {**run, "key": owner[0], "run_id": rid, "items": len(items),
               "status": info.status if info.status != "SUCCEEDED" or items else "EMPTY_OR_FAILED",
               "raw_file": raw_path.name, "recovered": True,
               "finished": datetime.now().isoformat(timespec="seconds")}
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  {run['label']}: {info.status} items={len(items)} key={owner[0]}")
    print(f"spent so far (ledger): ${spent_so_far():.2f}")


# -- process ------------------------------------------------------------------

def cmd_process(args) -> None:
    specs = {s["slug"]: s for s in brand_specs()}
    runs_by_slug: dict[str, list[dict]] = {}
    for rec in ledger():
        if rec.get("status") == "SUCCEEDED":
            runs_by_slug.setdefault(rec["slug"], []).append(rec)   # later re-runs append; dedup handles overlap

    OUTLET_LIST_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for slug, runs in sorted(runs_by_slug.items()):
        s = specs.get(slug)
        if not s:
            continue
        items = []
        for rec in runs:
            items += json.loads((RAW_DIR / rec["raw_file"]).read_text(encoding="utf-8"))
        stats = {"raw": len(items), "not_uk": 0, "perm_closed": 0, "temp_closed": 0,
                 "non_restaurant": 0, "noise": 0, "by_title": 0, "by_domain_only": 0,
                 "title_other_domain": 0}
        rows = []
        for it in items:
            title = (it.get("title") or "").strip()
            if (it.get("countryCode") or "").upper() not in REGION_CODES:
                stats["not_uk"] += 1
                continue
            site_dom = registrable_domain(it.get("website"))
            by_title = s["match"] != "domain" and title_matches(title, s["keywords"], s["exclude"])
            by_domain = (s["match"] != "title" and s["domain"] and site_dom == s["domain"]
                         and not any(norm_name(x) in norm_name(title) for x in s["exclude"]))
            if not (by_title or by_domain):
                stats["noise"] += 1
                continue
            if it.get("permanentlyClosed"):
                stats["perm_closed"] += 1
                continue
            cat = it.get("categoryName") or ""
            # MAIN category only: coffee chains list secondary categories such
            # as "Coffee wholesaler", and checking all of them discarded 105 of
            # Black Sheep Coffee's 129 cafes as "wholesalers".
            if not is_restaurant_category(cat, [cat]):
                stats["non_restaurant"] += 1
                continue
            need = s.get("require_category")
            if need and not any(w in " ".join([cat] + (it.get("categories") or [])).lower() for w in need):
                stats["non_restaurant"] += 1   # e.g. "Revolution Laundry" is not a Revolution bar
                continue
            rows.append({
                "place_id": it.get("placeId") or "", "title": title,
                "address": (it.get("address") or "").strip(), "city": it.get("city") or "",
                "postal_code": it.get("postalCode") or "", "category": cat,
                "website": it.get("website") or "", "maps_url": clean_maps_url(it.get("url") or ""),
                "lat": (it.get("location") or {}).get("lat"), "lng": (it.get("location") or {}).get("lng"),
                "temporarily_closed": bool(it.get("temporarilyClosed")),
                "matched_by": "title" if by_title else "domain",
                "title_other_domain": bool(by_title and s["domain"] and site_dom and site_dom != s["domain"]),
            })
        df = pd.DataFrame(rows)
        if len(df):
            df = df[df["place_id"] != ""].drop_duplicates("place_id")
            has_addr = df["address"] != ""
            df = pd.concat([df[has_addr].drop_duplicates("address"), df[~has_addr]], ignore_index=True)
            stats["temp_closed"] = int(df["temporarily_closed"].sum())
            stats["by_title"] = int((df["matched_by"] == "title").sum())
            stats["by_domain_only"] = int((df["matched_by"] == "domain").sum())
            stats["title_other_domain"] = int(df["title_other_domain"].sum())
            df.to_csv(OUTLET_LIST_DIR / f"{slug}.csv", index=False, encoding="utf-8-sig")
        open_count = int((~df["temporarily_closed"]).sum()) if len(df) else 0
        # "capped" = the REAL outlets filled the cap, so some may be missing.
        # A cap filled by padding (unrelated places) is not a truncation.
        real = stats["by_title"] + stats["by_domain_only"] + stats["perm_closed"] + stats["temp_closed"]
        capped = [r["label"] for r in runs if r.get("items", 0) >= r["cap"] and real >= 0.9 * r["cap"]]
        cost = sum(r.get("items", 0) * run_price(r) for r in runs)
        out.append({"row_index": s["row_index"], "input_name": s["input_name"], "slug": slug,
                    "uk_outlets": open_count, **stats, "capped_runs": capped,
                    "runs": len(runs), "est_cost_usd": round(cost, 4),
                    "noise_ratio": round(stats["raw"] / max(open_count, 1), 2)})
        print(f"{s['input_name']:<28} outlets {open_count:>5} | raw {stats['raw']:>5} noise {stats['noise']:>4} "
              f"non-outlet {stats['non_restaurant']:>3} "
              f"title {stats['by_title']:>4} domain-only {stats['by_domain_only']:>4} "
              f"title-other-domain {stats['title_other_domain']:>3} temp-closed {stats['temp_closed']:>3} "
              f"perm-closed {stats['perm_closed']:>3} | ${cost:.3f}" + (f" | CAPPED {capped}" if capped else ""))

    # rows that are the same brand as another row share its count
    by_name = {r["input_name"]: r for r in out}
    for spec in specs.values():
        src = by_name.get(spec.get("same_as") or "")
        if src:
            out.append({**src, "row_index": spec["row_index"], "input_name": spec["input_name"],
                        "slug": spec["slug"], "shared_from": src["input_name"]})

    existing = {}
    if OUTLETS_JSONL.exists():
        existing = {r["slug"]: r for r in map(json.loads, OUTLETS_JSONL.read_text(encoding="utf-8").splitlines())}
    existing.update({r["slug"]: r for r in out})
    OUTLETS_JSONL.write_text("\n".join(json.dumps(existing[k], ensure_ascii=False) for k in sorted(existing)) + "\n",
                             encoding="utf-8")
    print(f"\nwritten -> {OUTLETS_JSONL} ({len(existing)} brands)")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--names", nargs="*"); p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--from-plan", action="store_true", help="only brands count_plan.py routed to Apify")
    r = sub.add_parser("run"); r.add_argument("--names", nargs="*"); r.add_argument("--all", action="store_true")
    r.add_argument("--from-plan", action="store_true", help="only brands count_plan.py routed to Apify")
    r.add_argument("--max-spend", type=float, help="hard dollar limit across all runs (required)")
    r.add_argument("--all-at-once", action="store_true",
                   help="launch every affordable run immediately, with a live-charge watchdog")
    r.add_argument("--skip-started", nargs="*", help="launcher logs whose in-flight runs must not be relaunched")
    r.add_argument("--matching", default=None, choices=["all", "only_includes"],
                   help="override the per-brand choice made by plan_runs")
    r.add_argument("--workers", type=int, default=8); r.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("process")
    rc = sub.add_parser("recover"); rc.add_argument("--log", required=True)
    rr = sub.add_parser("rerun"); rr.add_argument("--names", nargs="*")
    rr.add_argument("--max-spend", type=float, required=True)
    args = ap.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "process": cmd_process, "recover": cmd_recover, "rerun": cmd_rerun}[args.cmd](args)


if __name__ == "__main__":
    main()
