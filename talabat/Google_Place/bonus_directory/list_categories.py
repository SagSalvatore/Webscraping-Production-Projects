import pandas as pd
import sys
sys.stdout.reconfigure(encoding="utf-8")

df = pd.read_csv("output/Bonus_Directory_Entities.csv")
apify_df = df[df["Source"] == "apify"]
print("Total unique categories:", apify_df["Category"].nunique())
print("Total rows with a category:", apify_df["Category"].notna().sum())
print()
vc = apify_df["Category"].value_counts()
vc.to_csv("all_categories_list.csv", header=["count"])
print("Saved full list -> all_categories_list.csv")
print()
print("Top 60 by frequency:")
for cat, cnt in vc.head(60).items():
    print(f"  {cnt:>5}  {cat}")
