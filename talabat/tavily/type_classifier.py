"""
type_classifier.py — Re-classify UAE_Type of Restaurants into exactly 5 categories.

Target categories (per user requirement):
  1. Full-Service Restaurants   — sit-down table-service (casual/fine dining)
  2. Quick-Service Restaurants  — counter service, no table service (fast food, QSR)
  3. Cafes                      — coffee shops, tea rooms, light-bites social spaces
  4. Bakery                     — bakeries, patisseries, pastry/dessert shops
  5. Cloud-Kitchen              — delivery-only virtual kitchens, no dine-in

Approach:
  • DIRECT MAP  — deterministic remapping for unambiguous existing types (0 API cost)
  • OPENAI ASYNC — all restaurants go through GPT-4o-mini for consistent labelling
    - Context: restaurant name + existing type hint + prior reasoning
    - Batch size: 10  →  170 restaurants = 17 OpenAI calls total
    - All batches fire concurrently (Tier-2, 4500 RPM)
    - Full jitter back-off on 429/timeout
    - AutoSave every 10 items

No Tavily calls needed — existing classification_results.csv has all context.
"""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from utils         import get_logger, retry_async, AutoSaveManager
from utils.retry   import DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY, DEFAULT_CAP_DELAY
from config        import clean_name

log = get_logger(__name__)

# ── 5-category taxonomy ───────────────────────────────────────────────────────
VALID_TYPES = {
    "Full-Service Restaurants",
    "Quick-Service Restaurants",
    "Cafes",
    "Bakery",
    "Cloud-Kitchen",
}

# ── Direct mapping: existing value → new 5-category (saves API calls for clear cases)
#    Used as CONTEXT HINT in the prompt, NOT as hard override
DIRECT_MAP: dict[str, str] = {
    "Casual Dining Restaurant"       : "Full-Service Restaurants",
    "Fine Dining Restaurant"         : "Full-Service Restaurants",
    "Bar & Grill"                    : "Full-Service Restaurants",
    "Quick-Service Restaurant (QSR)" : "Quick-Service Restaurants",
    "Food Court Stall"               : "Quick-Service Restaurants",
    "Café / Coffee Shop"             : "Cafes",
    "Coffee Shop"                    : "Cafes",
    "Bakery & Café"                  : "Bakery",          # OpenAI confirms
    "Patisserie / Dessert Shop"      : "Bakery",
    "Patisserie"                     : "Bakery",
    "Cloud Kitchen"                  : "Cloud-Kitchen",
    "Catering Service"               : "Cloud-Kitchen",
    "Fast-Casual Restaurant"         : "Quick-Service Restaurants",  # OpenAI confirms
}

# ── Concurrency knobs ─────────────────────────────────────────────────────────
BATCH_SIZE      = 10    # restaurants per OpenAI call
MAX_CONCURRENT  = 20    # max simultaneous OpenAI requests (Tier-2 headroom)

# ── OpenAI model config ───────────────────────────────────────────────────────
OPENAI_MODEL       = "gpt-4o-mini"
OPENAI_MAX_TOKENS  = 800
OPENAI_TEMPERATURE = 0.0   # deterministic — classification task


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_type_prompt(items: list[dict]) -> str:
    """
    Each item: {name, current_type, classification, reasoning}
    Returns a single prompt asking for JSON array with re-classified types.
    """
    items_block = "".join(
        f"\nRESTAURANT {i}:\n"
        f"  Name           : {item['name']}\n"
        f"  Current type   : {item['current_type']}\n"
        f"  Chain status   : {item['classification']}\n"
        f"  Context        : {item['reasoning'][:300]}\n"
        for i, item in enumerate(items, 1)
    )

    return f"""You are a UAE restaurant data expert. Reclassify each restaurant into EXACTLY ONE of these 5 categories:

1. Full-Service Restaurants — Table service, dine-in (casual/fine dining, buffet)
2. Quick-Service Restaurants — Counter/kiosk service, fast food, takeaway-first, no table waiter
3. Cafes — Coffee shops, tea cafes, light bites & beverages, social/co-working spaces
4. Bakery — Bakeries, patisseries, pastry/dessert/cake shops (may also serve coffee)
5. Cloud-Kitchen — Delivery-only, no dine-in, virtual kitchen

Rules:
- If a place is PRIMARILY a bakery that also sells coffee → Bakery (not Cafes)
- If a place is PRIMARILY a cafe that sells some baked goods → Cafes (not Bakery)
- Fast-food / QSR with counter service → Quick-Service Restaurants
- Delivery-only brands (no physical dining) → Cloud-Kitchen

Return ONLY a valid JSON array with {len(items)} objects:
[
  {{
    "restaurant_index": 1,
    "name": "<name>",
    "type": "<one of the 5 exact category names above>",
    "confidence": "High" | "Medium" | "Low",
    "reasoning": "<one sentence>"
  }},
  ...
]

RESTAURANTS:
{items_block}"""


# ── Batch classifier ──────────────────────────────────────────────────────────

class TypeClassifier:
    """
    Async classifier that re-labels UAE_Type of Restaurants
    into the 5 canonical categories.
    """

    def __init__(self, openai_client: AsyncOpenAI, sem: asyncio.Semaphore):
        self._client = openai_client
        self._sem    = sem
        self.calls_made   = 0
        self.tokens_used  = 0

    async def classify_batch(self, items: list[dict]) -> list[dict]:
        """
        Classify a batch of restaurants.
        items: list of {raw_name, name, current_type, classification, reasoning}
        Returns list of result dicts in same order.
        """
        prompt = _build_type_prompt(items)

        async with self._sem:
            response = await retry_async(
                self._client.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a UAE restaurant taxonomy expert. "
                            "Always respond with valid JSON only, no extra text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=OPENAI_TEMPERATURE,
                max_tokens=OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                max_retries=DEFAULT_MAX_RETRIES,
                base_delay=DEFAULT_BASE_DELAY,
                cap_delay=DEFAULT_CAP_DELAY,
            )

        self.calls_made  += 1
        self.tokens_used += response.usage.prompt_tokens + response.usage.completion_tokens

        raw    = response.choices[0].message.content
        parsed = _parse_response(raw, len(items))
        return parsed

    async def classify_all(
        self,
        records: list[dict],
        autosave: AutoSaveManager,
    ) -> list[dict]:
        """
        Classify all records concurrently in batches.
        records: from classification_results.csv rows
        Returns list of result dicts, order preserved.
        """
        # Split into batches
        batches: list[tuple[list[int], list[dict]]] = []
        buf_idx: list[int]  = []
        buf_rec: list[dict] = []

        skip_count = 0
        for i, rec in enumerate(records):
            raw_name = rec["matched_uae_name"]
            if autosave.contains(raw_name):
                skip_count += 1
                continue
            buf_idx.append(i)
            # Transform CSV row into the shape classify_batch / _build_type_prompt expects
            buf_rec.append({
                "raw_name"      : raw_name,
                "name"          : clean_name(raw_name),
                "current_type"  : rec.get("UAE_Type of Restaurants", ""),
                "classification": rec.get("classification", ""),
                "reasoning"     : rec.get("reasoning", ""),
            })
            if len(buf_idx) >= BATCH_SIZE:
                batches.append((buf_idx[:], buf_rec[:]))
                buf_idx.clear()
                buf_rec.clear()
        if buf_idx:
            batches.append((buf_idx[:], buf_rec[:]))

        if skip_count:
            log.info("Skipped {} already-saved restaurants (autosave resume)", skip_count)

        log.info(
            "Firing {} OpenAI batches concurrently (batch={}, max_concurrent={}) ...",
            len(batches), BATCH_SIZE, MAX_CONCURRENT,
        )

        # Pre-build result list from autosave + placeholders
        results: list[Optional[dict]] = [None] * len(records)
        for i, rec in enumerate(records):
            saved = autosave.get(rec["matched_uae_name"])
            if saved:
                results[i] = saved

        # Fire all batches concurrently
        async def _process_batch(indices: list[int], recs: list[dict]) -> None:
            batch_results = await self.classify_batch(recs)
            for idx, res in zip(indices, batch_results):
                raw_name      = records[idx]["matched_uae_name"]
                results[idx]  = res
                await autosave.add(raw_name, res)
            log.info(
                "Batch done ({}/{} total calls) | tokens so far: {}",
                self.calls_made, len(batches), self.tokens_used,
            )

        tasks = [
            asyncio.create_task(_process_batch(idxs, recs))
            for idxs, recs in batches
        ]
        await asyncio.gather(*tasks)

        # Fill any None slots with fallback
        for i, rec in enumerate(records):
            if results[i] is None:
                existing = rec.get("UAE_Type of Restaurants", "")
                mapped   = DIRECT_MAP.get(existing, "Full-Service Restaurants")
                results[i] = {
                    "type":       mapped,
                    "confidence": "Low",
                    "reasoning":  "Fallback direct-map (OpenAI call missed).",
                }

        log.info(
            "Classification complete. Calls: {} | Tokens: {} | Est cost: ${:.4f}",
            self.calls_made,
            self.tokens_used,
            (self.tokens_used / 1_000_000) * 0.375,  # gpt-4o-mini blended rate
        )
        return results


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: str, expected: int) -> list[dict]:
    """Parse OpenAI JSON response, returning a result dict per item."""
    try:
        parsed = json.loads(raw)
        # Unwrap common wrapper keys
        if isinstance(parsed, dict):
            for k in ("results", "restaurants", "data", "classifications"):
                if k in parsed and isinstance(parsed[k], list):
                    parsed = parsed[k]
                    break
            else:
                # Try to get the first list value
                lists = [v for v in parsed.values() if isinstance(v, list)]
                parsed = lists[0] if lists else []

        # Normalise each item
        results = []
        for item in parsed[:expected]:
            raw_type = item.get("type", "")
            # Fuzzy-correct close mismatches
            type_val = _normalise_type(raw_type)
            results.append({
                "type":       type_val,
                "confidence": item.get("confidence", "Medium"),
                "reasoning":  item.get("reasoning", ""),
            })

        # Pad if OpenAI returned fewer items than expected
        while len(results) < expected:
            results.append({"type": "Full-Service Restaurants", "confidence": "Low", "reasoning": "Padding"})

        return results

    except (json.JSONDecodeError, Exception) as e:
        log.error("Failed to parse OpenAI response: {} | raw={}", e, raw[:200])
        return [{"type": "Full-Service Restaurants", "confidence": "Low", "reasoning": f"Parse error: {e}"}
                for _ in range(expected)]


def _normalise_type(raw: str) -> str:
    """Map slight variants back to a canonical category."""
    raw_low = raw.lower().strip()
    mapping = {
        "full-service": "Full-Service Restaurants",
        "full service": "Full-Service Restaurants",
        "quick-service": "Quick-Service Restaurants",
        "quick service": "Quick-Service Restaurants",
        "qsr": "Quick-Service Restaurants",
        "fast food": "Quick-Service Restaurants",
        "cafe": "Cafes",
        "café": "Cafes",
        "coffee": "Cafes",
        "bakery": "Bakery",
        "patisserie": "Bakery",
        "pastry": "Bakery",
        "cloud": "Cloud-Kitchen",
        "virtual": "Cloud-Kitchen",
        "delivery": "Cloud-Kitchen",
    }
    for fragment, canonical in mapping.items():
        if fragment in raw_low:
            return canonical
    # Return as-is if already valid
    for valid in VALID_TYPES:
        if valid.lower() == raw_low:
            return valid
    log.warning("Unrecognised type '{}' — defaulting to Full-Service Restaurants", raw)
    return "Full-Service Restaurants"
