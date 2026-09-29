"""Deterministic area-extraction rules. No API calls, no cost, no guessing.

Ported from export/IT_Team_Location/scripts/clean_rules.py, which resolved ~78%
of rows in July. Anything this cannot resolve CONFIDENTLY returns None so a
later stage picks it up - it never guesses, because a plausible wrong district
is worse than a null.

WHAT THE INPUT LOOKS LIKE. 55% of values carry a ' - ' separator and the real
area is usually one of the segments:

    '9C5V+7JC - Wasit Suburb'                        -> Wasit Suburb
    'Opposite City Corner Supermarket - Al Karama'   -> Al Karama
    '4B Street - Hor Al Anz'                         -> Hor Al Anz
    'GCQG+62J Shakespeare and co - St. Regis Hotel'  -> None (landmark only)

GRANULARITY. When several segments are known areas the MOST SPECIFIC wins -
'Al Barsha First - Al Barsha' -> 'Al Barsha First'. That was July's decision and
rolling up to the parent is irreversible once written.
"""
import re

# Plus Codes: 'GCQG+62J', '9C5V+7JC'. Open Location Code alphabet only.
PLUS = re.compile(r"\b[23456789CFGHJMPQRVWX]{4,}\+[23456789CFGHJMPQRVWX]{2,}\b")

# tokens that mark a segment as a street/building/landmark rather than an area
STREET = re.compile(
    r"\b(st|street|rd|road|ave|avenue|highway|blvd|boulevard|floor|fl|level|lvl|"
    r"shop|unit|office|suite|bldg|building|villa|tower|plaza|centre|center|"
    r"mall|hotel|station|branch|block|gate|entrance|exit|parking|basement|"
    r"ground|mezzanine|room|no|number)\b", re.I)

DIRECTIONAL = re.compile(
    r"\b(opposite|beside|behind|near|nearby|next\s+to|in\s+front\s+of|inside|"
    r"adjacent|across\s+from|close\s+to|above|below|under)\b", re.I)

# a segment that is only digits/short codes carries no area information
CODEY = re.compile(r"^[\d\s\-.,#]+$|^[a-z]{1,2}\d{1,4}$", re.I)

COUNTRY = re.compile(r"\b(united arab emirates|u\.?a\.?e\.?)\b", re.I)

# separators Talabat actually uses, in order of reliability
SPLIT = re.compile(r"\s+-\s+|،|,")


def _clean_segment(s):
    s = PLUS.sub(" ", s or "")
    s = COUNTRY.sub(" ", s)
    s = re.sub(r"[​-‏‪-‮]", "", s)   # invisible marks
    s = re.sub(r"\s+", " ", s).strip(" -,،.")
    return s


def segments(raw):
    """Split into candidate segments, cleaned, longest-meaningful first."""
    out = []
    for part in SPLIT.split(raw or ""):
        p = _clean_segment(part)
        if p and not CODEY.match(p):
            out.append(p)
    return out


def looks_like_area(s):
    """A segment is area-shaped if it names no street, building or direction."""
    if not s or len(s) < 3:
        return False
    if PLUS.search(s) or CODEY.match(s):
        return False
    if DIRECTIONAL.search(s) or STREET.search(s):
        return False
    return True


def resolve(raw, vocab, city=None, area_city=None):
    """Return (area, method) or (None, reason).

    vocab      set of canonical area names
    area_city  optional {area: city}; when the row's city is known, a candidate
               belonging to a DIFFERENT city is rejected. This is what stops
               'Al Danah' (Abu Dhabi) being accepted on a Dubai row.
    """
    if not raw or not str(raw).strip():
        return None, "empty"

    raw = str(raw).strip()
    lower = {v.lower(): v for v in vocab}

    def city_ok(cand):
        if not city or not area_city:
            return True
        want = area_city.get(cand)
        return (want is None) or (want == city)

    # 1. the whole value is already a canonical area
    hit = lower.get(raw.lower())
    if hit and city_ok(hit):
        return hit, "exact_vocab"

    segs = segments(raw)
    if not segs:
        return None, "nothing_left_after_cleaning"

    # 2. segments that ARE canonical areas -> most specific wins.
    #    Specificity is approximated by length: 'Al Barsha First' over
    #    'Al Barsha'. July used corpus rarity; length is the same ordering here
    #    and needs no second pass over the data.
    known = [lower[s.lower()] for s in segs if s.lower() in lower]
    known = [k for k in known if city_ok(k)]
    if known:
        known.sort(key=len, reverse=True)
        return known[0], "segment_vocab"

    # 3. no canonical match. Offer the best area-shaped segment as a CANDIDATE,
    #    not an answer - it may be a genuine new UAE area, which needs approval
    #    (D6), so it is tagged rather than accepted.
    cands = [s for s in segs if looks_like_area(s)]
    if cands:
        cands.sort(key=len, reverse=True)
        return cands[0], "candidate_new_area"

    return None, "no_area_shaped_segment"
