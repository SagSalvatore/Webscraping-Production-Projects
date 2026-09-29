import json, sys, re
sys.stdout.reconfigure(encoding="utf-8")

def clean(s):
    return re.sub(r"[^\x00-\x7f]", "", s or "")

data = json.load(open("../output/retest_500_strict/UAE_Retest_500_Matched.json", encoding="utf-8"))
other = json.load(open("../output/retest_500_strict/UAE_Other_UAE_Entities.json", encoding="utf-8"))

print("=== MATCHED FILE (should contain the CORRECT matches) ===")
for row in data:
    name = row["Talabat_Restaurant_Name"].lower()
    if "bao kitchen" in name or "sayf" in name:
        print(f"  {row['Talabat_Restaurant_Name']!r:<45} -> {clean(row['Google_Business_Name'])!r:<40} status={row['Match_Status']}")

print()
print("=== OTHER ENTITIES FILE (should contain the WRONG/different businesses) ===")
for row in other:
    gname = clean(row["Google_Business_Name"]).lower()
    if "korean kitchen" in gname or "bentley kitchen" in gname or gname == "keif restaurant":
        print(f"  searched={row['Discovered_Via_Search']!r:<35} -> found={clean(row['Google_Business_Name'])!r}")
