"""
classifier.py — Core classification engine.

Flow for each restaurant:
  1. clean_name() strips delivery-platform boilerplate
  2. pre_classify() checks known-chain lookup  →  instant result, 0 API cost
  3. Tavily search                             →  1 credit per restaurant
  4. OpenAI GPT-4o-mini (batched 5 at a time) →  ~1 API call per 5 restaurants

Caching:
  - Tavily results  →  cache/tavily_cache.json   (keyed by cleaned name)
  - Classifications →  cache/results_cache.json  (keyed by raw name)
  Both files are written after every batch so progress is never lost.
"""

from __future__ import annotations
import json
import os
import re
import time
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

from config import (
    OPENAI_MODEL, OPENAI_BATCH_SIZE, OPENAI_TEMPERATURE, OPENAI_MAX_TOKENS,
    TAVILY_MAX_RESULTS, TAVILY_SEARCH_DEPTH, TAVILY_CREDIT_LIMIT,
    clean_name, pre_classify, RESTAURANT_TYPES,
    COL_OUTLET_TYPE, COL_CHAINED_TYPE, COL_TYPE_OF_REST,
    COL_CONFIDENCE, COL_REASONING, COL_CLASSIFICATION,
)

def _load_dotenv_robust(env_path: Path = None) -> None:
    """
    Load .env from the same directory as this file (or given path).
    Handles non-standard format where keys are wrapped in quotes:
      "TAVILY_API_KEY"=abc123   →   os.environ["TAVILY_API_KEY"] = "abc123"
    Falls back to python-dotenv for standard format.
    """
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        load_dotenv()
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().strip("\"'")
            val = val.strip().strip("\"'")
            if key:
                os.environ.setdefault(key, val)

_load_dotenv_robust()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
TAVILY_CACHE_FILE  = CACHE_DIR / "tavily_cache.json"
RESULTS_CACHE_FILE = CACHE_DIR / "results_cache.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        COL_CLASSIFICATION : "Unknown",
        COL_OUTLET_TYPE    : "Unknown",
        COL_CHAINED_TYPE   : "N/A",
        COL_TYPE_OF_REST   : "Unknown",
        COL_CONFIDENCE     : "Low",
        COL_REASONING      : reason,
    }

def _known_to_result(known_tuple: tuple) -> dict:
    classification, outlet_type, chained_type, type_of_rest = known_tuple
    return {
        COL_CLASSIFICATION : classification,
        COL_OUTLET_TYPE    : outlet_type,
        COL_CHAINED_TYPE   : chained_type,
        COL_TYPE_OF_REST   : type_of_rest,
        COL_CONFIDENCE     : "High",
        COL_REASONING      : "Pre-classified from known-chain lookup (no API needed).",
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_batch_prompt(batch: list[dict]) -> str:
    """
    batch is a list of {"name": clean_name, "search_text": str}
    Returns a single prompt asking for JSON classification of all items.
    """
    type_options = "\n".join(f"  - {t}" for t in RESTAURANT_TYPES)

    items_block = ""
    for i, item in enumerate(batch, 1):
        items_block += f"\n---\nRESTAURANT {i}: {item['name']}\nSEARCH RESULTS:\n{item['search_text'][:1200]}\n"

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


# ── Main classifier ───────────────────────────────────────────────────────────

class RestaurantClassifier:

    def __init__(self):
        api_key_tavily = os.getenv("TAVILY_API_KEY", "").strip('"\'')
        api_key_openai = os.getenv("OPENAI_API_KEY", "").strip('"\'')
        if not api_key_tavily:
            raise EnvironmentError("TAVILY_API_KEY not set in .env")
        if not api_key_openai:
            raise EnvironmentError("OPENAI_API_KEY not set in .env")

        self.tavily = TavilyClient(api_key=api_key_tavily)
        self.openai = OpenAI(api_key=api_key_openai)

        self._tavily_cache  = _load_json(TAVILY_CACHE_FILE)
        self._results_cache = _load_json(RESULTS_CACHE_FILE)

        self.tavily_calls_made = 0
        self.openai_calls_made = 0
        self.total_tokens_used = 0

    # ── Tavily search ─────────────────────────────────────────────────────────
    def search(self, name: str) -> str:
        """
        Returns a short text summary of Tavily search results.
        Uses cache to avoid duplicate API calls.
        """
        key = name.lower().strip()
        if key in self._tavily_cache:
            log.debug("Tavily cache hit: %s", name)
            return self._tavily_cache[key]

        if self.tavily_calls_made >= TAVILY_CREDIT_LIMIT:
            log.warning("Tavily credit limit reached — skipping search for: %s", name)
            return ""

        query = f'"{name}" restaurant UAE chain locations branches'
        try:
            resp = self.tavily.search(
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
            log.error("Tavily search failed for '%s': %s", name, e)
            text = ""

        self._tavily_cache[key] = text
        _save_json(TAVILY_CACHE_FILE, self._tavily_cache)
        return text

    # ── OpenAI batch classify ─────────────────────────────────────────────────
    def _classify_batch(self, batch: list[dict]) -> list[dict]:
        """
        batch: [{"raw_name": str, "name": str, "search_text": str}, ...]
        Returns list of result dicts (same order as batch).
        """
        prompt = _build_batch_prompt(batch)

        try:
            resp = self.openai.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert restaurant analyst specialising in "
                            "chain vs independent restaurant classification in the UAE. "
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
            self.total_tokens_used += (usage.prompt_tokens + usage.completion_tokens)

            raw = resp.choices[0].message.content
            # Parse: might be {"results": [...]} or just [...]
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # unwrap common wrappers
                for key in ("results", "restaurants", "classifications", "data"):
                    if key in parsed:
                        parsed = parsed[key]
                        break
                else:
                    parsed = list(parsed.values())[0] if parsed else []

            results = []
            for i, item in enumerate(batch):
                cls_data = parsed[i] if i < len(parsed) else {}
                classification = cls_data.get("classification", "Unknown")
                results.append({
                    COL_CLASSIFICATION : classification,
                    COL_OUTLET_TYPE    : cls_data.get("outlet_type",
                                            "Independent" if classification == "Independent" else "Chain"),
                    COL_CHAINED_TYPE   : cls_data.get("chained_outlet_type",
                                            "N/A" if classification == "Independent" else classification),
                    COL_TYPE_OF_REST   : cls_data.get("type_of_restaurant", "Unknown"),
                    COL_CONFIDENCE     : cls_data.get("confidence", "Low"),
                    COL_REASONING      : cls_data.get("reasoning", ""),
                })
            return results

        except json.JSONDecodeError as e:
            log.error("JSON parse error from OpenAI: %s", e)
            return [_make_empty_result("OpenAI returned unparseable JSON") for _ in batch]
        except Exception as e:
            log.error("OpenAI call failed: %s", e)
            return [_make_empty_result(str(e)) for _ in batch]

    # ── Public classify method ────────────────────────────────────────────────
    def classify_all(self, raw_names: list[str]) -> list[dict]:
        """
        Classify a list of raw restaurant names.
        Returns list of result dicts in same order.
        """
        results: list[Optional[dict]] = [None] * len(raw_names)

        # --- Pass 1: known lookup + results cache ---
        api_needed_indices = []
        for i, raw in enumerate(raw_names):
            if raw in self._results_cache:
                log.info("[CACHE] %s", raw)
                results[i] = self._results_cache[raw]
                continue
            known = pre_classify(clean_name(raw))
            if known:
                log.info("[KNOWN] %s -> %s", raw, known[0])
                results[i] = _known_to_result(known)
                self._results_cache[raw] = results[i]
            else:
                api_needed_indices.append(i)

        log.info(
            "Pre-classified: %d  |  Needs API: %d",
            len(raw_names) - len(api_needed_indices),
            len(api_needed_indices),
        )

        # --- Pass 2: Tavily search for unknowns ---
        for i in api_needed_indices:
            raw  = raw_names[i]
            name = clean_name(raw)
            log.info("[TAVILY] %d/%d  %s", api_needed_indices.index(i)+1, len(api_needed_indices), name)
            search_text = self.search(name)
            # store temporarily so batch builder can use it
            raw_names_temp = getattr(self, "_temp_search", {})
            raw_names_temp[raw] = {"name": name, "search_text": search_text}
            self._temp_search = raw_names_temp
            time.sleep(0.3)   # gentle rate-limiting

        # --- Pass 3: OpenAI batched classification ---
        batch_buffer: list[dict] = []
        batch_indices: list[int] = []

        def flush_batch():
            if not batch_buffer:
                return
            log.info("[OPENAI] Classifying batch of %d ...", len(batch_buffer))
            batch_results = self._classify_batch(batch_buffer)
            for idx, res in zip(batch_indices, batch_results):
                raw = raw_names[idx]
                results[idx] = res
                self._results_cache[raw] = res
            _save_json(RESULTS_CACHE_FILE, self._results_cache)
            batch_buffer.clear()
            batch_indices.clear()

        for i in api_needed_indices:
            raw = raw_names[i]
            info = self._temp_search.get(raw, {"name": clean_name(raw), "search_text": ""})
            batch_buffer.append({"raw_name": raw, **info})
            batch_indices.append(i)
            if len(batch_buffer) >= OPENAI_BATCH_SIZE:
                flush_batch()
                time.sleep(0.5)

        flush_batch()   # remaining partial batch

        # --- Finalize ---
        log.info(
            "Done. Tavily calls: %d | OpenAI calls: %d | Tokens: %d",
            self.tavily_calls_made,
            self.openai_calls_made,
            self.total_tokens_used,
        )
        return [r if r is not None else _make_empty_result("Not processed") for r in results]
