"""
openai_extract.py — turns Tavily search results into structured business
details (address, phone, website, Google Maps link if discoverable) using
GPT-4o-mini structured outputs. Pure extraction/classification over given
text -- the model is NOT asked to search or guess beyond the provided
snippets.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger
from openai import AsyncOpenAI, RateLimitError, APIConnectionError, APITimeoutError
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402


class ExtractedBusiness(BaseModel):
    found: bool = Field(description="True only if the search results clearly refer to this exact business in the UAE, not a same-named business elsewhere or an unrelated result.")
    address: Optional[str] = Field(default=None, description="Full street address in the UAE, if stated. Literally 'Chain' if found=true but multiple distinct branch locations are evident and no single address can be confidently chosen.")
    phone: Optional[str] = Field(default=None, description="Phone number in +971 international format if determinable.")
    website: Optional[str] = Field(default=None, description="Official website or ordering page URL, if any.")
    has_google_maps_listing: bool = Field(default=False, description="True if a google.com/maps or maps.app.goo.gl URL for this business appears VERBATIM in the results.")
    google_maps_url: Optional[str] = Field(default=None, description="Copy this EXACTLY, character-for-character, from a URL that literally appears in the search results below. NEVER construct, guess, or approximate a maps.google.com/place/... URL yourself -- you do not know this business's real coordinates, and inventing a plausible-looking one is worse than leaving this blank. If no such URL literally appears in the results, leave this null even if has_google_maps_listing seems likely.")
    rating: Optional[float] = Field(default=None, description="Google rating out of 5, only if explicitly visible in a snippet.")
    review_count: Optional[int] = Field(default=None, description="Google review count, only if explicitly visible in a snippet.")
    confidence: float = Field(description="0.0-1.0 confidence this extraction is correct and matches the intended business.")
    notes: Optional[str] = Field(default=None, description="Short note on ambiguity, e.g. 'multiple branches found' or 'name matches a different city'.")


_SYSTEM_PROMPT = """You extract UAE business contact details from web search results.
You will be given a restaurant/cafe name (from a Talabat delivery listing) and a set of
web search results. Decide whether the results actually identify that same business
operating in the UAE, and if so extract its address, phone, website, and whether any
result links to a Google Maps listing.

Rules:
- Only set found=true if you are reasonably confident the results refer to the SAME
  business, not a same-named place in another country or an unrelated business.
- CRITICAL: the restaurant's own name (or a clear variant of it) must actually appear
  in the result titles/content. The "area" given (e.g. "Zayed Sports City", "Dubai
  Marina") is only a rough delivery-zone label, NOT the business itself -- some area
  labels happen to also be real landmarks (stadiums, malls, sports complexes). If the
  search results are dominated by that LANDMARK's own page/contact info rather than a
  specific food business bearing the given name, that is NOT a match: set found=false
  even if a synthesized "Tavily answer" claims otherwise. Never report a mall/stadium/
  complex's own switchboard number as if it were the restaurant's phone number.
- Never invent an address, phone, website, or Google Maps URL that isn't supported by
  the given text. This especially includes google_maps_url: never fabricate a
  maps.google.com/place/Name/@lat,lng link with coordinates you made up -- only copy
  one verbatim if it is literally present in a result URL below.
- Do not trust the "Tavily synthesized answer" on its own -- verify it against the
  individual search results below it. If the answer's claim isn't backed by at least
  one result that actually names the business, ignore the answer and set found=false.
- If results are ambiguous or show multiple different candidates for DIFFERENT
  businesses (not the same brand), set found=false and explain briefly in notes,
  UNLESS one candidate is clearly the best/exact match.
- CRITICAL -- talabat.com results are NOT independent confirmation. The business
  is already on Talabat by definition (that's where this listing came from); a
  talabat.com page showing up in results is not new information and never
  provides a real street address (only a vague delivery-zone name, sometimes not
  even the one being searched for) or a trustworthy phone/rating. If the ONLY
  results that actually name this business are talabat.com pages -- no
  independent website, review platform, or listing site -- set found=false. Do
  not report a Talabat delivery-zone phrase (e.g. "in Muhaisnah, UAE") as if it
  were a real address, and never report Talabat's own internal order-rating
  count (often "0 Ratings") as the business's rating/review_count.
- IMPORTANT: the "Talabat listed area" is only an approximate delivery-zone label,
  not a precise address requirement. Use it only to disambiguate BETWEEN multiple
  real candidate addresses (see below) -- a small wording difference alone is not
  grounds to reject an otherwise-clear single match.
- Address selection, in priority order:
    1. If only ONE independently-sourced address appears across the results for
       this business (i.e. not from talabat.com), use it -- even if its area
       doesn't exactly match the Talabat-listed area. One known address is
       always more useful than none.
    2. If the results show genuinely DIFFERENT addresses for the same brand (real
       separate branches -- e.g. one in Al Barsha, another in Deira, another in
       Motor City), the brand's existence IS confirmed -- this is a found=true
       case either way, never found=false just because you can't tell which
       branch. Check whether the Talabat-listed area matches ONE of them (even
       loosely) and use that specific address. If NONE of the candidate
       addresses' areas correspond to the Talabat-listed area at all, do NOT
       guess which branch -- but still set found=true and set address to the
       literal string "Chain" (not a guess, not null, and NOT found=false) so
       it can be manually followed up. A confirmed multi-branch brand with an
       undetermined specific address is a real, useful recovery -- do not
       discard it as not-found.
    3. Do not choose "Chain" just because you're not 100% sure an address is the
       "right" branch when there is only one candidate -- prefer reporting the
       single best-supported address in that case. Reserve "Chain" for when
       multiple distinct addresses are genuinely in evidence and none of them
       can be tied to the Talabat-listed area.
- Prefer information that appears in multiple results (higher confidence) over a
  single unclear mention.
"""


class OpenAIExtractor:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls_made = 0

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self.total_input_tokens / 1_000_000 * config.OPENAI_INPUT_COST_PER_1M
            + self.total_output_tokens / 1_000_000 * config.OPENAI_OUTPUT_COST_PER_1M
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(config.RETRY_MAX_ATTEMPTS),
        wait=wait_random_exponential(
            multiplier=config.RETRY_MIN_WAIT, max=config.RETRY_MAX_WAIT
        ),
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)),
    )
    async def extract(
        self,
        restaurant_name: str,
        area_name: str | None,
        search_payload: dict,
    ) -> ExtractedBusiness:
        results_text = "\n\n".join(
            f"[{i+1}] {r.get('title')}\nURL: {r.get('url')}\n{(r.get('content') or '')[:600]}"
            for i, r in enumerate(search_payload.get("results", []))
        ) or "(no search results returned)"

        answer = search_payload.get("answer") or "(none)"

        user_prompt = (
            f"Talabat restaurant name: {restaurant_name}\n"
            f"Talabat listed area: {area_name or 'unknown'}\n\n"
            f"Tavily synthesized answer: {answer}\n\n"
            f"Search results:\n{results_text}"
        )

        try:
            completion = await self.client.chat.completions.parse(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=ExtractedBusiness,
                temperature=0,
            )
        except (RateLimitError, APIConnectionError, APITimeoutError) as exc:
            logger.warning(f"[openai] retryable error for '{restaurant_name}': {exc}")
            raise

        usage = completion.usage
        if usage:
            self.total_input_tokens += usage.prompt_tokens
            self.total_output_tokens += usage.completion_tokens
        self.calls_made += 1

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            return ExtractedBusiness(found=False, confidence=0.0, notes="Model returned no parseable result.")
        return parsed
