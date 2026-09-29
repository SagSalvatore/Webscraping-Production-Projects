"""Async gpt-5-mini client for the area values the rules cannot resolve.

Cost control:
  * only DISTINCT area strings are sent (1,234 of them, behind 3,741 rows)
  * BATCH_SIZE values per request, so the system prompt and the model's
    reasoning tokens are amortised (measured 2.2x cheaper than 1-per-call)
  * every result is cached to disk -> re-runs are free

Reliability: bounded concurrency + tenacity retry with exponential backoff,
and a per-batch fallback so one bad batch cannot lose the whole run.
"""
import asyncio
import json
import time

import orjson
from loguru import logger
from openai import AsyncOpenAI
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential, before_sleep_log)

from config import (LLM_CACHE, MAX_CONCURRENCY, MODEL, OPENAI_KEY,
                    REQUEST_TIMEOUT)

BATCH_SIZE = 20
PRICE_IN, PRICE_OUT = 0.25, 2.00        # gpt-5-mini USD / 1M tokens

SYSTEM = (
    "You normalise UAE location strings. For EACH numbered input, return the "
    "real UAE area/community/district name.\n"
    "Rules:\n"
    "- drop floor/shop/unit/building/mall/street references\n"
    "- drop zone codes (Zone 1, RB6, E11, W44)\n"
    "- translate Arabic to its common English name\n"
    "- if a landmark or mall is given, return the area it sits in\n"
    "- if only a plus-code or meaningless, use null\n"
    "- KEEP sub-area granularity: 'Al Barsha First - Al Barsha' -> "
    "'Al Barsha First'; 'Jumeirah 1' stays 'Jumeirah 1', do not collapse to "
    "'Jumeirah'\n"
    "OUTPUT FORMAT - strict:\n"
    "- bare area name ONLY: no parentheses, no qualifiers, no city suffix. "
    '"Al Fahidi", never "Al Fahidi (Bur Dubai)"\n'
    "- do NOT add or remove an 'Al ' prefix that is not in the source\n"
    "- if you cannot name a specific area, return null rather than a city "
    '(never return "Dubai" or "Abu Dhabi" as the area)\n'
    'Return JSON: {"results":[{"i":<index>,"area":<string|null>,'
    '"confidence":<0-1>}]} - one entry per input, in the same order.'
)


class LLMStats:
    def __init__(self):
        self.calls = self.tok_in = self.tok_out = 0
        self.failed_batches = 0

    @property
    def cost(self):
        return self.tok_in / 1e6 * PRICE_IN + self.tok_out / 1e6 * PRICE_OUT

    def line(self):
        return (f"calls={self.calls} in={self.tok_in:,} out={self.tok_out:,} "
                f"cost=${self.cost:.4f} failed_batches={self.failed_batches}")


CHECKPOINT_EVERY = 5        # batches (~100 values) between cache flushes


def load_cache():
    if LLM_CACHE.exists():
        try:
            return orjson.loads(LLM_CACHE.read_bytes())
        except Exception as exc:
            logger.warning(f"cache unreadable ({exc}) - starting empty")
    return {}


def save_cache(cache):
    """Atomic: write to .tmp then replace, so a crash mid-write cannot leave a
    truncated cache that would force a full (paid) re-run."""
    tmp = LLM_CACHE.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(cache, option=orjson.OPT_INDENT_2))
    tmp.replace(LLM_CACHE)


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, "WARNING"),
    reraise=True,
)
async def _call(client, payload):
    return await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": payload}],
        response_format={"type": "json_object"},
    )


async def _run_batch(client, sem, batch, stats, out):
    payload = "\n".join(f"{i}. {v}" for i, v in enumerate(batch))
    async with sem:
        try:
            resp = await _call(client, payload)
        except Exception as exc:
            stats.failed_batches += 1
            logger.error(f"batch failed after retries ({len(batch)} items): "
                         f"{type(exc).__name__}: {exc}")
            for v in batch:
                out[v] = {"area": None, "confidence": 0.0, "error": str(exc)[:120]}
            return

    u = resp.usage
    stats.calls += 1
    stats.tok_in += u.prompt_tokens
    stats.tok_out += u.completion_tokens

    try:
        results = json.loads(resp.choices[0].message.content)["results"]
    except Exception as exc:
        stats.failed_batches += 1
        logger.error(f"unparseable response: {exc}")
        for v in batch:
            out[v] = {"area": None, "confidence": 0.0, "error": "unparseable"}
        return

    by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
    for i, v in enumerate(batch):
        r = by_i.get(i)
        if r is None:
            # index missing from the response - do not silently mis-align
            out[v] = {"area": None, "confidence": 0.0, "error": "missing_index"}
            continue
        area = r.get("area")
        if isinstance(area, str):
            area = area.strip() or None
        out[v] = {"area": area, "confidence": r.get("confidence")}


async def resolve_areas(values, use_cache=True):
    """values: iterable of distinct area strings -> {value: {area, confidence}}"""
    if not OPENAI_KEY:
        raise RuntimeError("OPEN_AI_API not set in talabat/.env")

    cache = load_cache() if use_cache else {}
    todo = [v for v in dict.fromkeys(values) if v not in cache]
    logger.info(f"{len(values):,} distinct values | cached {len(values)-len(todo):,} "
                f"| to fetch {len(todo):,}")
    if not todo:
        return {v: cache[v] for v in values}

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    logger.info(f"{len(batches)} batches of <={BATCH_SIZE}, concurrency={MAX_CONCURRENCY}, "
                f"model={MODEL}")

    stats = LLMStats()
    out, sem = {}, asyncio.Semaphore(MAX_CONCURRENCY)
    client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=REQUEST_TIMEOUT)

    t0 = time.time()
    tasks = [_run_batch(client, sem, b, stats, out) for b in batches]
    done = 0
    try:
        for fut in asyncio.as_completed(tasks):
            await fut
            done += 1
            # CHECKPOINT: flush finished work to disk as we go, so a dropped
            # connection / Ctrl-C never costs money already spent. A re-run
            # skips everything already cached.
            if done % CHECKPOINT_EVERY == 0 or done == len(batches):
                cache.update(out)
                save_cache(cache)
            if done % 10 == 0 or done == len(batches):
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (len(batches) - done) / rate if rate else 0
                logger.info(f"  {done}/{len(batches)} batches | {stats.line()} | "
                            f"ETA {eta:.0f}s")
    except (KeyboardInterrupt, asyncio.CancelledError):
        cache.update(out)
        save_cache(cache)
        logger.warning(f"interrupted after {done}/{len(batches)} batches - "
                       f"{len(out):,} results checkpointed to {LLM_CACHE.name}. "
                       f"Re-run to resume; cached values are not re-billed.")
        raise
    except Exception:
        cache.update(out)
        save_cache(cache)
        logger.error(f"aborted after {done}/{len(batches)} batches - "
                     f"{len(out):,} results checkpointed. Re-run to resume.")
        raise

    cache.update(out)
    save_cache(cache)
    logger.success(f"LLM done in {time.time()-t0:.1f}s | {stats.line()}")
    return {v: cache.get(v, {"area": None, "confidence": 0.0}) for v in values}
