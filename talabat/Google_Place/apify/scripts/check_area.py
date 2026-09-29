import csv
from collections import Counter, defaultdict

with open("../Complete_list_apify.csv", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

def emirate(lat, lon):
    if lat < 24.6:
        return "Abu Dhabi"
    if lat > 25.6:
        return "Northern Emirates"
    return "Dubai/Sharjah/Ajman belt"

area_stats = defaultdict(lambda: {"emirates": Counter(), "names": set(), "lat_range": [999, -999], "lon_range": [999, -999]})

for r in rows:
    aid = r["area_id"].strip()
    lat, lon = float(r["ld_lat"]), float(r["ld_lon"])
    st = area_stats[aid]
    st["emirates"][emirate(lat, lon)] += 1
    st["names"].add(r["restaurant_name"].strip().lower())
    st["lat_range"][0] = min(st["lat_range"][0], lat)
    st["lat_range"][1] = max(st["lat_range"][1], lat)
    st["lon_range"][0] = min(st["lon_range"][0], lon)
    st["lon_range"][1] = max(st["lon_range"][1], lon)

header = "area_id".ljust(8) + "count".ljust(7) + "emirates_span".ljust(45) + "lat_range".ljust(18) + "lon_range"
print(header)

for aid, st in sorted(area_stats.items(), key=lambda x: -sum(x[1]["emirates"].values())):
    count = sum(st["emirates"].values())
    em = dict(st["emirates"])
    lr = f"{st['lat_range'][0]:.3f}-{st['lat_range'][1]:.3f}"
    lonr = f"{st['lon_range'][0]:.3f}-{st['lon_range'][1]:.3f}"
    line = aid.ljust(8) + str(count).ljust(7) + str(em).ljust(45) + lr.ljust(18) + lonr
    print(line)
