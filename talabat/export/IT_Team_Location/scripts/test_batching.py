"""STEP 0b - cost control. One-area-per-call is dominated by the system prompt
and by reasoning tokens. Batch N areas per call so both are amortised, and
compare gpt-5-mini vs gpt-5-nano on the SAME real inputs.

Run: python talabat/export/IT_Team_Location/scripts/test_batching.py
"""
import asyncio
import json
import sys
import time

import orjson
from loguru import logger
from openai import AsyncOpenAI

from config import OPENAI_KEY, INPUT_JSON, LOG_DIR

sys.stdout.reconfigure(encoding="utf-8")      # Arabic in the console
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}")
logger.add(LOG_DIR / "test_batching.log", level="DEBUG", encoding="utf-8")

PRICE = {                      # USD per 1M tokens
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}

SYSTEM = (
    "You normalise UAE location strings. For EACH numbered input, return the "
    "real UAE area/community/district name.\n"
    "Rules:\n"
    "- drop floor/shop/unit/building/mall/street references\n"
    "- drop zone codes (Zone 1, RB6, E11, W44)\n"
    "- translate Arabic to its common English name\n"
    "- if a landmark or mall is given, return the area it sits in\n"
    "- if only a plus-code or meaningless, use null\n"
    "OUTPUT FORMAT - strict:\n"
    "- return the bare area name ONLY: no parentheses, no qualifiers, no city "
    'suffix. "Al Fahidi", never "Al Fahidi (Bur Dubai)". "Hamdan Street" in '
    'Abu Dhabi is the area "Al Markaziyah", not "Abu Dhabi (central)".\n'
    "- do NOT add or remove an 'Al ' prefix that is not in the source; if the "
    'input says "Rabdan", return "Rabdan", not "Al Rabdan"\n'
    "- if you cannot name a specific area, return null rather than a city\n"
    'Return JSON: {"results":[{"i":<index>,"area":<string|null>,'
    '"confidence":<0-1>}]}  - one entry per input, same order.'
)

# a deliberately mixed, real sample: clean, zone-coded, landmark, arabic, junk
SAMPLE = [
    "Rabdan - RB6", "Al Danah - Zone 1", "Alserkal Avenue", "Al Fahidi Street",
    "WAFI Mall - Oud Metha Rd", "Marsa Dubai - Dubai Marina", "Hamdan Street",
    "The Dubai Mall Level- 2", "شارع الشيخ زايد - البرشاء 1", "محل رقم 8 - ليوارة 1",
    "9FV3+3R7", "Unnamed Road", "Burj Khalifa - Downtown Dubai",
    "Nadd Hessa - Dubai Silicon Oasis", "Appolo Building - Al Mussallah Rd",
    "No 1 Palm Jumeirah", "City Centre Ajman Ground Level", "Hessa Street",
    "Al Barsha First - Al Barsha", "Dubai - Jumeirah Village Circle",
]


async def run(client, model, items):
    payload = "\n".join(f"{i}. {v}" for i, v in enumerate(items))
    t0 = time.time()
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": payload}],
        response_format={"type": "json_object"},
    )
    dt = time.time() - t0
    u = r.usage
    pin, pout = PRICE[model]
    cost = u.prompt_tokens / 1e6 * pin + u.completion_tokens / 1e6 * pout
    try:
        parsed = json.loads(r.choices[0].message.content)["results"]
    except Exception as exc:
        logger.error(f"{model}: could not parse response: {exc}")
        logger.error(r.choices[0].message.content[:400])
        return None
    return {"model": model, "dt": dt, "in": u.prompt_tokens, "out": u.completion_tokens,
            "cost": cost, "parsed": parsed}


async def main():
    if not OPENAI_KEY:
        logger.error("no key")
        return 1
    client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=120)

    n_distinct = 5035
    logger.info(f"batching {len(SAMPLE)} real area values per call")

    for model in ("gpt-5-mini", "gpt-5-nano"):
        res = await run(client, model, SAMPLE)
        if not res:
            continue
        per_item = res["cost"] / len(SAMPLE)
        logger.success(f"{model}: {res['dt']:.1f}s  in={res['in']} out={res['out']}  "
                       f"${res['cost']:.5f}  (${per_item:.6f}/item)")
        logger.info(f"    projected 1,234 distinct: ${per_item*1234:.3f}")
        logger.info(f"    projected 5,035 distinct: ${per_item*n_distinct:.3f}")
        print()
        for item, out in zip(SAMPLE, res["parsed"]):
            a = out.get("area")
            c = out.get("confidence")
            print(f"      {item[:44]:46} -> {str(a)[:26]:28} {c}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
