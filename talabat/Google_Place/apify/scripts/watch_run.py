"""
watch_run.py
------------
Live-polls an Apify run's status every 10s and prints progress.
Usage: python watch_run.py <key_name> <run_id>
"""
import sys, time, csv, requests
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

key_name, run_id = sys.argv[1], sys.argv[2]
keys_path = Path(__file__).resolve().parent.parent / "keys.csv"
keys = {row["Name"]: row["Keys"] for row in csv.DictReader(open(keys_path, encoding="utf-8-sig"))}
token = keys[key_name]

print(f"Watching {key_name}'s run {run_id} ... (Ctrl+C to stop)\n")

while True:
    r = requests.get(f"https://api.apify.com/v2/actor-runs/{run_id}", params={"token": token}, timeout=15)
    run = r.json()["data"]
    started = datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00"))
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    places = run.get("chargedEventCounts", {}).get("place-scraped", 0)
    cost = run.get("usageTotalUsd", 0)
    status = run["status"]

    print(f"[{elapsed:>5.0f}s] status={status:<10} places={places:<6} cost=${cost}")

    if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        print(f"\nFinished: {status}")
        break

    time.sleep(10)
