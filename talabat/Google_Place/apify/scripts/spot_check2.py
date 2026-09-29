import json, sys, re
sys.stdout.reconfigure(encoding="utf-8")

def clean(s):
    return re.sub(r"[^\x00-\x7f]", "", s or "")

data = json.load(open("../output/retest_500_strict/UAE_Retest_500_Matched.json", encoding="utf-8"))
other = json.load(open("../output/retest_500_strict/UAE_Other_UAE_Entities.json", encoding="utf-8"))

checks = ["bao kitchen", "sayf wa kayf", "mumbai masti", "monk"]
print("=== MATCHED FILE ===")
for row in data:
    name = row["Talabat_Restaurant_Name"].lower()
    if any(c in name for c in checks):
        print(f"  {row['Talabat_Restaurant_Name']!r:<40} -> {clean(row['Google_Business_Name'])!r:<45} status={row['Match_Status']}")

print()
print("=== OTHER ENTITIES (should still contain the wrong ones) ===")
for row in other:
    gname = clean(row["Google_Business_Name"]).lower()
    if "korean kitchen" in gname or "bentley kitchen" in gname or gname == "keif restaurant" or "dragon bao" in gname:
        print(f"  searched={row['Discovered_Via_Search']!r:<35} -> found={clean(row['Google_Business_Name'])!r}")
