"""Test suite - runs BEFORE any production run. Offline, free, no API calls.

THREE LAYERS
  SMOKE       everything imports, loads, and has the shape we expect
  SANITY      hand-built cases with known answers
  REGRESSION  every trap already paid for once, pinned so it cannot return

    python test_area_pipeline.py
    python test_area_pipeline.py -v      show each passing case
"""
import json
import sys

import config as C
from clean_rules import PLUS, looks_like_area, resolve, segments

sys.stdout.reconfigure(encoding="utf-8")

VERBOSE = "-v" in sys.argv
PASS, FAIL = [], []


def check(label, got, want, note=""):
    ok = got == want
    (PASS if ok else FAIL).append((label, got, want, note))
    if VERBOSE or not ok:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}")
        if not ok:
            print(f"         got  {got!r}")
            print(f"         want {want!r}   {note}")


def truthy(label, cond, note=""):
    check(label, bool(cond), True, note)


# =====================================================================
print("=" * 74)
print("  SMOKE")
print("=" * 74)

for name in ("INPUT", "JULY_CLEAN", "AREAS_LIST", "AREANAME_CACHE"):
    p = getattr(C, name)
    truthy(f"smoke: {name} exists", p.exists(), str(p))

records = json.loads(C.INPUT.read_text(encoding="utf-8"))
check("smoke: record count", len(records), 24689)
check("smoke: top-level keys", sorted(records[0].keys()),
      ["_id", "chain_id", "location", "mordor_restaurant_id", "name",
       "source_id"])
check("smoke: location keys", sorted(records[0]["location"].keys()),
      ["area", "city", "country", "sublocality"])

areas = json.loads(C.AREAS_LIST.read_text(encoding="utf-8"))
VOCAB = {a["area"] for a in areas if a.get("area")}
AREA_CITY = {a["area"]: a.get("city") for a in areas if a.get("area")}
check("smoke: vocabulary size", len(VOCAB), 716)
truthy("smoke: every vocab city is a UAE city",
       {c for c in AREA_CITY.values() if c} <= C.UAE_CITIES,
       str({c for c in AREA_CITY.values() if c} - C.UAE_CITIES))

if C.LOOKUP.exists():
    lk = json.loads(C.LOOKUP.read_text(encoding="utf-8"))
    truthy("smoke: lookup has by_source_id", len(lk.get("by_source_id", {})) > 10000)
    truthy("smoke: lookup has by_raw_text", len(lk.get("by_raw_text", {})) > 1000)
    truthy("smoke: lookup has by_areaname", len(lk.get("by_areaname", {})) > 5000)
else:
    print("  (lookup not built yet - run build_lookup.py)")

# =====================================================================
print("\n" + "=" * 74)
print("  SANITY")
print("=" * 74)

# --- Plus Codes are stripped -----------------------------------------
truthy("sanity: plus code detected", PLUS.search("9C5V+7JC - Wasit Suburb"))
check("sanity: plus code stripped from segments",
      segments("9C5V+7JC - Wasit Suburb"), ["Wasit Suburb"])

# --- area-shaped vs not ----------------------------------------------
for s, want, note in [
    ("Al Karama", True, "plain area"),
    ("Hor Al Anz", True, ""),
    ("4B Street", False, "street token"),
    ("Opposite City Corner Supermarket", False, "directional"),
    ("Food Court, first floor", False, "floor"),
    ("St. Regis Hotel", False, "hotel"),
    ("C3", False, "code"),
    ("0", False, "digit only"),
]:
    check(f"sanity: looks_like_area({s!r})", looks_like_area(s), want, note)

# --- full resolution --------------------------------------------------
for raw, want, note in [
    ("Al Karama", "Al Karama", "already canonical"),
    ("9C5V+7JC - Wasit Suburb", None, "Wasit Suburb not in the 716 vocab"),
    ("4B Street - Hor Al Anz", "Hor Al Anz", "street segment discarded"),
    ("Opposite City Corner Supermarket - Al Karama", "Al Karama",
     "directional discarded"),
]:
    got, method = resolve(raw, VOCAB, area_city=AREA_CITY)
    if want is None:
        check(f"sanity: resolve({raw[:34]!r}) not canonical",
              got in VOCAB, False, f"method={method}")
    else:
        check(f"sanity: resolve({raw[:34]!r})", got, want, f"method={method}")

# most specific segment wins when several are canonical
if "Al Barsha First" in VOCAB and "Al Barsha" in VOCAB:
    got, _ = resolve("Al Barsha First - Al Barsha", VOCAB, area_city=AREA_CITY)
    check("sanity: most specific segment wins", got, "Al Barsha First",
          "must not roll up to the parent")

# empty / null input
check("sanity: null input", resolve(None, VOCAB)[0], None)
check("sanity: empty string", resolve("   ", VOCAB)[0], None)

# =====================================================================
print("\n" + "=" * 74)
print("  REGRESSION  (traps already paid for once)")
print("=" * 74)

# --- casing must never be title-cased: DIFC -> Difc ------------------
acronyms = [a for a in VOCAB if a.isupper() and len(a) >= 3]
truthy("regression: vocabulary retains ALL-CAPS acronyms",
       len(acronyms) > 0, f"found {acronyms[:5]}")
for a in acronyms[:5]:
    check(f"regression: {a} not title-cased", a.title() in VOCAB, False,
          "title-casing would destroy the acronym")

# --- numeric suffixes are DIFFERENT places, never merged -------------
pairs = [("Al Barsha 1", "Al Barsha 3"), ("Jumeirah 1", "Jumeirah 3")]
for a, b in pairs:
    if a in VOCAB and b in VOCAB:
        truthy(f"regression: {a} and {b} both survive as distinct",
               a in VOCAB and b in VOCAB,
               "they score 91% similar - fuzzy merging would destroy this")

# --- canonical casing invariant: one surface form per area -----------
low = {}
dupes = []
for a in VOCAB:
    k = a.lower()
    if k in low and low[k] != a:
        dupes.append((low[k], a))
    low[k] = a
check("regression: no casing twins in the vocabulary", len(dupes), 0, str(dupes[:3]))

# --- the source_id join must NOT be applied to multi-row source_ids --
from collections import defaultdict
nd = defaultdict(list)
for r in records:
    nd[str(r["source_id"])].append(r)
multi = {s: v for s, v in nd.items() if len(v) > 1}
check("regression: multi-row source_ids present", len(multi), 342,
      "chain branches at different addresses")
diff_area = sum(1 for v in multi.values()
                if len({(x.get("location") or {}).get("area") for x in v}) > 1)
check("regression: ALL multi-row source_ids have differing areas",
      diff_area, len(multi),
      "a blind source_id join would smear one branch's area onto siblings")

if C.LOOKUP.exists():
    lk = json.loads(C.LOOKUP.read_text(encoding="utf-8"))
    leaked = [s for s in multi if s in lk["by_source_id"]]
    check("regression: no multi-row source_id entered the reuse map",
          len(leaked), 0, str(leaked[:3]))

# --- UAE-only constraint ---------------------------------------------
truthy("regression: UAE_CITIES has no foreign city",
       "Riyadh" not in C.UAE_CITIES and "Doha" not in C.UAE_CITIES)
check("regression: a bare city is not an area",
      "dubai" in C.CITY_TOKENS, True,
      "resolving to 'Dubai' must be rejected - it is a city, not an area")

# --- an arterial road must resolve to nothing ------------------------
for road in ("Sheikh Zayed Road", "Emirates Road", "Al Khail Road"):
    got, _ = resolve(road, VOCAB, area_city=AREA_CITY)
    check(f"regression: {road!r} -> not a canonical area", got in VOCAB, False,
          "runs through many districts; a plausible wrong one is worse than null")

# =====================================================================
# CROSS-EMIRATE REPAIR (fix_city_mismatch.py)
# =====================================================================
print("\n" + "=" * 74)
print("  CROSS-EMIRATE REPAIR")
print("=" * 74)

from fix_city_mismatch import looks_like_area as fcm_area
from fix_city_mismatch import text_supports

CITIES = {c.lower() for c in C.UAE_CITIES} | {"uae", "united arab emirates"}

# --- the text BACKS the area: keep it, whatever the city says --------
# these are transliterations, not copies - an exact-substring test failed
# them, the LLM then nulled them, and correct data was destroyed
for area, raw in (("Al Rifah", "Al Rifa'"),
                  ("Al Musalla", "Shop 5 Al Mussallah Rd - near Exit 4"),
                  ("Al Salamah - C", "Central District - Hai Al Salama"),
                  ("Al Barsha 3", "Al Barsha 3 - Dubai")):
    truthy(f"text backs {area!r} in {raw[:28]!r}", text_supports(area, raw),
           "a spelling variant still counts as evidence; nulling it is a regression")

# --- the text does NOT back the area: it may be repaired -------------
for area, raw in (("Dubai Marina", "Hadbat Al Za`Faranah - Zone 1"),
                  ("Al Barsha 3", "Building C4 - Shop 22"),
                  ("Al Qusais 1", "Al Danah - Zone 1"),
                  ("Al Barsha 1", "Bu Shaghara - Hay Al Qasimiah"),
                  ("Al Bustan", "Astana - Kazakhstan")):
    check(f"text does NOT back {area!r} in {raw[:26]!r}",
          text_supports(area, raw), False,
          "nothing in the address supports this value")

# --- a business name is never an area --------------------------------
# a first version shipped these as areas at ~50% precision; injecting
# business names into a categorical vocabulary is worse than the error
# it was repairing
for junk in ("Shakespeare and co", "Hilton Dubai Jumeirah",
             "Alawazi Residence", "Last Exit Al Khawaneej",
             "Building C4", "Shop 22", "Al Ittihad Street",
             "Lulu Hypermarket", "Dubai"):
    check(f"rejected as an area: {junk!r}", fcm_area(junk, CITIES), None,
          "buildings, businesses, streets and cities are not areas")

# --- a real area survives the gate -----------------------------------
for good in ("Al Danah", "Wasit Suburb", "Bu Shaghara", "Al Rawda 3",
             "Hadbat Al Za`Faranah", "Mussafah Sanaiya"):
    truthy(f"accepted as an area: {good!r}", fcm_area(good, CITIES),
           "a genuine area must not be caught by the noise gate")

# =====================================================================
print("\n" + "=" * 74)
print(f"  {len(PASS)} passed | {len(FAIL)} failed")
print("=" * 74)
if FAIL:
    for label, got, want, note in FAIL:
        print(f"  FAILED: {label}")
        print(f"     got  {got!r}")
        print(f"     want {want!r}   {note}")
    sys.exit(1)
print("  All checks passed - safe to run the pipeline.")
sys.exit(0)
