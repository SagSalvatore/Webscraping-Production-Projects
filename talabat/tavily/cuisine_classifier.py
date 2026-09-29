"""
cuisine_classifier.py — Hybrid async cuisine classifier.

Routing per restaurant:
  PATH A (KEY CUISINES → OpenAI)
    → 163 restaurants have clean KEY CUISINES
    → Feed name + KEY CUISINES + restaurant type into GPT-4o-mini
    → 0 Tavily credits used

  PATH B (Tavily web search → OpenAI)
    → 11 restaurants with no KEY CUISINES + ~6 with garbled data
    → Concurrent Tavily search first, then OpenAI on results
    → ~17 Tavily credits total

All 174 unique restaurants → 18 concurrent OpenAI batches of 10
AutoSave every 10 items · Jitter backoff · Loguru logging
"""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from tavily import AsyncTavilyClient

from cuisine_config import (
    CUISINE_TYPES, SUB_CUISINE_TYPES, needs_tavily,
    OPENAI_MODEL, OPENAI_BATCH_SIZE, OPENAI_MAX_TOKENS, OPENAI_TEMPERATURE,
    TAVILY_MAX_RESULTS, TAVILY_SEARCH_DEPTH, TAVILY_CONCURRENCY, OPENAI_CONCURRENCY,
    COL_CUISINE_TYPE, COL_SUB_CUISINE, COL_CUISINE_CONF, COL_CUISINE_REASON,
)
from config   import clean_name
from utils    import get_logger, retry_async, AutoSaveManager
from utils.retry import DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, DEFAULT_CAP_DELAY

log = get_logger(__name__)

# ── Prompt helpers ────────────────────────────────────────────────────────────

def _cuisine_types_str() -> str:
    return "\n".join(f"  {i+1}. {t}" for i, t in enumerate(CUISINE_TYPES))

def _sub_cuisine_str() -> str:
    return "\n".join(f"  - {t}" for t in SUB_CUISINE_TYPES)


def _build_cuisine_prompt(batch: list[dict]) -> str:
    """
    batch items: {name, key_cuisines, type_of_restaurant, context_text}
    Returns a single prompt for classifying all items in the batch.
    """
    items_block = "".join(
        f"\nRESTAURANT {i}:\n"
        f"  Name              : {item['name']}\n"
        f"  Key Cuisines      : {item.get('key_cuisines') or 'Not available'}\n"
        f"  Restaurant Type   : {item.get('type_of_restaurant', '')}\n"
        f"  Additional Context: {str(item.get('context_text', ''))[:500]}\n"
        for i, item in enumerate(batch, 1)
    )

    return f"""You are a UAE restaurant cuisine classification expert.
Classify each restaurant's cuisine using ONLY the taxonomies provided below.

━━ CUISINE TYPE (choose exactly ONE) ━━
{_cuisine_types_str()}

━━ SUB-CUISINE TYPES (choose 1-3 from this list, comma-separated) ━━
{_sub_cuisine_str()}

RULES:
1. Use "Key Cuisines" as the primary signal — it directly reflects what the restaurant serves.
2. If Key Cuisines is not available, use "Additional Context" (web search result).
3. "Multi-Cuisine" only if the restaurant genuinely spans 3+ very different cuisine families.
4. Sub-cuisine must be chosen from the list above (pick closest match if exact not found).
5. Bakery / café places → Cuisine Type should be "Bakery" or "Café" respectively.

Return ONLY a valid JSON array with {len(batch)} objects:
[
  {{
    "restaurant_index": 1,
    "name": "<name>",
    "cuisine_type": "<exact value from Cuisine Type list>",
    "sub_cuisine_types": "<comma-separated values from Sub-cuisine list>",
    "confidence": "High" | "Medium" | "Low",
    "reasoning": "<one sentence>"
  }},
  ...
]

RESTAURANTS:
{items_block}"""


# ── Response normaliser ───────────────────────────────────────────────────────

def _normalise_cuisine_type(raw: str) -> str:
    """Fuzzy-map OpenAI output back to a canonical Cuisine Type."""
    if not raw:
        return "Multi-Cuisine"
    raw_low = raw.lower().strip()
    mapping = {
        "asian"          : "Asian",
        "american"       : "American",
        "café"           : "Café",
        "cafe"           : "Café",
        "coffee"         : "Café",
        "multi"          : "Multi-Cuisine",
        "middle eastern" : "Middle Eastern and North African",
        "north african"  : "Middle Eastern and North African",
        "arabic"         : "Middle Eastern and North African",
        "bakery"         : "Bakery",
        "mediterranean"  : "Mediterranean",
        "european"       : "European",
        "western"        : "European",
        "beverages"      : "Beverages",
    }
    for frag, canonical in mapping.items():
        if frag in raw_low:
            return canonical
    # Try exact match (case-insensitive)
    for ct in CUISINE_TYPES:
        if ct.lower() == raw_low:
            return ct
    log.warning("Unrecognised cuisine type '{}' — defaulting to Multi-Cuisine", raw)
    return "Multi-Cuisine"


def _parse_cuisine_response(raw: str, expected: int) -> list[dict]:
    """Parse OpenAI JSON response → list of cuisine result dicts."""
    fallback = {
        COL_CUISINE_TYPE  : "Multi-Cuisine",
        COL_SUB_CUISINE   : "International",
        COL_CUISINE_CONF  : "Low",
        COL_CUISINE_REASON: "Parse fallback",
    }
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for k in ("results", "restaurants", "data", "classifications"):
                if k in parsed and isinstance(parsed[k], list):
                    parsed = parsed[k]
                    break
            else:
                lists = [v for v in parsed.values() if isinstance(v, list)]
                parsed = lists[0] if lists else []

        results = []
        for item in parsed[:expected]:
            results.append({
                COL_CUISINE_TYPE  : _normalise_cuisine_type(item.get("cuisine_type", "")),
                COL_SUB_CUISINE   : item.get("sub_cuisine_types", "International"),
                COL_CUISINE_CONF  : item.get("confidence", "Medium"),
                COL_CUISINE_REASON: item.get("reasoning", ""),
            })
        while len(results) < expected:
            results.append(fallback.copy())
        return results
    except Exception as e:
        log.error("Cuisine response parse error: {} | raw={}", e, raw[:200])
        return [fallback.copy() for _ in range(expected)]


# ── Main classifier ───────────────────────────────────────────────────────────

class CuisineClassifier:
    """
    Async hybrid classifier:
      - PATH A: KEY CUISINES → OpenAI (no Tavily cost)
      - PATH B: Tavily search → OpenAI
    """

    def __init__(self, openai_client: AsyncOpenAI, tavily_client: AsyncTavilyClient):
        self._openai  = openai_client
        self._tavily  = tavily_client
        self._sem_oai = asyncio.Semaphore(OPENAI_CONCURRENCY)
        self._sem_tv  = asyncio.Semaphore(TAVILY_CONCURRENCY)
        self._tv_cache: dict[str, str] = {}

        self.openai_calls  = 0
        self.tavily_calls  = 0
        self.tokens_used   = 0

    # ── Tavily search ─────────────────────────────────────────────────────────
    async def _tavily_search(self, name: str) -> str:
        key = name.lower().strip()
        if key in self._tv_cache:
            return self._tv_cache[key]

        query = f'"{name}" restaurant UAE cuisine type menu food'
        async with self._sem_tv:
            try:
                resp = await retry_async(
                    self._tavily.search,
                    query=query,
                    search_depth=TAVILY_SEARCH_DEPTH,
                    max_results=TAVILY_MAX_RESULTS,
                    include_answer=True,
                )
                self.tavily_calls += 1
                parts = []
                if resp.get("answer"):
                    parts.append(f"Summary: {resp['answer']}")
                for r in resp.get("results", []):
                    parts.append(f"{r.get('content','')[:400]}")
                text = "\n".join(parts)
            except Exception as e:
                log.error("Tavily error for '{}': {}", name, e)
                text = ""
        self._tv_cache[key] = text
        return text

    # ── OpenAI batch classify ─────────────────────────────────────────────────
    async def _classify_batch(self, batch: list[dict]) -> list[dict]:
        prompt = _build_cuisine_prompt(batch)
        async with self._sem_oai:
            resp = await retry_async(
                self._openai.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a UAE restaurant cuisine taxonomy expert. "
                            "Always respond with valid JSON only, no extra text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=OPENAI_TEMPERATURE,
                max_tokens=OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
        self.openai_calls += 1
        self.tokens_used  += resp.usage.prompt_tokens + resp.usage.completion_tokens
        return _parse_cuisine_response(resp.choices[0].message.content, len(batch))

    # ── Main classify_all ────────────────────────────────────────────────────
    async def classify_all(
        self,
        records: list[dict],
        autosave: AutoSaveManager,
    ) -> list[dict]:
        """
        records: list of restaurant dicts with keys:
          matched_uae_name, UAE_KEY CUISINES, UAE_Type of Restaurants, classification
        """
        results: list[Optional[dict]] = [None] * len(records)

        # Load already-saved results
        skip = 0
        pending_indices: list[int] = []
        for i, rec in enumerate(records):
            key = rec[COL_CUISINE_TYPE[4:] if False else "matched_uae_name"]  # raw name
            if autosave.contains(key):
                results[i] = autosave.get(key)
                skip += 1
            else:
                pending_indices.append(i)

        if skip:
            log.info("Skipped {} already-classified restaurants (autosave resume)", skip)

        # ── STEP 1: Concurrent Tavily searches for PATH B restaurants ────────
        tavily_needed = [
            i for i in pending_indices
            if needs_tavily(records[i].get("UAE_KEY CUISINES"))
        ]
        path_a_count = len(pending_indices) - len(tavily_needed)
        log.info(
            "Routing: PATH A (Key Cuisines) = {} | PATH B (Tavily+AI) = {}",
            path_a_count, len(tavily_needed),
        )

        search_cache: dict[int, str] = {}
        if tavily_needed:
            log.info("Firing {} Tavily searches concurrently ...", len(tavily_needed))
            tv_tasks = {
                i: asyncio.create_task(
                    self._tavily_search(clean_name(records[i]["matched_uae_name"]))
                )
                for i in tavily_needed
            }
            for i, task in tv_tasks.items():
                search_cache[i] = await task

        # ── STEP 2: Build batches and fire all OpenAI calls concurrently ─────
        buf_idx: list[int]  = []
        buf_rec: list[dict] = []
        batches: list[tuple[list[int], list[dict]]] = []

        for i in pending_indices:
            rec = records[i]
            key_cuis = rec.get("UAE_KEY CUISINES", "")
            buf_idx.append(i)
            buf_rec.append({
                "raw_name"         : rec["matched_uae_name"],
                "name"             : clean_name(rec["matched_uae_name"]),
                "key_cuisines"     : key_cuis if not needs_tavily(key_cuis) else "",
                "type_of_restaurant": rec.get("UAE_Type of Restaurants", ""),
                "classification"   : rec.get("classification", ""),
                "context_text"     : search_cache.get(i, ""),
            })
            if len(buf_idx) >= OPENAI_BATCH_SIZE:
                batches.append((buf_idx[:], buf_rec[:]))
                buf_idx.clear(); buf_rec.clear()
        if buf_idx:
            batches.append((buf_idx[:], buf_rec[:]))

        log.info("Firing {} OpenAI batches concurrently (batch={}) ...", len(batches), OPENAI_BATCH_SIZE)

        async def _process(indices: list[int], recs: list[dict]) -> None:
            batch_results = await self._classify_batch(recs)
            for idx, res in zip(indices, batch_results):
                raw_name = records[idx]["matched_uae_name"]
                results[idx] = res
                await autosave.add(raw_name, res)
            log.info(
                "Batch done ({} calls so far) | tokens: {}",
                self.openai_calls, self.tokens_used,
            )

        await asyncio.gather(*[
            asyncio.create_task(_process(idxs, recs))
            for idxs, recs in batches
        ])

        # Fill any remaining None
        fallback = {
            COL_CUISINE_TYPE  : "Multi-Cuisine",
            COL_SUB_CUISINE   : "International",
            COL_CUISINE_CONF  : "Low",
            COL_CUISINE_REASON: "Fallback",
        }
        for i in range(len(results)):
            if results[i] is None:
                results[i] = fallback.copy()

        log.info(
            "Cuisine classification done. OpenAI calls: {} | Tavily calls: {} | "
            "Tokens: {} | Est cost: ${:.4f}",
            self.openai_calls, self.tavily_calls,
            self.tokens_used,
            (self.tokens_used / 1_000_000) * 0.375,
        )
        return results
