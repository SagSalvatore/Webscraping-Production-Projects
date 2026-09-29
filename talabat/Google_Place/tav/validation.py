"""
validation.py — guardrails against the LLM trusting Tavily's synthesized
"answer" over what the actual search results say. Observed failure mode on
the 150-name pilot: for obscure names, top results are dominated by an
unrelated landmark/complex that happens to share its name with the
Talabat delivery-zone label (e.g. "Zayed Sports City" the stadium vs.
"Zayed Sports City" as an area tag) -- Tavily's answer then fabricates a
connection, and the extractor reported the landmark's own switchboard
number as if it were the restaurant's.

Two independent guardrails:
  1. name_supported_by_results -- the restaurant's own significant name
     tokens must actually appear in the raw result text, not just in the
     Tavily answer, or the match is downgraded to not-found.
  2. flag_shared_contact_duplicates -- if the same phone/address ends up
     attached to 2+ DIFFERENT restaurant names within a batch, that's a
     signature of exactly this failure mode (a shared venue/mall/landmark
     line, not a real per-business contact) -- all such rows are flagged
     and downgraded.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

import pandas as pd

_STOPWORDS = {
    "restaurant", "cafe", "cafeteria", "kitchen", "house", "llc", "the",
    "and", "of", "by", "for", "co", "company", "est", "establishment",
    "food", "foods", "grill", "bakery", "sweets", "juice", "corner", "hub",
    "spot", "place", "shop", "store", "express", "fast",
}


def significant_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    sig = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return sig or tokens


_ASCENDING = "0123456789"
_DESCENDING = "9876543210"


def _has_sequential_run(digits: str, min_run: int = 5) -> bool:
    """True if `digits` contains an ascending or descending run of
    min_run+ consecutive digits anywhere (e.g. "1234567" contains the
    5-run "12345") -- the classic shape of a template/placeholder number
    like "+971 4 123 4567", seen hallucinated across 14 unrelated
    businesses in one production sample."""
    for i in range(len(digits) - min_run + 1):
        chunk = digits[i:i + min_run]
        if chunk in _ASCENDING or chunk in _DESCENDING:
            return True
    return False


def is_placeholder_phone(phone: str | None) -> bool:
    """Catches obviously-fake numbers (sequential/all-repeated digits) that
    are template placeholders, not a real per-business contact."""
    if not phone or not isinstance(phone, str):
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return True
    if _has_sequential_run(digits):
        return True
    if re.search(r"(\d)\1{5,}", digits):  # 6+ of the same digit in a row
        return True
    return False


def _required_token_hits(sig_len: int) -> int:
    """How many significant tokens must appear before we trust a match.
    Regression: "half the tokens" let a single generic shared word (e.g.
    "delights", "chai", "club", "acai", "smoothie") pass for 2-word names --
    376/679 (55%) of one production run's Zomato matches were a DIFFERENT
    business that merely shared one common word ("Fuji Delights" matched
    to "Foodies Delights", "Chai Samosa" to "Chai Wala Cafe", "The Kain
    Club" to "The Club Restaurant"). Short names now require ALL tokens;
    longer names require an absolute floor of 2, not just a proportion."""
    if sig_len <= 2:
        return sig_len
    return max(2, (sig_len + 1) // 2)


def name_supported_by_results(name: str, search_payload: dict) -> bool:
    """True only if enough of the restaurant's own name tokens actually
    appear in the raw result titles/content (NOT the synthesized answer,
    which is exactly what fabricated the false match)."""
    sig = significant_tokens(name)
    if not sig:
        return False
    combined_text = " ".join(
        f"{r.get('title') or ''} {r.get('content') or ''}"
        for r in search_payload.get("results", [])
    ).lower()
    hits = sum(1 for t in sig if t in combined_text)
    return hits >= _required_token_hits(len(sig))


_GOOGLE_MAPS_DOMAINS = ("google.com/maps", "maps.google.com", "maps.app.goo.gl", "goo.gl/maps")


def maps_url_supported_by_results(url: str | None, search_payload: dict) -> bool:
    """True only if the claimed URL is (a) actually a Google Maps domain --
    not some other aggregator's link the model mislabeled as one (caught
    a magicpin.com listing URL reported as google_maps_url) -- AND (b)
    literally appears among the raw Tavily result URLs. Observed failure
    mode: the model fabricates a plausible-looking maps.google.com/place/
    Name/@lat,lng URL with invented coordinates (caught two different
    businesses assigned the exact same made-up latitude) -- a URL that was
    never actually in evidence."""
    if not url or not isinstance(url, str):
        return False
    if not any(domain in url.lower() for domain in _GOOGLE_MAPS_DOMAINS):
        return False
    raw_urls = [r.get("url") or "" for r in search_payload.get("results", [])]
    return any(url in u or u in url for u in raw_urls if u)


def build_maps_search_url(name: str, address: str | None) -> str:
    """Google's documented, always-resolvable search deep link -- no real
    coordinates or Place ID required, so nothing to hallucinate. Used as
    the safe fallback whenever the model didn't supply (or supplied an
    unverifiable) literal Maps URL."""
    query = f"{name} {address}" if address and address != "Chain" else f"{name} UAE"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def has_independent_confirmation(name: str, search_payload: dict) -> bool:
    """True only if at least one NON-talabat.com result actually names this
    business. A talabat.com-only appearance isn't independent confirmation:
    the business is on Talabat by definition (that's where this listing came
    from), so re-finding its own Talabat page is not new information, and
    Talabat's own delivery-zone label ("in Muhaisnah, UAE") is not a real
    street address. Observed failure mode: the model reported a Talabat zone
    label as a "recovered address" and Talabat's own internal order-rating
    count as if it were the business's Google rating."""
    sig = significant_tokens(name)
    if not sig:
        return False
    required = max(1, (len(sig) + 1) // 2)
    for r in search_payload.get("results", []):
        url = (r.get("url") or "").lower()
        if "talabat.com" in url:
            continue
        text = f"{r.get('title') or ''} {r.get('content') or ''}".lower()
        hits = sum(1 for t in sig if t in text)
        if hits >= required:
            return True
    return False


def flag_shared_contact_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Downgrade any row whose Phone is shared by 2+ rows with a DIFFERENT
    restaurant name -- a real business doesn't share its exact phone number
    with unrelated businesses; a landmark/mall switchboard (or a
    hallucinated placeholder number) caught by mistake does.

    Deliberately Phone-only, NOT Address: two genuinely different real
    businesses in the same neighborhood legitimately share a vague,
    area-level address ("Al Barsha 3, Dubai, UAE") with no street/building
    specifics -- that's normal, not a sign of a bad match. A shared exact
    phone number essentially never is.
    """
    df = df.copy()
    df["Suspicious_Shared_Contact"] = False

    for col in ("Phone",):
        counts = (
            df[df["Found"] & df[col].notna()]
            .groupby(col)["Talabat_Restaurant_Name_Clean"]
            .nunique()
        )
        shared_values = set(counts[counts > 1].index)
        if shared_values:
            mask = df[col].isin(shared_values) & df["Found"]
            df.loc[mask, "Suspicious_Shared_Contact"] = True

    downgrade_mask = df["Suspicious_Shared_Contact"]
    if downgrade_mask.any():
        df.loc[downgrade_mask, "Found"] = False
        df.loc[downgrade_mask, "Notes"] = df.loc[downgrade_mask, "Notes"].fillna("") + \
            " [downgraded: phone/address shared with a different, unrelated business name]"
        for col in ("Address", "Phone", "Website", "Google_Maps_URL", "Rating", "Review_Count"):
            df.loc[downgrade_mask, col] = None
        df.loc[downgrade_mask, "Has_Google_Maps_Listing"] = False

    return df
