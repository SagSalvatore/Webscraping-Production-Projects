"""STEP 0 - verify the OpenAI API works before spending anything on a real run.

Sends 3 tiny probes:
  1. plain connectivity + model availability
  2. a real area-cleaning case
  3. an Arabic case (checks the model translates + extracts)

Prints token usage and the actual cost so the budget is visible from the start.
Run:  python talabat/export/IT_Team_Location/scripts/test_api.py
"""
import asyncio
import json
import sys
import time

from loguru import logger
from openai import AsyncOpenAI

from config import MODEL, OPENAI_KEY, LOG_DIR

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "test_api.log", level="DEBUG", encoding="utf-8")

# gpt-5-mini list price (USD per 1M tokens) - update if it changes
PRICE_IN = 0.25
PRICE_OUT = 2.00

SYSTEM = (
    "You normalise UAE location strings. Given a messy 'area' value from a "
    "restaurant listing, return ONLY the real UAE area/community/district name. "
    "Rules: drop floor/shop/unit/building/mall/street references, drop zone "
    "codes (Zone 1, RB6, E11), translate Arabic to its common English name. "
    "If a landmark is given, return the area it sits in. "
    'Respond as JSON: {"area": "<name>", "confidence": <0-1>}. '
    'If no UAE area can be determined, use {"area": null, "confidence": 0}.'
)

PROBES = [
    ("connectivity", "Rabdan - RB6"),
    ("landmark",     "Alserkal Avenue"),
    ("arabic",       "شارع الشيخ زايد - البرشاء 1"),
]


async def probe(client, label, value):
    t0 = time.time()
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": value}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.error(f"[{label}] FAILED: {type(exc).__name__}: {exc}")
        return None
    dt = time.time() - t0
    txt = r.choices[0].message.content
    u = r.usage
    cost = u.prompt_tokens / 1e6 * PRICE_IN + u.completion_tokens / 1e6 * PRICE_OUT
    logger.success(f"[{label}] {value!r}")
    logger.info(f"    -> {txt}")
    logger.info(f"    {dt:.2f}s | in={u.prompt_tokens} out={u.completion_tokens} "
                f"| ${cost:.6f}")
    return {"label": label, "input": value, "output": txt,
            "in_tok": u.prompt_tokens, "out_tok": u.completion_tokens, "cost": cost}


async def main():
    if not OPENAI_KEY:
        logger.error("No API key. Expected OPEN_AI_API in talabat/.env")
        return 1
    logger.info(f"key loaded (len={len(OPENAI_KEY)}), model={MODEL}")

    client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=60)
    results = await asyncio.gather(*(probe(client, l, v) for l, v in PROBES))
    ok = [r for r in results if r]

    print()
    if not ok:
        logger.error("ALL PROBES FAILED - do not proceed to the batch run.")
        return 1

    total = sum(r["cost"] for r in ok)
    avg_in = sum(r["in_tok"] for r in ok) / len(ok)
    avg_out = sum(r["out_tok"] for r in ok) / len(ok)
    logger.success(f"{len(ok)}/{len(PROBES)} probes succeeded")
    logger.info(f"avg tokens: in={avg_in:.0f} out={avg_out:.0f}")
    logger.info(f"cost for these {len(ok)} calls: ${total:.6f}")
    for n in (1234, 5035):
        est = (avg_in * n / 1e6 * PRICE_IN) + (avg_out * n / 1e6 * PRICE_OUT)
        logger.info(f"projected for {n:,} distinct values: ${est:.3f}")
    return 0 if len(ok) == len(PROBES) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
