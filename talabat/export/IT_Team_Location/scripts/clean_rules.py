"""Deterministic area cleaning rules.

Resolves ~78% of rows without any LLM call. Everything it cannot resolve
confidently returns None so the LLM stage picks it up - it never guesses.

Granularity: SUB-AREA is preserved (Sagar's decision). When several segments
are known areas, the MOST SPECIFIC one wins:
    'Al Barsha First - Al Barsha'  ->  'Al Barsha First'   (not 'Al Barsha')
"""
import re
from collections import Counter

SPLIT = re.compile(r"\s+-\s+|[,،]")          # incl. U+060C Arabic comma
ARABIC = re.compile(r"[؀-ۿ]")

# segments that are never an area name
NOISE = re.compile(r"""^(
      .*\b(floor|level|lvl|mezzanine)\b.*
    | (shop|unit|store|office|kiosk|counter|suite|room|villa|apt|apartment|
       stall|booth|gate|entrance)\b.*
    | (near|inside|opposite|opp|behind|beside|next\s+to|in\s+front\s+of|adjacent|
       close\s+to)\b.*
    | (drive\s*thru|drive\s*through|takeaway|delivery|food\s*court|parking)\b.*
    | (enoc|adnoc|eppco|emarat)\s*\d*.*
    | (p\.?o\.?\s*box|pobox)\b.*
    | [a-z0-9]{4}\+[a-z0-9]{2,4}.*                      # google plus-code
    | unnamed\s+road
    | .*\b(st|street|rd|road|ave|avenue|blvd|boulevard|highway|hwy)\s*\d*$
    | (building|bldg|tower|plaza|mall|centre|center|hotel|complex)\s*\d*$
    | .*\b(petrol|filling)\s+station\b.*
)$""", re.I | re.X)

# zone / sector codes - 'Rabdan - RB6' -> drop RB6
ZONE = re.compile(r"""^(
      (zone|sector|district|community|plot|block|phase)\s*\d+[a-z]?
    | [a-z]{1,4}\s?\d{1,3}(\s?\d{1,2})?[a-z]?           # RB6 E11 W44 ME10 E19 02
    | \d{1,4}[a-z]?
    | icad\s*[ivx]+
    | [ivx]{1,4}
    | district
)$""", re.I | re.X)

# country/city tokens that are never the area
STOPWORDS = {"dubai", "abu dhabi", "sharjah", "ajman", "united arab emirates",
             "uae", "ras al khaimah", "ras al-khaimah", "fujairah",
             "umm al quwain", "al ain"}


def segments(value):
    return [p.strip(" -,،") for p in SPLIT.split(value or "") if p.strip(" -,،")]


def build_vocabulary(area_values, min_doc_freq=3):
    """Data-derived list of real UAE area names.

    Document-frequency, not row-frequency: a real area recurs as a segment
    across many DISTINCT area strings, whereas a specific mall or building
    appears in only one or two. Counting each distinct string once stops a
    single high-volume value (e.g. 'Al Barsha 3' x1692) from dominating.

    Returns (vocab:set, doc_freq:Counter) - doc_freq also ranks specificity:
    a rarer name is the more specific one.
    """
    doc_freq = Counter()
    for value in set(area_values):
        if not value:
            continue
        for seg in {s.lower() for s in segments(value)}:
            doc_freq[seg] += 1
    vocab = {
        s for s, c in doc_freq.items()
        if c >= min_doc_freq
        and len(s) > 3
        and s not in STOPWORDS
        and not NOISE.match(s)
        and not ZONE.match(s)
        and not ARABIC.search(s)
    }
    return vocab, doc_freq


def clean_area(value, vocab, doc_freq):
    """-> (area or None, method).  None means 'send this to the LLM'."""
    if not value or not value.strip():
        return None, "empty"
    if ARABIC.search(value) and not re.search(r"[A-Za-z]", value):
        return None, "pure_arabic"

    parts = [p for p in segments(value) if not NOISE.match(p)]
    parts = [p for p in parts
             if not (ARABIC.search(p) and not re.search(r"[A-Za-z]", p))]
    parts = [p for p in parts if p.lower() not in STOPWORDS]
    if not parts:
        return None, "all_noise"

    while len(parts) > 1 and ZONE.match(parts[-1]):
        parts.pop()
    while len(parts) > 1 and ZONE.match(parts[0]):
        parts.pop(0)

    known = [p for p in parts if p.lower() in vocab]
    if known:
        # rarest across distinct areas == most specific -> sub-area granularity
        known.sort(key=lambda p: doc_freq[p.lower()])
        return known[0], "confident" if len(known) == 1 else "confident_specific"

    return None, "needs_llm"


def area_from_address(address, vocab, doc_freq):
    """Fallback for rows the LLM also fails on. Only a vocabulary-backed hit
    is trusted ('A'); anything else is a weak guess ('C') for human review."""
    if not address:
        return None, None
    parts = [p.strip() for p in SPLIT.split(address) if p.strip()]
    parts = [p for p in parts
             if p.lower() not in STOPWORDS
             and not NOISE.match(p) and not ZONE.match(p)]
    if not parts:
        return None, None
    known = [p for p in parts if p.lower() in vocab]
    if known:
        known.sort(key=lambda p: doc_freq[p.lower()])
        return known[0], "A"
    return parts[-1], "C"
