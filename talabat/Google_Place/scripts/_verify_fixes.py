"""Verifies name-match guard and URL cleaner before the full run."""
import sys
sys.path.insert(0, ".")
from scripts.fetch_places import slug_to_name, _name_matches, _clean_maps_url

TESTS = [
    ("mcdonalds",            "McDonald's",          True),
    ("pf_changs",            "P.F. Chang's",        True),
    ("shake_shack",          "Shake Shack",          True),
    ("totally_xyz_fake_99",  "99 Sushi Bar",         False),
    ("baskin_robbins",       "Baskin-Robbins",       True),
    ("nyla",                 "Nyla Dubai",           True),
    ("zaatar_w_zeit",        "Zaatar W Zeit",        True),
    ("dominos",              "Domino's Pizza",       True),
    ("kfc",                  "KFC",                  True),
    ("starbucks",            "Starbucks Coffee",     True),
]

print("Name-match guard results:")
print(f"  {'Slug':<30} {'Returned Name':<35} {'Expected':<10} {'Got':<10} Status")
print("  " + "-" * 90)
all_ok = True
for slug, returned, expected in TESTS:
    query_name = slug_to_name(slug)
    got = _name_matches(query_name, returned)
    ok  = got == expected
    if not ok:
        all_ok = False
    flag = "OK" if ok else "FAIL"
    print(f"  {slug:<30} {returned:<35} {str(expected):<10} {str(got):<10} [{flag}]")

print()

raw_url = (
    "https://maps.google.com/?cid=15045333211104653018"
    "&g_mp=Cidnb29nbGUubWFwcy5wbGFjZXMudjEuUGxhY2VzLlNlYXJjaFRleHQQAhgEIAA"
)
clean = _clean_maps_url(raw_url)
print("URL cleaning:")
print(f"  Before: {raw_url}")
print(f"  After : {clean}")
print()
print("All match tests PASSED!" if all_ok else "SOME TESTS FAILED - check above")
