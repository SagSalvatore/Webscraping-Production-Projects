import sys, os, requests, re
from pathlib import Path
from dotenv import load_dotenv
import csv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)
keys = {row["Name"]: row["Keys"] for row in csv.DictReader(open(Path(__file__).resolve().parent.parent / "keys.csv", encoding="utf-8-sig"))}
token = keys["Ramendu"]

r = requests.get("https://api.apify.com/v2/actor-runs/DNNCzQ0n54T9fkXvB/log", params={"token": token}, timeout=30)
log = re.sub(r"[^\x00-\x7f]", "", r.text)
print(log[-4000:])
