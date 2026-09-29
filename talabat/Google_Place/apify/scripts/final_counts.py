import csv
import json
from collections import Counter

with open("../Complete_list_apify.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

with open("../brands/known_global_chains.json", encoding="utf-8") as f:
    allow = json.load(f)

known = set(n.lower() for n in allow["known_global_chains"])

name_counter = Counter(r["restaurant_name"].strip().lower() for r in rows)

EXCLUDE_DONE = {"kfc", "domino's pizza"}

total_branches = sum(name_counter.values())
total_names = len(name_counter)

done_branches = sum(cnt for name, cnt in name_counter.items() if name in EXCLUDE_DONE)
done_names = sum(1 for name in name_counter if name in EXCLUDE_DONE)

remaining_names = {n: c for n, c in name_counter.items() if n not in EXCLUDE_DONE}
remaining_total_names = len(remaining_names)
remaining_total_branches = sum(remaining_names.values())

known_matched = {n: c for n, c in remaining_names.items() if n in known}
known_names_count = len(known_matched)
known_branches_count = sum(known_matched.values())

rest_names = {n: c for n, c in remaining_names.items() if n not in known}
rest_names_count = len(rest_names)
rest_branches_count = sum(rest_names.values())

print(f"Total unique names (raw)       : {total_names}")
print(f"Total branches (raw)           : {total_branches}")
print()
print(f"Done (KFC + Domino's) names    : {done_names}")
print(f"Done (KFC + Domino's) branches : {done_branches}")
print()
print(f"Remaining unique names         : {remaining_total_names}")
print(f"Remaining branches             : {remaining_total_branches}")
print()
print(f"Known-chain allowlist matched names    : {known_names_count}")
print(f"Known-chain allowlist matched branches  : {known_branches_count}")
print()
print(f"Everyone-else names            : {rest_names_count}")
print(f"Everyone-else branches         : {rest_branches_count}")
print()
print(f"Check: {known_names_count} + {rest_names_count} = {known_names_count+rest_names_count} (should equal {remaining_total_names})")
print(f"Check: {known_branches_count} + {rest_branches_count} = {known_branches_count+rest_branches_count} (should equal {remaining_total_branches})")

missing = known - set(remaining_names.keys())
if missing:
    print()
    print("Allowlist entries not found in data at all (typo or already excluded):", missing)
