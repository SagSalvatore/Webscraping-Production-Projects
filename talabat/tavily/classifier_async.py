"""
classifier_async.py — Fully async, concurrent classification engine.

Performance vs sequential:
  Sequential (old):  ~2.5s/Tavily × 138 + ~7s/OpenAI × 28 ≈ 9 minutes
  Async (this file): 20 concurrent Tavily + all OpenAI batches concurrent ≈ 20-30 seconds

Concurrency model:
  - Tavily:  asyncio.Semaphore(20) — 20 searches fire simultaneously
  - OpenAI:  asyncio.Semaphore(28) — all 28 batches fire simultaneously
             (Tier-2 = 4500 RPM = 75 req/s, 28 calls is trivial)
  - Cache write: asyncio.Lock protects the JSON files
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from tavily import AsyncTavilyClient

from config import (
    OPENAI_MODEL, OPENAI_BATCH_SIZE, OPENAI_TEMPERATURE, OPENAI_MAX_TOKENS,
    TAVILY_MAX_RESULTS, TAVILY_SEARCH_DEPTH, TAVILY_CREDIT_LIMIT,
    clean_name, pre_classify, RESTAURANT_TYPES,
    COL_OUTLET_TYPE, COL_CHAINED_TYPE, COL_TYPE_OF_REST,
    COL_CONFIDENCE, COL_REASONING, COL_CLASSIFICATION,
)

# ── Concurrency limits ────────────────────────────────────────────────────────
TAVILY_CONCURRENCY = 20   # max simultaneous Tavily requests
OPENAI_CONCURRENCY = 28   # max simultaneous OpenAI requests (Tier-2 = 4500 RPM)

log = logging.getLogger(__name__)

CACHE_DIR          = Path(__file__).parent / "cache"
TAVILY_CACHE_FILE  = CACHE_DIR / "tavily_cache.json"
RESULTS_CACHE_FILE = CACHE_DIR / "results_cache.json"


# ── Dotenv loader (handles "KEY"=value format) ────────────────────────────────
def _load_dotenv_robust(env_path: Path = None) -> None:
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        load_dotenv()
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().strip("\"'")
            val = val.strip().strip("\"'")
            if key:
                os.environ.setdefault(key, val)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _make_empty_result(reason: str = "") -> dict:
    return {
        COL_CLASSIFICATION: "Unknown", COL_OUTLET_TYPE: "Unknown",
        COL_CHAINED_TYPE: "N/A",       COL_TYPE_OF_REST: "Unknown",
        COL_CONFIDENCE: "Low",         COL_REASONING: reason,
    }

def _known_to_result(known_tuple: tuple) -> dict:
    classification, outlet_type, chained_type, type_of_rest = known_tuple
    return {
        COL_CLASSIFICATION: classification,
        COL_OUTLET_TYPE:    outlet_type,
        COL_CHAINED_TYPE:   chained_type,
        COL_TYPE_OF_REST:   type_of_rest,
        COL_CONFIDENCE:     "High",
        COL_REASONING:      "Pre-classified from known-chain lookup (no API needed).",
    }

def _build_batch_prompt(batch: list[dict]) -> str:
    type_options = "\n".join(f"  - {t}" for t in RESTAURANT_TYPES)
    items_block  = "".join(
        f"\n---\nRESTAURANT {i}: {item['name']}\nSEARCH RESULTS:\n{item['search_text'][:1200]}\n"
        for i, item in enumerate(batch, 1)
    )
    return f"""You are a restaurant classification expert specialising in UAE/GCC restaurants.
Classify EACH restaurant below and return a JSON array.

CLASSIFICATION RULES:
- "Independent"  : Single establishment, no other branches
- "Local Chain"  : Multiple locations within UAE/GCC region only
- "MNC Chain"    : International chain with branches in multiple countries

TYPE OF RESTAURANT options:
{type_options}

For each restaurant provide:
  {{
    "name": "<restaurant name>",
    "classification": "Independent" | "Local Chain" | "MNC Chain",
    "outlet_type": "Independent" | "Chain",
    "chained_outlet_type": "Local Chain" | "MNC Chain" | "N/A",
    "type_of_restaurant": "<one of the types above>",
    "confidence": "High" | "Medium" | "Low",
    "reasoning": "<1-2 sentence explanation>",
    "locations_found": ["<location1>", "..."]
  }}

Return ONLY a valid JSON array with {len(batch)} objects, no extra text.

RESTAURANTS TO CLASSIFY:
{items_block}"""


# ── Async classifier ──────────────────────────────────────────────────────────

class AsyncRestaurantClassifier:

    def __init__(self):
        _load_dotenv_robust()
        api_key_tavily = os.getenv("TAVILY_API_KEY", "").strip("\"'")
        api_key_openai = os.getenv("OPENAI_API_KEY", "").strip("\"'")
        if not api_key_tavily:
            raise EnvironmentError("TAVILY_API_KEY not set in .env")
        if not api_key_openai:
            raise EnvironmentError("OPENAI_API_KEY not set in .env")

        CACHE_DIR.mkdir(exist_ok=True)
        self._tavily_cache  = _load_json(TAVILY_CACHE_FILE)
        self._results_cache = _load_json(RESULTS_CACHE_FILE)

        self.tavily  = AsyncTavilyClient(api_key=api_key_tavily)
        self.openai  = AsyncOpenAI(api_key=api_key_openai)

        # Semaphores for rate limiting
        self._tavily_sem = asyncio.Semaphore(TAVILY_CONCURRENCY)
        self._openai_sem = asyncio.Semaphore(OPENAI_CONCURRENCY)
        self._cache_lock = asyncio.Lock()

        # Stats (thread-safe via GIL for simple ints)
        self.tavily_calls_made = 0
        self.openai_calls_made = 0
        self.total_tokens_used = 0

    # ── Async Tavily search ───────────────────────────────────────────────────
    async def _search_one(self, name: str) -> str:
        key = name.lower().strip()

        async with self._cache_lock:
            if key in self._tavily_cache:
                return self._tavily_cache[key]

        if self.tavily_calls_made >= TAVILY_CREDIT_LIMIT:
            log.warning("Tavily credit limit reached — skipping: %s", name)
            return ""

        query = f'"{name}" restaurant UAE chain locations branches'
        async with self._tavily_sem:
            try:
                resp = await self.tavily.search(
                    query=query,
                    search_depth=TAVILY_SEARCH_DEPTH,
                    max_results=TAVILY_MAX_RESULTS,
                    include_answer=True,
                )
                self.tavily_calls_made += 1
                parts = []
                if resp.get("answer"):
                    parts.append(f"Summary: {resp['answer']}")
                for r in resp.get("results", []):
                    parts.append(f"Source: {r.get('url','')}\n{r.get('content','')[:400]}")
                text = "\n\n".join(parts)
            except Exception as e:
                log.error("Tavily error for '%s': %s", name, e)
                text = ""

        async with self._cache_lock:
            self._tavily_cache[key] = text
            _save_json(TAVILY_CACHE_FILE, self._tavily_cache)
        return text

    # ── Async OpenAI batch classify ───────────────────────────────────────────
    async def _classify_batch(self, batch: list[dict]) -> list[dict]:
        prompt = _build_batch_prompt(batch)
        async with self._openai_sem:
            try:
                resp = await self.openai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an expert restaurant analyst specialising in "
                                "UAE chain vs independent classification. "
                                "Always respond with valid JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=OPENAI_TEMPERATURE,
                    max_tokens=OPENAI_MAX_TOKENS,
                    response_format={"type": "json_object"},
                )
                self.openai_calls_made += 1
                usage = resp.usage
                self.total_tokens_used += usage.prompt_tokens + usage.completion_tokens

                raw    = resp.choices[0].message.content
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for k in ("results", "restaurants", "classifications", "data"):
                        if k in parsed:
                            parsed = parsed[k]
                            break
                    else:
                        parsed = list(parsed.values())[0] if parsed else []

                results = []
                for i, item in enumerate(batch):
                    cls_data = parsed[i] if i < len(parsed) else {}
                    classification = cls_data.get("classification", "Unknown")
                    results.append({
                        COL_CLASSIFICATION: classification,
                        COL_OUTLET_TYPE:    cls_data.get("outlet_type",
                                                "Independent" if classification == "Independent" else "Chain"),
                        COL_CHAINED_TYPE:   cls_data.get("chained_outlet_type",
                                                "N/A" if classification == "Independent" else classification),
                        COL_TYPE_OF_REST:   cls_data.get("type_of_restaurant", "Unknown"),
                        COL_CONFIDENCE:     cls_data.get("confidence", "Low"),
                        COL_REASONING:      cls_data.get("reasoning", ""),
                    })
                return results

            except json.JSONDecodeError as e:
                log.error("JSON parse error: %s", e)
                return [_make_empty_result("OpenAI returned unparseable JSON") for _ in batch]
            except Exception as e:
                log.error("OpenAI error: %s", e)
                return [_make_empty_result(str(e)) for _ in batch]

    # ── Main entry point ──────────────────────────────────────────────────────
    async def classify_all(self, raw_names: list[str]) -> list[dict]:
        results: list[Optional[dict]] = [None] * len(raw_names)

        # Pass 1: known lookup + results cache (instant, no I/O)
        api_needed_indices = []
        for i, raw in enumerate(raw_names):
            if raw in self._results_cache:
                results[i] = self._results_cache[raw]
                continue
            known = pre_classify(clean_name(raw))
            if known:
                results[i] = _known_to_result(known)
                self._results_cache[raw] = results[i]
            else:
                api_needed_indices.append(i)

        n_precl = len(raw_names) - len(api_needed_indices)
        log.info("Pre-classified: %d  |  Needs API: %d", n_precl, len(api_needed_indices))

        if not api_needed_indices:
            return [r or _make_empty_result() for r in results]

        # Pass 2: ALL Tavily searches concurrently (semaphore caps at 20)
        log.info("Firing %d Tavily searches concurrently (max %d at a time)...",
                 len(api_needed_indices), TAVILY_CONCURRENCY)
        t_tavily = time.perf_counter()

        search_tasks = {
            i: asyncio.create_task(self._search_one(clean_name(raw_names[i])))
            for i in api_needed_indices
        }
        search_results: dict[int, str] = {}
        for i, task in search_tasks.items():
            search_results[i] = await task

        log.info("All Tavily searches done in %.1fs", time.perf_counter() - t_tavily)

        # Pass 3: ALL OpenAI batches concurrently (semaphore caps at 28)
        # Build batches
        batches: list[tuple[list[int], list[dict]]] = []
        buf_idx: list[int]  = []
        buf_dat: list[dict] = []
        for i in api_needed_indices:
            raw = raw_names[i]
            buf_idx.append(i)
            buf_dat.append({"raw_name": raw, "name": clean_name(raw), "search_text": search_results[i]})
            if len(buf_idx) >= OPENAI_BATCH_SIZE:
                batches.append((buf_idx[:], buf_dat[:]))
                buf_idx.clear()
                buf_dat.clear()
        if buf_idx:
            batches.append((buf_idx[:], buf_dat[:]))

        log.info("Firing %d OpenAI batch calls concurrently (max %d at a time)...",
                 len(batches), OPENAI_CONCURRENCY)
        t_openai = time.perf_counter()

        batch_tasks = [
            asyncio.create_task(self._classify_batch(dat))
            for _, dat in batches
        ]
        batch_outputs = await asyncio.gather(*batch_tasks)

        log.info("All OpenAI calls done in %.1fs", time.perf_counter() - t_openai)

        # Merge results back
        for (indices, _), batch_res in zip(batches, batch_outputs):
            for idx, res in zip(indices, batch_res):
                raw = raw_names[idx]
                results[idx] = res
                self._results_cache[raw] = res

        async with self._cache_lock:
            _save_json(RESULTS_CACHE_FILE, self._results_cache)

        log.info(
            "DONE. Tavily: %d calls | OpenAI: %d calls | Tokens: %d",
            self.tavily_calls_made, self.openai_calls_made, self.total_tokens_used,
        )
        return [r or _make_empty_result() for r in results]


# ── Sync wrapper (drop-in replacement for RestaurantClassifier) ───────────────
def classify_all_sync(raw_names: list[str]) -> tuple[list[dict], "AsyncRestaurantClassifier"]:
    """
    Synchronous wrapper — run from non-async code.
    Returns (results, classifier) so caller can read .tavily_calls_made etc.
    """
    clf = AsyncRestaurantClassifier()

    async def _run():
        return await clf.classify_all(raw_names)

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(_run())
    finally:
        loop.close()
    return results, clf
