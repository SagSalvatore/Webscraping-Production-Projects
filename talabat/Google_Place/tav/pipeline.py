"""
pipeline.py — async orchestration: Tavily search -> OpenAI extraction ->
merge back onto Talabat branch rows, for the "Not Found" recovery phase.

Results are appended to a JSONL file as they complete (crash-safe / resumable)
and a name-level checkpoint set is updated after each completed name.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pandas as pd
from loguru import logger

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402
from tavily_search import TavilySearcher  # noqa: E402
from openai_extract import OpenAIExtractor, ExtractedBusiness  # noqa: E402
from validation import (  # noqa: E402
    name_supported_by_results,
    flag_shared_contact_duplicates,
    is_placeholder_phone,
    maps_url_supported_by_results,
    build_maps_search_url,
    has_independent_confirmation,
)
from platform_scrape import find_platform_urls  # noqa: E402


def load_unique_notfound_names(csv_path: Path = config.NOT_FOUND_CSV) -> pd.DataFrame:
    """
    Dedupe NotFound_Branches.csv down to one row per unique cleaned name,
    carrying the list of Branch_ID/Restaurant_ID pairs that share it (so a
    single recovered result can be fanned back out to every branch).
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    grouped = (
        df.groupby("Talabat_Restaurant_Name_Clean")
        .apply(
            lambda g: pd.Series(
                {
                    "Talabat_Restaurant_Name_Raw": g["Talabat_Restaurant_Name_Raw"].iloc[0],
                    "Area_Name": g["Area_Name"].mode().iloc[0] if not g["Area_Name"].dropna().empty else None,
                    "Branch_IDs": list(g["Branch_ID"]),
                    "Restaurant_IDs": list(g["Restaurant_ID"]),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    return grouped


def load_checkpoint(checkpoint_file: Path) -> set[str]:
    if checkpoint_file.exists():
        data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        return set(data.get("done_names", []))
    return set()


def load_total_cost(checkpoint_file: Path) -> float:
    """Cumulative OpenAI spend across ALL prior chunks/resumed runs -- each
    run_batch() call gets its own fresh OpenAIExtractor (cost resets to 0),
    so the cost ceiling has to be tracked here, in the persisted checkpoint,
    not on the extractor object itself."""
    if checkpoint_file.exists():
        data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        return float(data.get("total_cost_usd", 0.0))
    return 0.0


def save_checkpoint(checkpoint_file: Path, done_names: set[str], total_cost_usd: float | None = None) -> None:
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    prior_cost = load_total_cost(checkpoint_file) if total_cost_usd is None else 0.0
    payload = {
        "done_names": sorted(done_names),
        "total_cost_usd": total_cost_usd if total_cost_usd is not None else prior_cost,
    }
    checkpoint_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


async def process_one(
    row: dict,
    searcher: TavilySearcher,
    extractor: OpenAIExtractor,
    sem_tavily: asyncio.Semaphore,
    sem_openai: asyncio.Semaphore,
    cost_ceiling: float | None = None,
) -> dict:
    name = row["Talabat_Restaurant_Name_Clean"]
    area = row.get("Area_Name")

    # Hard safety stop: checked BEFORE spending anything further on this
    # name. Not checkpointed as done, so a resumed run picks it back up
    # once the user is ready to continue (e.g. after topping up budget).
    if cost_ceiling is not None and extractor.estimated_cost_usd >= cost_ceiling:
        return {
            "Talabat_Restaurant_Name_Clean": name,
            "Talabat_Restaurant_Name_Raw": row.get("Talabat_Restaurant_Name_Raw"),
            "Area_Name": area,
            "Branch_IDs": row.get("Branch_IDs"),
            "Restaurant_IDs": row.get("Restaurant_IDs"),
            "Found": False,
            "Address": None,
            "Phone": None,
            "Website": None,
            "Has_Google_Maps_Listing": False,
            "Google_Maps_URL": None,
            "Rating": None,
            "Review_Count": None,
            "Latitude": None,
            "Longitude": None,
            "Confidence": 0.0,
            "Notes": "Skipped: cost ceiling reached, not checkpointed -- will retry on next run.",
            "Source": "skipped_cost_ceiling",
            "Platform_URLs_Found": [],
            "Search_Query": None,
            "_skipped_cost_ceiling": True,
        }

    async with sem_tavily:
        try:
            search_payload = await searcher.search(name, area)
        except Exception as exc:
            logger.error(f"[pipeline] search failed permanently for '{name}': {exc}")
            search_payload = {"query": None, "answer": None, "results": []}

    async with sem_openai:
        try:
            extracted: ExtractedBusiness = await extractor.extract(name, area, search_payload)
        except Exception as exc:
            logger.error(f"[pipeline] extraction failed permanently for '{name}': {exc}")
            extracted = ExtractedBusiness(found=False, confidence=0.0, notes=f"extraction error: {exc}")

    # Guardrail: even if the model said found=true, require that the
    # business's own name tokens actually appear in the raw results --
    # not just in Tavily's synthesized (sometimes hallucinated) answer.
    if extracted.found and extracted.address != "Chain" and not name_supported_by_results(name, search_payload):
        logger.warning(f"[pipeline] downgrading '{name}': result content doesn't mention the business name")
        extracted = ExtractedBusiness(
            found=False,
            confidence=0.0,
            notes="Downgraded: no search result content actually mentions the business name "
                  "(likely a generic area/landmark false match).",
        )

    # Guardrail: a talabat.com-only "match" isn't a real recovery -- the
    # business is on Talabat by definition (that's our own source data), and
    # Talabat's own pages don't reliably give a real street address, phone,
    # or rating (its delivery-zone label and internal order-count aren't
    # those things, even though the model has reported them as if they were).
    if extracted.found and not has_independent_confirmation(name, search_payload):
        logger.warning(f"[pipeline] downgrading '{name}': only talabat.com results found, no independent confirmation")
        extracted = ExtractedBusiness(
            found=False,
            confidence=0.0,
            notes="Downgraded: only talabat.com results found (our own source data, not independent "
                  "confirmation) -- no real address/phone/rating available.",
        )

    phone = extracted.phone
    notes = extracted.notes
    if is_placeholder_phone(phone):
        logger.warning(f"[pipeline] dropping placeholder-looking phone for '{name}': {phone!r}")
        notes = f"{notes or ''} [phone dropped: looked like a placeholder/template number, not a real contact]".strip()
        phone = None

    # Guardrail: the model has fabricated maps.google.com/place/Name/@lat,lng
    # URLs with invented coordinates (caught two different businesses given
    # the exact same made-up latitude). Only trust a claimed Maps URL if it
    # was literally present in the raw Tavily results; otherwise fall back
    # to Google's own documented search-deep-link format, which needs no
    # coordinates/Place ID and therefore can't be hallucinated.
    maps_url = extracted.google_maps_url
    if maps_url and not maps_url_supported_by_results(maps_url, search_payload):
        logger.warning(f"[pipeline] dropping fabricated Google Maps URL for '{name}': {maps_url!r}")
        notes = f"{notes or ''} [Maps URL dropped: not present in actual search results, looked fabricated]".strip()
        maps_url = None
    if not maps_url and extracted.found and extracted.address:
        maps_url = build_maps_search_url(name, extracted.address)

    # Priority hierarchy: the official website / general web search (above)
    # is the primary source. Zomato/Deliveroo/noon are only a FALLBACK --
    # only collect their URLs when the primary path didn't give us a
    # usable, specific address. Collecting them unconditionally was wrong:
    # a name like "My Tai Wok Salads Smoothies" already resolves cleanly to
    # its own site (mytai.ae) with full details, and grabbing a same-domain
    # Zomato URL on top (worse, sometimes for an unrelated business that
    # happened to rank first in results) added noise, not signal.
    needs_fallback = (not extracted.found) or (extracted.address == "Chain")
    platform_urls = find_platform_urls(search_payload) if needs_fallback else []

    return {
        "Talabat_Restaurant_Name_Clean": name,
        "Talabat_Restaurant_Name_Raw": row.get("Talabat_Restaurant_Name_Raw"),
        "Area_Name": area,
        "Branch_IDs": row.get("Branch_IDs"),
        "Restaurant_IDs": row.get("Restaurant_IDs"),
        "Found": extracted.found,
        "Address": extracted.address if extracted.found else None,
        "Phone": phone,
        "Website": extracted.website,
        "Has_Google_Maps_Listing": extracted.has_google_maps_listing,
        "Google_Maps_URL": maps_url,
        "Rating": extracted.rating,
        "Review_Count": extracted.review_count,
        "Latitude": None,
        "Longitude": None,
        "Confidence": extracted.confidence,
        "Notes": notes,
        "Source": "llm_web_search",
        "Platform_URLs_Found": [p["url"] for p in platform_urls],
        "Search_Query": search_payload.get("query"),
        "_skipped_cost_ceiling": False,
    }


async def run_batch(
    names_df: pd.DataFrame,
    results_jsonl: Path,
    checkpoint_file: Path | None = None,
    progress_every: int = 25,
    cost_ceiling: float | None = None,
) -> list[dict]:
    results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    done_names = load_checkpoint(checkpoint_file) if checkpoint_file else set()
    prior_cost = load_total_cost(checkpoint_file) if checkpoint_file else 0.0

    # cost_ceiling is a TOTAL across the whole (possibly multi-chunk,
    # possibly resumed) production run. Each call here gets a fresh
    # OpenAIExtractor whose .estimated_cost_usd starts at 0, so translate
    # the absolute ceiling into "how much is left" for THIS chunk.
    remaining_budget = None
    if cost_ceiling is not None:
        remaining_budget = cost_ceiling - prior_cost
        if remaining_budget <= 0:
            logger.warning(
                f"[pipeline] cost ceiling (${cost_ceiling:.2f}) already reached "
                f"(${prior_cost:.4f} spent in prior chunks) -- skipping this chunk entirely."
            )

    pending = [
        r for r in names_df.to_dict("records")
        if r["Talabat_Restaurant_Name_Clean"] not in done_names
    ]
    logger.info(
        f"[pipeline] {len(pending)} names to process "
        f"({len(names_df) - len(pending)} already done per checkpoint)"
    )

    rotator_searcher = TavilySearcher()
    extractor = OpenAIExtractor()
    sem_tavily = asyncio.Semaphore(config.TAVILY_MAX_CONCURRENCY)
    sem_openai = asyncio.Semaphore(config.OPENAI_MAX_CONCURRENCY)

    all_results: list[dict] = []
    start = time.monotonic()
    completed = 0
    found_count = 0

    cost_ceiling_hit = False

    async def _worker(row: dict) -> None:
        nonlocal completed, found_count, cost_ceiling_hit
        result = await process_one(row, rotator_searcher, extractor, sem_tavily, sem_openai, remaining_budget)
        all_results.append(result)
        completed += 1
        if result["Found"]:
            found_count += 1

        with open(results_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

        if result.get("_skipped_cost_ceiling"):
            if not cost_ceiling_hit:
                cost_ceiling_hit = True
                logger.warning(
                    f"[pipeline] COST CEILING (${cost_ceiling:.2f} total) reached at "
                    f"${prior_cost + extractor.estimated_cost_usd:.4f} spent -- remaining names in "
                    f"this batch will be skipped (not checkpointed) until resumed."
                )
        elif checkpoint_file:
            done_names.add(result["Talabat_Restaurant_Name_Clean"])
            if completed % progress_every == 0:
                save_checkpoint(checkpoint_file, done_names, prior_cost + extractor.estimated_cost_usd)

        if completed % progress_every == 0 or completed == len(pending):
            elapsed = time.monotonic() - start
            rate = completed / elapsed if elapsed > 0 else 0
            logger.info(
                f"[pipeline] {completed}/{len(pending)} done | "
                f"found={found_count} ({found_count/completed*100:.1f}%) | "
                f"{rate:.2f} names/s | "
                f"tavily_keys_active={rotator_searcher.rotator.keys_remaining}/{len(rotator_searcher.rotator._keys)} | "
                f"openai_cost=${extractor.estimated_cost_usd:.4f}"
            )

    await asyncio.gather(*(_worker(r) for r in pending))

    if checkpoint_file:
        save_checkpoint(checkpoint_file, done_names, prior_cost + extractor.estimated_cost_usd)

    # Post-hoc guardrail: catch the same-phone/address-across-different-names
    # pattern (shared landmark/mall switchboard, not a real per-business
    # contact) that a single-name-at-a-time check can't see.
    if all_results:
        flagged_df = flag_shared_contact_duplicates(pd.DataFrame(all_results))
        n_downgraded = int(flagged_df["Suspicious_Shared_Contact"].sum())
        if n_downgraded:
            logger.warning(
                f"[pipeline] downgraded {n_downgraded} rows sharing a phone/address "
                f"with a different, unrelated business name"
            )
            found_count = int(flagged_df["Found"].sum())
        all_results = flagged_df.to_dict("records")

    elapsed = time.monotonic() - start
    logger.info(
        f"[pipeline] DONE — {completed} names in {elapsed:.1f}s | "
        f"found={found_count} ({found_count/max(completed,1)*100:.1f}%) | "
        f"openai_calls={extractor.calls_made} | "
        f"chunk_cost=${extractor.estimated_cost_usd:.4f} | "
        f"total_cost_so_far=${prior_cost + extractor.estimated_cost_usd:.4f} | "
        f"tavily_searches={rotator_searcher.rotator.searches_made} | "
        f"tavily_keys_blacklisted={len(rotator_searcher.rotator._blacklisted)}/{len(rotator_searcher.rotator._keys)}"
    )
    return all_results
