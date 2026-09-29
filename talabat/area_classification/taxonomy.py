"""The authoritative Talabat area taxonomy, and the matcher that maps onto it.

WHY THIS REPLACED THE JULY VOCABULARY. area_list.csv is Talabat's OWN area list -
686 values, containing every one of the 635 delivery zones we crawl plus 51
more. July's 716-value vocabulary was derived by PARSING ADDRESS TEXT, and only
218 of it appears here; the other 498 are values the parser produced that
Talabat does not actually use. Validating against that was validating against a
partly-invented list.

EVERY ROW RESOLVES TO ONE OF THESE 686, OR TO NULL. There is no "new area"
category any more - a value either maps onto the taxonomy or it does not, which
removes the whole approval loop and the hallucination risk with it.

WHY NOT ASK AN LLM "IS THIS A REAL AREA". That invites invention. The right
question is "WHICH of these 686 does this text mean", a controlled-vocabulary
mapping. Measured on this exact class of task: TF-IDF character n-grams 76.5%,
direct LLM classification 42.5%. Similarity finds the candidate; it never
decides the merge on its own.

    from taxonomy import load, match
"""
import csv
import re
import unicodedata
from functools import lru_cache

ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4",
            "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
            "ninth": "9", "tenth": "10"}
ORD_RX = re.compile(r"\b(" + "|".join(ORDINALS) + r")\b", re.I)
SUFFIX_RX = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.I)
PLUS = re.compile(r"\b[23456789CFGHJMPQRVWX]{4,}\+[23456789CFGHJMPQRVWX]{2,}\b")


def load(path):
    """-> (ordered list, set) of the 686 canonical areas."""
    out, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row or not row[0].strip():
                continue
            v = row[0].strip()
            if i == 0 and v.lower() in ("areas", "area"):
                continue                      # header
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out, seen


@lru_cache(maxsize=100_000)
def key(s):
    """Collapse the dimensions that are NOT meaning: case, punctuation,
    diacritics, the 'Al ' article, ordinal words vs digits.

    'Al Barsha First' == 'al barsha 1' == 'Barsha 1'. Deliberately does NOT
    collapse the trailing NUMBER - 'Al Barsha 1' and 'Al Barsha 3' are different
    districts and merging them destroys a real distinction.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = SUFFIX_RX.sub(r"\1", s)
    s = ORD_RX.sub(lambda m: ORDINALS[m.group(1).lower()], s)
    s = re.sub(r"^al\s+", "", s)
    s = re.sub(r"\bal\s+", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def build_index(areas):
    """key -> canonical. Collisions keep the FIRST, which is the taxonomy's own
    order, so the choice is the client's list rather than ours."""
    idx = {}
    for a in areas:
        idx.setdefault(key(a), a)
    return idx


def segments(raw):
    """Address text -> candidate segments, noise stripped."""
    s = PLUS.sub(" ", str(raw or ""))
    s = re.sub(r"\b(united arab emirates|u\.?a\.?e\.?)\b", " ", s, flags=re.I)
    parts = re.split(r"\s+-\s+|،|,|\s{2,}", s)
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip(" -,،.")
        if p and len(p) > 2:
            out.append(p)
    return out


def match(raw, index, areas):
    """Map free text onto the taxonomy.

    -> (canonical_area, method) or (None, reason). Only EXACT and NORMALISED
    matches are returned; anything needing judgement is left for the LLM stage,
    which chooses from the taxonomy rather than inventing.
    """
    if not raw or not str(raw).strip():
        return None, "empty"
    raw = str(raw).strip()

    if raw in areas:
        return raw, "exact"

    k = key(raw)
    if k in index:
        return index[k], "normalised"

    # a segment of the address IS a taxonomy area - prefer the most specific,
    # approximated by the longest key so 'Al Barsha 1' beats 'Al Barsha'
    hits = []
    for seg in segments(raw):
        if seg in areas:
            hits.append((len(key(seg)), seg))
        else:
            sk = key(seg)
            if sk in index:
                hits.append((len(sk), index[sk]))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1], "segment"

    return None, "no_taxonomy_match"
