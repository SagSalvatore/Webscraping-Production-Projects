"""The one place a free-text area value is mapped onto area_list.csv.

Sagar's standing rule: ANY area name is compared and mapped against
area_list.csv FIRST; only what cannot be mapped there is treated as anything
else. Both the hand-review passes go through this module so they cannot drift
apart.

TAXONOMY-SIDE ALIASES. Several entries carry an alias inside the name:

    Tourist Club Area (Al Zahiya)     Jumeirah Village Circle - JVC
    Port Zayed (Al Mina)              Jumeirah Beach Residence - JBR

token_sort_ratio scores a short value badly against the long full name, so
'Al Zahiyah' missed 'Tourist Club Area (Al Zahiya)' entirely and fell through to
'Al Zahya' - an Ajman area, 8 Abu Dhabi rows wrong. Indexing the alias parts
makes the short form reachable.

INPUT-SIDE VARIANTS. Hand-written values carry the same shapes:

    'Al Ain (Al Jimi)'                    -> the area is Al Jimi
    'Mohammed Bin Zayed City (MBZ City)'  -> strip the alias
    'Corniche Road, Al Khubeirah'         -> the area is the last segment

so the value is tried whole, then without its parenthetical, then as the
parenthetical, then segment by segment. The most specific hit wins.

THREE GUARDS, because a bare score >= 88 produces real errors on this data:

    numbered       'Al Qusais' / 'Al Qusais 1' are different districts
    wrong emirate  'Al Zahiyah' (Abu Dhabi) / 'Al Zahya' (Ajman) score 90
    a tie          'Al Meena' scores 88.9 against BOTH 'Al Mina' and
                   'Al Muteena' - picking either is a coin flip

    from tax_map import TaxMapper
    m = TaxMapper(areas, TAX, tax_city)
    area, how = m.map("Al Ain (Al Jimi)", "Abu Dhabi")
"""
import re

from rapidfuzz import fuzz, process

from taxonomy import build_index, key, match, segments

FUZZ_MIN = 88
NUM = re.compile(r"\d")
PAREN = re.compile(r"^(.*?)\s*\((.*?)\)\s*$")


def alias_variants(a):
    """Taxonomy entry -> the surface forms someone might write for it."""
    out = {a}
    m = PAREN.match(a)
    if m:
        out |= {m.group(1), m.group(2)}
    if " - " in a:
        head, tail = a.rsplit(" - ", 1)
        out |= {head, tail}
    return {x.strip() for x in out if len(x.strip()) >= 4}


def input_variants(v):
    """Hand-written value -> the pieces worth trying, most specific first."""
    v = " ".join(str(v or "").split()).strip(" -,.\"'`")
    out = [v]
    m = PAREN.match(v)
    if m:
        # the parenthetical is usually the SPECIFIC part: 'Al Ain (Al Jimi)'
        out += [m.group(2).strip(), m.group(1).strip()]
    for seg in segments(v):
        if seg not in out:
            out.append(seg)
    for part in re.split(r"\s*,\s*", v):
        if part and part not in out:
            out.append(part)
    return [x for x in out if len(x) >= 3]


class TaxMapper:
    def __init__(self, areas, TAX, tax_city=None):
        self.areas, self.TAX = areas, TAX
        self.idx = build_index(areas)
        self.tax_city = tax_city or {}
        # canonical names first, so a real entry always beats another's alias
        self.alias_of = {a: a for a in areas}
        for a in areas:
            for var in alias_variants(a):
                self.alias_of.setdefault(var, a)
        self.pool = list(self.alias_of)

    def _one(self, v, city):
        hit, _ = match(v, self.idx, self.TAX)
        if hit:
            return hit, "exact"
        scored = process.extract(v, self.pool, scorer=fuzz.token_sort_ratio,
                                 limit=4)
        if not scored or scored[0][1] < FUZZ_MIN:
            return None, None
        s = scored[0][1]
        winners = {self.alias_of[x[0]] for x in scored if x[1] == s}
        if len(winners) > 1:
            return None, "tie"
        m = winners.pop()
        if NUM.sub("", v).strip().lower() == NUM.sub("", m).strip().lower() \
                and key(v) != key(m):
            return None, "numbered"
        seen = self.tax_city.get(m)
        if city and seen and city not in seen:
            return None, "wrong_emirate"
        return m, ("alias " if scored[0][0] != m else "") + f"fuzzy {s:.0f}"

    def map(self, value, city=""):
        """-> (canonical area, how) or (None, reason). Never guesses."""
        blocked = None
        for i, cand in enumerate(input_variants(value)):
            hit, how = self._one(cand, city)
            if hit:
                tag = how if i == 0 else f"{how} via {cand!r}"
                return hit, tag
            if how and not blocked:
                blocked = how
        return None, blocked
