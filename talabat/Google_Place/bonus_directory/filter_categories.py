"""
filter_categories.py
-----------------------
Filters Bonus_Directory_Entities.csv down to genuine food-service
businesses (restaurants, cafes, bakeries, QSR, dessert/bar/pub) and
removes everything else -- offices, retail stores, malls, gyms,
wholesale/supply businesses, hotels, entertainment venues, etc.

DIY-sourced rows have no Category data (by design -- the DIY scraper
skips detail-page visits for non-matches to save bandwidth) and are
left untouched per user instruction ("ignore diy playwright since we
dont have category for those").
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = Path(__file__).parent / "output"

# Category must contain at least one of these to be considered food-service
INCLUDE_KEYWORDS = [
    "restaurant", "cafe", "café", "coffee", "bakery", "patisserie", "pastry",
    "cake", "dessert", "ice cream", "gelato", "juice", "bubble tea",
    "chocolate", "candy", "confectionery", "sweet", "donut", "cupcake",
    "frozen yogurt", "tea house", "tea store", "tea shop", "bar", "pub",
    "grill", "diner", "bistro", "buffet", "food court", "cafeteria",
    "kebab", "shawarma", "sandwich", "hamburger", "pizza", "fast food",
    "takeaway", "takeout", "popcorn", "creperie", "brasserie", "gastropub",
    "steak house", "biryani", "noodle", "ramen", "sushi", "hot pot",
    "falafel", "fried chicken", "chicken wings", "wok", "espresso",
    "açaí", "acai", "cookie", "waffle", "crepe", "smoothie", "pancake",
    "burrito", "taco", "curry", "momo", "dumpling", "korean barbecue",
    "barbecue", "fish and chips", "fish & chips", "hot dog", "salad",
]

# Explicit overrides -- even though these match an include keyword, they
# are wholesale/supply/retail-store businesses, not consumer food-service
EXCLUDE_OVERRIDES = [
    "restaurant supply store", "kitchen supply store", "coffee wholesaler",
    "seafood wholesaler", "meat wholesaler", "beverage distributor",
    "food manufacturing supply", "kitchen furniture store",
    "kitchen remodeler", "wholesale bakery", "coffee store",
    "restaurant depot", "food products supplier", "food manufacturer",
    "food producer", "food processing company", "manufacturer",
    "beverage supplier",
]


def is_food_service(category: str) -> bool:
    if not category or not isinstance(category, str):
        return False
    c = category.lower().strip()
    if any(ex in c for ex in EXCLUDE_OVERRIDES):
        return False
    return any(kw in c for kw in INCLUDE_KEYWORDS)


df = pd.read_csv(OUT_DIR / "Bonus_Directory_Entities.csv")
apify_mask = df["Source"] == "apify"
diy_mask = df["Source"] == "diy_playwright"

apify_df = df[apify_mask].copy()
diy_df = df[diy_mask].copy()

print(f"Apify rows before filtering : {len(apify_df)}")
apify_df["_keep"] = apify_df["Category"].apply(is_food_service)
dropped = apify_df[~apify_df["_keep"]]
kept = apify_df[apify_df["_keep"]]
print(f"Apify rows kept (food-service) : {len(kept)}")
print(f"Apify rows dropped (irrelevant): {len(dropped)}")
print()
print("Top 30 dropped categories (to sanity-check the filter):")
print(dropped["Category"].value_counts().head(30).to_string())

kept = kept.drop(columns=["_keep"])

final_df = pd.concat([kept, diy_df], ignore_index=True)

csv_path = OUT_DIR / "Bonus_Directory_Entities.csv"
json_path = OUT_DIR / "Bonus_Directory_Entities.json"
final_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
final_df.to_json(json_path, orient="records", indent=2, force_ascii=False)

# Save the dropped rows too, for review, not deleted from disk entirely
dropped_path = OUT_DIR / "Dropped_NonFoodService_Entities.csv"
dropped.drop(columns=["_keep"]).to_csv(dropped_path, index=False, encoding="utf-8-sig")

print(f"\n{'='*60}")
print(f"FINAL total (Apify food-service + all DIY): {len(final_df)}")
print(f"  Apify (food-service only) : {len(kept)}")
print(f"  DIY (untouched, no category): {len(diy_df)}")
print(f"{'='*60}")
print(f"\nSaved (overwritten) -> {csv_path}")
print(f"Saved (overwritten) -> {json_path}")
print(f"Dropped rows saved for review -> {dropped_path}")
