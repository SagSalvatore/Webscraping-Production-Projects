import os, requests, re, sys
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)
token = os.getenv("APIFY_API_TOKEN")

run_ids = ["igZb7xOKHN84LSxUJ", "WaoaH7vXq4uI971XP", "9YsI6Gz1DWA5BqnO3", "94yNM2owmvlTus0AZ", "vtwLcVHaaho2ujVSo"]

for rid in run_ids:
    r = requests.get(f"https://api.apify.com/v2/actor-runs/{rid}/log", params={"token": token}, timeout=20)
    log = re.sub(r"[^\x00-\x7f]", "", r.text)
    print(f"=== {rid[:10]} last 500 chars ===")
    print(log[-500:])
    print()
