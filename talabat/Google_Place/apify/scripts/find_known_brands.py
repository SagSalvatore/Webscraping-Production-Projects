import csv
import re
from collections import Counter

with open("../Complete_list_apify.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

# Standard list of well-known multinational QSR / coffee / retail / bakery chains
# likely to appear in a UAE Talabat export. KFC/Domino's excluded (already done).
KNOWN_GLOBAL_BRANDS = [
    "mcdonald's", "mcdonalds",
    "starbucks",
    "subway",
    "burger king",
    "pizza hut",
    "papa john's", "papa johns",
    "hardee's", "hardees",
    "baskin robbins", "baskin-robbins",
    "dunkin",
    "costa coffee",
    "tim hortons",
    "circle k",
    "krispy kreme",
    "five guys",
    "shake shack",
    "texas chicken", "texas de brazil",
    "chili's", "chilis",
    "tgi friday", "tgi fridays",
    "applebee's", "applebees",
    "nando's", "nandos",
    "wendy's", "wendys",
    "popeyes",
    "carl's jr", "carls jr",
    "little caesars",
    "jollibee",
    "cinnabon",
    "haagen-dazs", "häagen-dazs",
    "cold stone",
    "chuck e cheese",
    "ihop",
    "denny's", "dennys",
    "outback steakhouse",
    "hard rock cafe",
    "cheesecake factory",
    "p.f. chang's", "pf changs",
    "wagamama",
    "pinkberry",
    "% arabica", "arabica",
    "gloria jean's", "gloria jeans",
    "second cup",
    "caribou coffee",
    "peet's coffee",
    "juan valdez",
    "pret a manger",
    "greggs",
    "itsu",
    "pf chang",
]

name_counter = Counter(r["restaurant_name"].strip().lower() for r in rows)

print(f"{'Brand keyword':<25} {'Matched unique names':<45} {'Total branches':>10}")
print("-" * 85)

matched_names = set()
for kw in KNOWN_GLOBAL_BRANDS:
    hits = {name: cnt for name, cnt in name_counter.items() if kw in name}
    if hits:
        for name, cnt in hits.items():
            if name not in matched_names:
                matched_names.add(name)
                print(f"{kw:<25} {name:<45} {cnt:>10}")

print()
print(f"Total unique matched brand-names : {len(matched_names)}")
print(f"Total Talabat branches covered    : {sum(name_counter[n] for n in matched_names)}")
