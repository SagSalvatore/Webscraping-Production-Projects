#!/usr/bin/env python3
"""
menu_classifier.py — UAE Menu Item Standard Term Classifier

Maps 355K menu items to 102 curated standard terms using GPT-4o-mini.

Tech stack:
  - openai async client
  - asyncio.Semaphore  (concurrency=60)
  - AsyncTokenBucket   (RPM=4500)
  - tenacity + jitter  (retries with exponential backoff)
  - loguru             (structured logging)
  - auto-save checkpoint every 100 batches (~5K rows)
  - auto-skip rows already in checkpoint

Output:
  output_menu_classified.xlsx  — original cols A-D + col E (new std_term)
  output_menu_classified.json  — full records as JSON array
  checkpoint.json              — resumable state {str(row_idx): term}

Run:
  python talabat/menu/classification/menu_classifier.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import openpyxl
import xlsxwriter
import polars as pl
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
    before_sleep_log,
    RetryError,
)
import logging

# ── Paths ────────────────────────────────────────────────────────────────────
HERE            = Path(__file__).parent
INPUT_XLSX      = HERE / "Complete_Menu.xlsx"
STD_TERMS_CSV   = HERE / "standard_term_table.csv"
CHECKPOINT_JSON = HERE / "checkpoint.json"
OUTPUT_XLSX     = HERE / "output_menu_classified.xlsx"
OUTPUT_JSON     = HERE / "output_menu_classified.json"
LOG_FILE        = HERE / "menu_classifier.log"

load_dotenv(HERE.parent.parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
OPENAI_MODEL  = "gpt-4o-mini"
BATCH_SIZE    = 50       # items per API call
CONCURRENCY   = 60       # max concurrent in-flight requests
RPM           = 4500     # requests per minute (Tier 3)
DESC_MAXLEN   = 120      # truncate descriptions to keep tokens down
AUTOSAVE_EVERY= 100      # save checkpoint every N completed batches

# ── Loguru setup ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")
logger.add(LOG_FILE, level="DEBUG", rotation="10 MB", encoding="utf-8",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}")


# ── Async token-bucket rate limiter ──────────────────────────────────────────
class AsyncTokenBucket:
    def __init__(self, rpm: int):
        self._rate     = rpm / 60.0
        self._capacity = max(30, rpm // 20)
        self._tokens   = float(self._capacity)
        self._last     = time.monotonic()
        self._lock     = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now  = time.monotonic()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
        await asyncio.sleep(wait)
        async with self._lock:
            self._tokens = max(0.0, self._tokens - 1.0)


# ── System prompt (built once) ────────────────────────────────────────────────
def build_system_prompt(std_terms: list[str]) -> str:
    terms_block = "\n".join(f"{i+1}. {t}" for i, t in enumerate(std_terms))
    return f"""You are a UAE restaurant menu classification expert with deep knowledge of Middle Eastern, South Asian, East Asian, Filipino, and international cuisines.

Your job: map each menu item to exactly ONE standard term from the list below.

STANDARD TERMS (use ONLY these, exact spelling):
{terms_block}

CLASSIFICATION PRIORITY — check each level in ORDER, stop at the first clear match:

LEVEL 0 — UNCONDITIONAL KEYWORD OVERRIDES (check item name first, highest priority):
  If the item name contains any of these words, assign the term IMMEDIATELY
  regardless of category or description. Weight specs (1kg, 200gm) and product
  codes (mb-8-9) in the name are IGNORED — classify by the food word only:
    "combo" anywhere in name                              -> combo
    "sushi", "maki", "sashimi", "nigiri", "temaki" in name -> sashimi
    "maguro", "hamachi", "otoro", "unagi" in name         -> sashimi
    "poke" in name                                        -> sashimi
    "falafel" in name                                     -> falafel
    "shawarma" in name                                    -> shawarma
    "wrap" or "burrito" in name                           -> wrap
    "burger" in name                                      -> burger
    "pizza" in name                                       -> pizza
    "waffle" in name                                      -> waffle
    "crepe" or "crep" in name                             -> crepe
    "tonkatsu", "katsu", "schnitzel" in name              -> grill
    "meatball" or "meatballs" in name                     -> grill
    "wagyu", "ribeye", "rib-eye", "sirloin", "tenderloin",
      "striploin", "t-bone", "brisket" in name            -> steak
    "quesadilla" or "quesadillas" in name                 -> tacos
    "safiha" or "sfeeha" in name                          -> bread and bakery
    "homos" in name (Arabic spelling of hummus)           -> hummus
    "mocktail" or "mocktails" in name                     -> beverages
    "flatbread" or "flatbreads" in name                   -> bread and bakery
    "biryani" in name                                     -> rice dish
    "mandi", "kabsa", "machboos", "ouzi" in name          -> rice dish
    "tikka" in name                                       -> tikka
    "noodle" or "ramen" or "udon" or "soba" in name       -> noodle
    "dosa" or "idli" or "vada" in name                    -> dosa
    "spread" or "paste" in name                           -> sweet spreads
    "cacao" or "cocoa" in name                            -> chocolate
    "praline" or "truffle" or "ganache" in name           -> chocolate

LEVEL 1 — Scan ITEM NAME for food keywords (even inside creative/fun names):
  Extract the dominant food word from the name even if the name is a phrase.
  Do NOT treat creative names as a single unknown unit:
    "ufo fries"                 -> hot sides  (keyword: fries)
    "small popcorn plate"       -> savory snack (keyword: popcorn)
    "mango yogurt bowl"         -> dairy and yogurt (keyword: yogurt)
    "veal meatballs with sauce" -> grill (keyword: meatballs)
    "truffle mushroom flatbread" -> bread and bakery (keyword: flatbread)
    "batman kids toy"           -> no food keyword -> go to LEVEL 2

LEVEL 2 — Category column (DECISIVE signal when name is ambiguous or foreign):
  When the item name is vague, generic, in another language, or a creative phrase,
  the restaurant category decides. Apply these DIRECT mappings:

    sushi / cooked sushi / sushi rolls / speciality maki -> sashimi
    poke                                                  -> sashimi
    wrap / wraps                                          -> wrap
    appetizer / starter / starters / mezza / mezze / cold mezza -> appetizer
    grill / grilled / grills / bbq / charcoal             -> grill
    beef / lamb / veal / mutton / meat                    -> grill
    burger / burgers                                      -> burger
    pizza / pizzas                                        -> pizza
    salad / salads                                        -> salad
    soup / soups / stew / stews                           -> soup
    egg / eggs                                            -> egg plates
    bread / breads / bakery / flatbread / flatbreads      -> bread and bakery
    noodle / noodles / ramen / udon                       -> noodle
    pasta                                                 -> pasta (tomato marinara)
    kebab / kebabs / doner / raw kababs / kabab           -> kebab
    sandwich / sandwiches                                 -> sandwich
    dessert / desserts / sweet / sweets                   -> sweets & desserts
    juice / juices                                        -> juice
    smoothie / smoothies                                  -> smoothie
    mocktail / mocktails / refreshing mocktail            -> beverages
    beverage / beverages / drink / drinks                 -> beverages
    side / sides / side dish                              -> hot sides
    rice / fried rice / biryani                           -> rice dish
    seafood / fish / fishes / prawns / shrimp             -> seafood platter
    snack / snacks                                        -> savory snack
    water / water bottle                                  -> sparkling water
    chocolate / chocolates                                -> chocolate
    dosa / idli / vada / idly / south indian              -> dosa
    crepe / crepes                                        -> crepe
    steak / steaks                                        -> steak
    wing / wings                                          -> wings
    strip / strips / tenders / tender                     -> strips
    shawarma                                              -> shawarma
    tacos / taco                                          -> tacos
    waffle / waffles                                      -> waffle
    cookie / cookies                                      -> cookie
    ice cream / gelato / sorbet                           -> ice cream
    milkshake / shake / shakes / frappe                   -> milkshake
    coffee / hot drink / hot drinks                       -> hot beverage
    breakfast / morning / brunch                          -> breakfast plate
    curry / curries / masala                              -> indian/pakistani veg curry
    hummus / homos / hummous                              -> hummus
    kunafa / knafeh                                       -> kunafa
    baklava                                               -> baklava
    dates                                                 -> dates
    nuts / nut                                            -> nuts
    fruit / fruits                                        -> fresh fruits
    sauce / sauces / dip / dips / salsa / condiment       -> sauces & condiments
    maamoul                                               -> maamoul
    mojito / mokito                                       -> mojito
    matcha                                                -> matcha
    lassi / buttermilk / laban / ayran                   -> lassi/ butter milk
    yogurt / yoghurt / raita / raitas                     -> dairy and yogurt
    swedish meatballs / meatball / meatballs              -> grill
    chicken / poultry                                     -> grill

  For "add ons" / "extras" / "add-on" / "toppings" / "add on" categories:
    Classify by PRIMARY INGREDIENT in the item name:
      "extra chicken" / "extra meat" / "extra boti" -> grill
      "extra sauce" / "extra dip"                   -> sauces & condiments
      "extra halva" / "extra mithai"                -> indian/pakistani dessert
      "extra cheese" / "mozzarella" / "mozarella"   -> hot sides
      "extra rice"                                  -> rice dish
      "extra bread" / "extra roti" / "extra naan"   -> bread and bakery

LEVEL 3 — Description column (only when BOTH level 1 AND level 2 fail):
  Analyse ingredients, preparation method, or presentation to find the food type.

COMBO RULE — "combo" = explicit MEAL DEAL only:
  YES combo: set meals, thali plates, value bundles, items explicitly named as deals
    combining 2+ distinct food categories (burger + drink + fries, family meal).
    "combo 1", "hot pot combo", "thali", "set meal", "family deal"
    "mexi-grill supreme combo" (name says combo) -> combo
  NO combo: single dishes with natural components
    "manchurian with fried rice"  -> rice dish
    "beef with garlic and pepper" -> grill
    "chicken with rice"           -> grill
    "biryani with raita"          -> rice dish

MARKETING/NON-STANDARD MENU RULE — use this term very sparingly:
  CORRECT use: promotional section headers used as menu items, loyalty points/
    rewards entries, gift vouchers, "service charge" line items, section dividers
    that appear as entries ("---- STARTERS ----"), non-food administrative entries.
  NEVER use for these — classify by food type instead:
    - Items with weight/size specs: "beef wagyu 1kg" -> steak (1kg is just size)
    - Items with product codes: "mb-8-9 wagyu ribeye" -> steak (code is irrelevant)
    - Unfamiliar ethnic food names: use category (LEVEL 2) to decide
    - Raw ingredients sold by weight: "chicken breast 500g" -> grill
    - Japanese dish names: "philadelphia maguro goma" -> sashimi (maguro = tuna)
    - Filipino dish names: "regular sweet boneless chicken (tosilog)" -> grill
    - Lebanese items: "1 kg safiha" -> bread and bakery (safiha = meat flatbread)

FALLBACK — use: Others
  ONLY when NONE of levels 0-3 give a clear food signal.
  Typical Others: flowers, toys, gift cards, merchandise, service charges,
  packaging fees, abstract brand names with no English food keyword.
  NEVER use Others if the category clearly maps via LEVEL 2.

RULES:
- Use ONLY terms from the standard-term list (exact spelling)
- Never invent new terms or combine two terms
- Be consistent: identical or near-identical items must always get the same term

OUTPUT FORMAT — return ONLY this, one line per item, no other text:
INDEX|standard_term

Example:
1|burger
2|juice
3|Others
"""


# ── API call with retry + jitter ──────────────────────────────────────────────
def make_classifier(client: AsyncOpenAI, bucket: AsyncTokenBucket,
                    sem: asyncio.Semaphore, system_prompt: str,
                    valid_terms: set[str]):

    @retry(
        retry=retry_if_exception_type(
            (RateLimitError, APITimeoutError, APIConnectionError)
        ),
        wait=wait_random_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        before_sleep=before_sleep_log(logging.getLogger("tenacity"), logging.WARNING),
        reraise=True,
    )
    async def _call(batch: list[tuple[int, str, str, str]]) -> dict[int, str]:
        """
        batch: [(row_idx, item_name, category, description), ...]
        returns: {row_idx: std_term}
        """
        await bucket.acquire()

        lines = []
        for idx, name, cat, desc in batch:
            desc_t = (desc or "")[:DESC_MAXLEN].strip()
            lines.append(f"{idx}|{name or ''}|{cat or ''}|{desc_t}")
        payload = "\n".join(lines)

        async with sem:
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": payload},
                ],
                temperature=0,
                max_tokens=600,
            )

        raw = (resp.choices[0].message.content or "").strip()
        result: dict[int, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            idx_str, _, term = line.partition("|")
            try:
                row_idx = int(idx_str.strip())
                term    = term.strip()
                # Validate against known terms (case-insensitive fallback)
                if term in valid_terms:
                    result[row_idx] = term
                elif term.lower() in {t.lower() for t in valid_terms}:
                    # fix casing
                    matched = next(t for t in valid_terms if t.lower() == term.lower())
                    result[row_idx] = matched
                else:
                    result[row_idx] = "Others"
            except ValueError:
                pass
        return result

    return _call


# ── Checkpoint helpers ────────────────────────────────────────────────────────
def load_checkpoint() -> dict[str, str]:
    if CHECKPOINT_JSON.exists():
        with open(CHECKPOINT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Checkpoint loaded: {:,} rows already done", len(data))
        return data
    return {}


def save_checkpoint(ckpt: dict[str, str]) -> None:
    tmp = CHECKPOINT_JSON.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ckpt, f, ensure_ascii=False)
    tmp.replace(CHECKPOINT_JSON)


# ── Load input data ───────────────────────────────────────────────────────────
def load_input() -> list[tuple[int, str, str, str, str]]:
    """Returns list of (row_idx, item_name, category, description, existing_std_term)
    row_idx is 0-based."""
    logger.info("Loading {} ...", INPUT_XLSX.name)
    wb = openpyxl.load_workbook(INPUT_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        name  = str(row[0]).strip() if row[0] is not None else ""
        cat   = str(row[1]).strip() if row[1] is not None else ""
        desc  = str(row[2]).strip() if row[2] is not None else ""
        exist = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
        rows.append((i, name, cat, desc, exist))
    wb.close()
    logger.info("Loaded {:,} rows", len(rows))
    return rows


# ── Write outputs ─────────────────────────────────────────────────────────────
def write_excel(rows: list[tuple], results: dict[str, str]) -> None:
    """Write fresh xlsx with original A-D preserved + column E = new_std_term.
    Uses xlsxwriter constant_memory mode for speed on 355K rows."""
    logger.info("Writing Excel -> {}", OUTPUT_XLSX.name)
    wb = xlsxwriter.Workbook(str(OUTPUT_XLSX), {"constant_memory": True})
    ws = wb.add_worksheet("Menu")
    bold = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "#FFFFFF"})

    headers = ["Item Name", "Category", "Description",
               "final_accurate_std_term", "new_std_term"]
    for col, h in enumerate(headers):
        ws.write(0, col, h, bold)

    for i, (row_idx, name, cat, desc, exist) in enumerate(rows):
        term = results.get(str(row_idx), "")
        ws.write(i + 1, 0, name)
        ws.write(i + 1, 1, cat)
        ws.write(i + 1, 2, desc)
        ws.write(i + 1, 3, exist)
        ws.write(i + 1, 4, term)

    wb.close()
    logger.success("Excel saved -> {} ({:.1f} MB)", OUTPUT_XLSX.name,
                   OUTPUT_XLSX.stat().st_size / 1e6)


def write_json(rows: list[tuple], results: dict[str, str]) -> None:
    logger.info("Writing JSON -> {}", OUTPUT_JSON.name)
    out = []
    for row_idx, name, cat, desc, exist in rows:
        out.append({
            "item_name":          name,
            "category":           cat,
            "description":        desc,
            "existing_std_term":  exist,
            "new_std_term":       results.get(str(row_idx), ""),
        })
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    logger.success("JSON saved -> {} ({:.1f} MB)", OUTPUT_JSON.name,
                   OUTPUT_JSON.stat().st_size / 1e6)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("=" * 60)
    logger.info("UAE Menu Classifier — GPT-4o-mini")
    logger.info("=" * 60)
    t_start = time.time()

    # Load standard terms
    std_df    = pl.read_csv(STD_TERMS_CSV, infer_schema_length=0)
    std_terms = [t.strip() for t in std_df["Standard Term"].to_list()]
    valid_set = set(std_terms)
    valid_set.add("Others")
    logger.info("Standard terms loaded: {} terms", len(std_terms))

    # Build prompt
    system_prompt = build_system_prompt(std_terms)

    # Load input rows
    all_rows = load_input()
    total    = len(all_rows)

    # Load checkpoint
    ckpt = load_checkpoint()
    todo = [r for r in all_rows if str(r[0]) not in ckpt]
    logger.info("To classify: {:,} / {:,} (skipping {:,} already done)",
                len(todo), total, len(ckpt))

    if not todo:
        logger.success("All rows already classified. Writing outputs...")
        write_excel(all_rows, ckpt)
        write_json(all_rows, ckpt)
        return

    # OpenAI client
    api_key = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY", "")
    client  = AsyncOpenAI(api_key=api_key)
    bucket  = AsyncTokenBucket(rpm=RPM)
    sem     = asyncio.Semaphore(CONCURRENCY)
    classify_fn = make_classifier(client, bucket, sem, system_prompt, valid_set)

    # Build batches
    batches = [
        todo[i : i + BATCH_SIZE]
        for i in range(0, len(todo), BATCH_SIZE)
    ]
    n_batches = len(batches)
    logger.info("Batches: {:,} × {} items each", n_batches, BATCH_SIZE)

    # Shared state
    lock          = asyncio.Lock()
    completed     = 0
    failed_rows   = 0
    t_last_log    = time.time()

    async def process_batch(batch_idx: int, batch: list) -> None:
        nonlocal completed, failed_rows

        items = [(r[0], r[1], r[2], r[3]) for r in batch]   # (row_idx, name, cat, desc)
        try:
            result = await classify_fn(items)
        except RetryError as e:
            logger.error("Batch {} failed after all retries: {}", batch_idx, e)
            result = {r[0]: "Others" for r in batch}
            async with lock:
                failed_rows += len(batch)
        except Exception as e:
            logger.error("Batch {} unexpected error: {}", batch_idx, e)
            result = {r[0]: "Others" for r in batch}
            async with lock:
                failed_rows += len(batch)

        # Fill in any items the API missed
        for row_idx, *_ in batch:
            if row_idx not in result:
                result[row_idx] = "Others"

        async with lock:
            for row_idx, term in result.items():
                ckpt[str(row_idx)] = term
            completed += 1

            # Progress log every 5s or every 200 batches
            now = time.time()
            if completed % 200 == 0 or (now - t_last_log) > 5:
                pct  = 100 * completed / n_batches
                done = completed * BATCH_SIZE
                eta  = (time.time() - t_start) / max(completed, 1) * (n_batches - completed)
                logger.info("Progress: {}/{} batches ({:.1f}%) | ~{:.0f}s left",
                            completed, n_batches, pct, eta)

            # Auto-save checkpoint
            if completed % AUTOSAVE_EVERY == 0:
                save_checkpoint(ckpt)
                logger.debug("Checkpoint saved at batch {}", completed)

    # Dispatch all batches concurrently (semaphore limits actual I/O)
    tasks = [
        asyncio.create_task(process_batch(i, batch))
        for i, batch in enumerate(batches)
    ]
    await asyncio.gather(*tasks)

    # Final checkpoint save
    save_checkpoint(ckpt)

    elapsed = time.time() - t_start
    classified = sum(1 for v in ckpt.values() if v and v != "Others")
    others     = sum(1 for v in ckpt.values() if v == "Others")

    logger.info("=" * 60)
    logger.info("Done in {:.1f}s", elapsed)
    logger.info("  Classified : {:,}", classified)
    logger.info("  Others     : {:,}", others)
    logger.info("  Failed     : {:,}", failed_rows)
    logger.info("=" * 60)

    # Write outputs
    write_excel(all_rows, ckpt)
    write_json(all_rows, ckpt)


if __name__ == "__main__":
    asyncio.run(main())
