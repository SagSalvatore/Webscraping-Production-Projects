import sys, os, requests, json, re
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)
token = os.getenv("APIFY_API_TOKEN")

r = requests.get(
    "https://api.apify.com/v2/acts/compass~crawler-google-places/builds/default",
    params={"token": token}, timeout=30
)
data = r.json()["data"]
schema = json.loads(data["inputSchema"])
props = schema["properties"]

def clean(s):
    return re.sub(r"[^\x00-\x7f]", "", s or "")

print("All top-level input fields:")
for k, v in props.items():
    title = clean(v.get("title", ""))[:70]
    print(f"  {k:<30} type={str(v.get('type')):<10} title={title}")

Path("input_schema_full.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
print("\nFull schema saved to input_schema_full.json")
