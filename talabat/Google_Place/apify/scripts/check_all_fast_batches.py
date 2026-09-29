import csv, sys, requests
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
APIFY_ROOT = Path(__file__).resolve().parent.parent
keys = {row["Name"].strip(): row["Keys"].strip() for row in csv.DictReader(open(APIFY_ROOT / "keys.csv", encoding="utf-8-sig"))}

BATCHES = [
    ("uae_fast_chain_01", "Anju Shokeen"),
    ("uae_fast_chain_02", "Prashant Bombe"),
    ("uae_fast_chain_03", "Amit Dwivedi"),
    ("uae_fast_chain_04", "Shekar Nittu"),
    ("uae_fast_chain_05", "Simran Dash"),
    ("uae_fast_chain_06", "Elakiya Chandrasekhar"),
    ("uae_fast_default_07", "Minal Gherde"),
    ("uae_fast_default_08", "Dania Athar Khan"),
    ("uae_fast_default_09", "Ravi Chandra"),
    ("uae_fast_default_10", "Thejas K Sabu"),
    ("uae_fast_default_11", "Moni Maity"),
    ("uae_fast_default_12", "Vaidehi Shripad Shukla"),
    ("uae_fast_default_13", "Priyanadh Kanikelli"),
    ("uae_fast_default_14", "Steephen Velagapalli"),
    ("uae_fast_default_15", "Venkatesh Bestha"),
    ("uae_fast_default_16", "Indranil Chakraborty"),
    ("uae_fast_default_17", "Shuja Hafeez"),
    ("uae_fast_default_18", "Rohan Kakde"),
]

total_places = 0
total_cost = 0.0
statuses = {}
max_elapsed = 0

for brand, key_name in BATCHES:
    token = keys[key_name]
    r = requests.get("https://api.apify.com/v2/actor-runs", params={
        "token": token, "limit": 10, "desc": "true",
    }, timeout=15)
    runs = r.json()["data"]["items"]
    # Filter to runs from this actor started recently
    runs = [x for x in runs if x.get("actId")][:5]

    for run in runs:
        started = datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed > 3600:  # skip old unrelated runs
            continue
        places = run.get("chargedEventCounts", {}).get("place-scraped", 0) or 0
        cost = run.get("usageTotalUsd", 0) or 0
        status = run["status"]
        statuses[status] = statuses.get(status, 0) + 1
        total_places += places
        total_cost += cost
        max_elapsed = max(max_elapsed, elapsed)

print(f"Status breakdown: {statuses}")
print(f"Total places charged so far: {total_places}")
print(f"Total cost so far: ${total_cost:.2f}")
print(f"Longest-running job elapsed: {max_elapsed:.0f}s ({max_elapsed/60:.1f} min)")
