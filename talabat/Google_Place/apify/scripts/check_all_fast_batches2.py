import csv, sys, requests
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
APIFY_ROOT = Path(__file__).resolve().parent.parent
keys = {row["Name"].strip(): row["Keys"].strip() for row in csv.DictReader(open(APIFY_ROOT / "keys.csv", encoding="utf-8-sig"))}

KEY_NAMES = [
    "Anju Shokeen", "Prashant Bombe", "Amit Dwivedi", "Shekar Nittu", "Simran Dash",
    "Elakiya Chandrasekhar", "Minal Gherde", "Dania Athar Khan", "Ravi Chandra",
    "Thejas K Sabu", "Moni Maity", "Vaidehi Shripad Shukla", "Priyanadh Kanikelli",
    "Steephen Velagapalli", "Venkatesh Bestha", "Indranil Chakraborty",
    "Shuja Hafeez", "Rohan Kakde",
]

total_places = 0
total_cost = 0.0
statuses = {}
elapsed_list = []

for key_name in KEY_NAMES:
    token = keys[key_name]
    r = requests.get("https://api.apify.com/v2/actor-runs", params={"token": token, "limit": 5, "desc": "true"}, timeout=15)
    runs = r.json()["data"]["items"]
    for run_summary in runs:
        # Fetch full detail for accurate chargedEventCounts
        rid = run_summary["id"]
        r2 = requests.get(f"https://api.apify.com/v2/actor-runs/{rid}", params={"token": token}, timeout=15)
        run = r2.json()["data"]
        started = datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed > 3600:
            continue
        places = run.get("chargedEventCounts", {}).get("place-scraped", 0) or 0
        cost = run.get("usageTotalUsd", 0) or 0
        status = run["status"]
        statuses[status] = statuses.get(status, 0) + 1
        total_places += places
        total_cost += cost
        elapsed_list.append(elapsed)
        print(f"  {key_name:<25} {status:<10} elapsed={elapsed:>6.0f}s  places={places:<5} cost=${cost:.3f}")

print()
print(f"Status breakdown : {statuses}")
print(f"Total places     : {total_places}")
print(f"Total cost so far: ${total_cost:.2f}")
if elapsed_list:
    print(f"Min/Max elapsed  : {min(elapsed_list):.0f}s / {max(elapsed_list):.0f}s")
