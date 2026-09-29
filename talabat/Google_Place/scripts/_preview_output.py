"""Preview OUTPUT.csv and OUTPUT.json quality."""
import json
import pandas as pd

# ── CSV ───────────────────────────────────────────────────────────────────────
df = pd.read_csv("output/OUTPUT.csv", dtype=str).fillna("")

print("=" * 70)
print(f"CSV SUMMARY  |  Total: {len(df)}  |  Found: {len(df[df.status=='found'])}  "
      f"|  Broad: {len(df[df.status=='found_broad'])}  |  Missed: {len(df[df.status=='not_found'])}")
print("=" * 70)

print("\n--- First 5 FOUND rows ---")
found = df[df["status"].isin(["found", "found_broad"])].head(5)
for _, r in found.iterrows():
    print(f"  Name   : {r.Restaurant_Name}")
    print(f"  Address: {r.Address[:80]}")
    print(f"  Phone  : {r.Contact_No or '—'}")
    print(f"  Website: {r.Website[:60] or '—'}")
    print(f"  LatLng : {r.Geo_Lat}, {r.Geo_Lng}")
    print(f"  Map URL: {r.Google_Maps_URL}")
    print()

print("--- NOT FOUND slugs ---")
missed = df[df["status"] == "not_found"]["slug"].tolist()
for s in missed:
    print(f"  - {s}")

# ── JSON ──────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("JSON SAMPLE (entry index 0)")
print("=" * 70)

with open("output/OUTPUT.json", encoding="utf-8") as f:
    data = json.load(f)

print(json.dumps(data[0], indent=2, ensure_ascii=False))

print(f"\nJSON entries: {len(data)}")
print(f"All entries have 'Geo_Coordinates': {all('Geo_Coordinates' in e for e in data)}")
print(f"All entries have 'Google_Maps_URL': {all('Google_Maps_URL' in e for e in data)}")
