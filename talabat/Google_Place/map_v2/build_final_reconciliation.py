"""
build_final_reconciliation.py — the complete, final reconciliation of the
whole project: apify/Complete_list_apify.csv (15,768 original Talabat
branches) against BOTH recovery pipelines run against it:

  1. map/output/Master_Talabat_Google_Mapping.csv -- the Apify/Google Maps
     pipeline's results (Matched / Not Found / Not Yet Searched per branch).
  2. tav/output/final/TavRecovery_ByBranch.csv -- the Tavily+OpenAI web
     search / Zomato-Deliveroo-noon recovery pipeline's results, run ONLY
     against the branches Apify's own pipeline left "Not Found".

For every branch that was "Not Found" by Apify, its final status now comes
from the Tavily recovery outcome (Matched / Chain / still Not Found). KFC
and Domino's (19 branches) were always handled separately via their own
dedicated brand scrape (apify/output/KFC_UAE.csv,
apify/brands/dominos/output/DOMINOS_UAE.csv) and are called out as their
own category here, not silently dropped.

Output: map_v2/output/
  Final_Master_Mapping.csv/json      -- every original branch, one row each
                                         (multiple rows for confirmed chains)
  Final_Matched_Branches.csv/json    -- Status == Matched
  Final_Chain_Branches.csv/json      -- Status == Chain (confirmed brand,
                                         specific address not determinable)
  Final_NotFound_Branches.csv/json   -- Status == Not Found (exhausted both
                                         pipelines)
  Final_NotSearched_Branches.csv/json-- Status == Not Searched (never
                                         attempted by either pipeline)
  New_Discovered_Listings.csv/json   -- copied as-is from map/output (bonus
                                         entities Apify found that aren't in
                                         the original Talabat list; the Tavily
                                         phase was targeted per-name lookups,
                                         not open-ended discovery, so it adds
                                         nothing new to this bucket)
"""
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
MAP_OUT = ROOT / "map" / "output"
TAV_FINAL = ROOT / "tav" / "output" / "final"
APIFY_CSV = ROOT / "apify" / "Complete_list_apify.csv"
OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

KFC_DOMINOS_KEYWORDS = ("kfc", "domino")


def is_kfc_or_dominos(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in KFC_DOMINOS_KEYWORDS)


# -- Load everything --------------------------------------------------------
talabat = pd.read_csv(APIFY_CSV, encoding="utf-8-sig")
print(f"Original Talabat branches (source of truth): {len(talabat):,}")

master = pd.read_csv(MAP_OUT / "Master_Talabat_Google_Mapping.csv", encoding="utf-8-sig")
tav = pd.read_csv(TAV_FINAL / "TavRecovery_ByBranch.csv", encoding="utf-8-sig")

print(f"Apify master mapping rows: {len(master):,} ({master['Branch_ID'].nunique():,} unique branches)")
print(f"Tavily recovery rows: {len(tav):,} ({tav['Branch_ID'].nunique():,} unique branches)")

kfc_dominos_ids = set(talabat[talabat["restaurant_name"].apply(is_kfc_or_dominos)]["branch_id"])
print(f"KFC/Domino's branches (handled separately, own dedicated scrape): {len(kfc_dominos_ids):,}")

tav_by_branch = {row["Branch_ID"]: row for _, row in tav.iterrows()}

# -- Build the final reconciled rows ----------------------------------------
final_rows = []
matched_apify = matched_tav = chain_count = notfound_count = notsearched_count = 0

for _, row in master.iterrows():
    branch_id = row["Branch_ID"]
    status = row["Status"]

    if status == "Matched":
        matched_apify += 1
        final_rows.append({
            "Branch_ID": branch_id,
            "Restaurant_ID": row["Restaurant_ID"],
            "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
            "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
            "Area_Name": row["Area_Name"],
            "Final_Status": "Matched",
            "Data_Source": "Apify (Google Maps)",
            "Brand_Type": row["Brand_Type"],
            "Address": row["Address"],
            "Phone": row["Phone"],
            "Website": row["Website"],
            "Google_Maps_URL": row["Google_Maps_URL"],
            "Rating": row["Rating"],
            "Review_Count": row["Review_Count"],
            "Notes": None,
        })

    elif status == "Not Yet Searched":
        notsearched_count += 1
        final_rows.append({
            "Branch_ID": branch_id,
            "Restaurant_ID": row["Restaurant_ID"],
            "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
            "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
            "Area_Name": row["Area_Name"],
            "Final_Status": "Not Searched",
            "Data_Source": None,
            "Brand_Type": None,
            "Address": None, "Phone": None, "Website": None,
            "Google_Maps_URL": None, "Rating": None, "Review_Count": None,
            "Notes": "Never attempted by either the Apify or Tavily recovery pipeline.",
        })

    elif status == "Not Found":
        tav_row = tav_by_branch.get(branch_id)
        if tav_row is None:
            # Shouldn't happen (every Not-Found branch was fed into the tav
            # pipeline), but fall back safely if it does.
            notfound_count += 1
            final_rows.append({
                "Branch_ID": branch_id,
                "Restaurant_ID": row["Restaurant_ID"],
                "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
                "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
                "Area_Name": row["Area_Name"],
                "Final_Status": "Not Found",
                "Data_Source": None,
                "Brand_Type": None,
                "Address": None, "Phone": None, "Website": None,
                "Google_Maps_URL": None, "Rating": None, "Review_Count": None,
                "Notes": "Not found by Apify; missing from Tavily recovery output (unexpected).",
            })
            continue

        tav_status = tav_row["Status"]
        if tav_status == "Matched":
            matched_tav += 1
            final_rows.append({
                "Branch_ID": branch_id,
                "Restaurant_ID": row["Restaurant_ID"],
                "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
                "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
                "Area_Name": row["Area_Name"],
                "Final_Status": "Matched",
                "Data_Source": f"Tavily Recovery ({tav_row['Source']})",
                "Brand_Type": None,
                "Address": tav_row["Address"],
                "Phone": tav_row["Phone"],
                "Website": tav_row["Website"],
                "Google_Maps_URL": tav_row["Google_Maps_URL"],
                "Rating": tav_row["Rating"],
                "Review_Count": tav_row["Review_Count"],
                "Notes": tav_row["Notes"],
            })
        elif tav_status == "Chain":
            chain_count += 1
            final_rows.append({
                "Branch_ID": branch_id,
                "Restaurant_ID": row["Restaurant_ID"],
                "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
                "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
                "Area_Name": row["Area_Name"],
                "Final_Status": "Chain",
                "Data_Source": f"Tavily Recovery ({tav_row['Source']})",
                "Brand_Type": "Chain (Multiple Locations, Specific Branch Unconfirmed)",
                "Address": None, "Phone": None, "Website": None,
                "Google_Maps_URL": None, "Rating": None, "Review_Count": None,
                "Notes": tav_row["Notes"],
            })
        else:
            notfound_count += 1
            final_rows.append({
                "Branch_ID": branch_id,
                "Restaurant_ID": row["Restaurant_ID"],
                "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
                "Talabat_Restaurant_Name_Clean": row["Talabat_Restaurant_Name_Clean"],
                "Area_Name": row["Area_Name"],
                "Final_Status": "Not Found",
                "Data_Source": None,
                "Brand_Type": None,
                "Address": None, "Phone": None, "Website": None,
                "Google_Maps_URL": None, "Rating": None, "Review_Count": None,
                "Notes": tav_row["Notes"],
            })

final_df = pd.DataFrame(final_rows)

# -- Save the full reconciled mapping ----------------------------------------
final_df.to_csv(OUT_DIR / "Final_Master_Mapping.csv", index=False, encoding="utf-8-sig")
final_df.to_json(OUT_DIR / "Final_Master_Mapping.json", orient="records", indent=2, force_ascii=False)

# -- Split into the requested deliverable buckets ----------------------------
matched_df = final_df[final_df["Final_Status"] == "Matched"]
chain_df = final_df[final_df["Final_Status"] == "Chain"]
notfound_df = final_df[final_df["Final_Status"] == "Not Found"]
notsearched_df = final_df[final_df["Final_Status"] == "Not Searched"]

for name, df in [
    ("Final_Matched_Branches", matched_df),
    ("Final_Chain_Branches", chain_df),
    ("Final_NotFound_Branches", notfound_df),
    ("Final_NotSearched_Branches", notsearched_df),
]:
    df.to_csv(OUT_DIR / f"{name}.csv", index=False, encoding="utf-8-sig")
    df.to_json(OUT_DIR / f"{name}.json", orient="records", indent=2, force_ascii=False)

# -- Carry over the bonus/new-discovered-listings file as-is -----------------
bonus_df = pd.read_csv(MAP_OUT / "New_Discovered_Listings.csv", encoding="utf-8-sig")
bonus_df.to_csv(OUT_DIR / "New_Discovered_Listings.csv", index=False, encoding="utf-8-sig")
bonus_df.to_json(OUT_DIR / "New_Discovered_Listings.json", orient="records", indent=2, force_ascii=False)

# -- Summary ------------------------------------------------------------------
unique_matched_branches = matched_df["Branch_ID"].nunique()
total_accounted = unique_matched_branches + len(chain_df) + len(notfound_df) + len(notsearched_df) + len(kfc_dominos_ids)

print(f"\n{'='*72}")
print("FINAL PROJECT RECONCILIATION — Complete_list_apify.csv (15,768 branches)")
print(f"{'='*72}")
print(f"MATCHED (real address/phone/website found)   : {unique_matched_branches:,} branches")
print(f"   via Apify/Google Maps pipeline             : {matched_apify:,}")
print(f"   via Tavily web/Zomato recovery pipeline    : {matched_tav:,}")
print(f"CHAIN (confirmed brand, specific branch       : {chain_count:,} branches")
print(f"   address not determinable)")
print(f"NOT FOUND (exhausted both pipelines)           : {notfound_count:,} branches")
print(f"NOT SEARCHED (never attempted by either)       : {notsearched_count:,} branches")
print(f"HANDLED SEPARATELY (KFC/Domino's, own scrape)  : {len(kfc_dominos_ids):,} branches")
print(f"{'-'*72}")
print(f"TOTAL ACCOUNTED FOR                            : {total_accounted:,} / {len(talabat):,}")
print(f"{'='*72}")
print(f"\nBONUS: new listings discovered (not in original Talabat list): {len(bonus_df):,} rows")
print(f"{'='*72}")
print(f"\nSaved -> {OUT_DIR / 'Final_Master_Mapping.csv'}")
print(f"Saved -> {OUT_DIR / 'Final_Matched_Branches.csv'} ({len(matched_df):,} rows, {unique_matched_branches:,} unique branches)")
print(f"Saved -> {OUT_DIR / 'Final_Chain_Branches.csv'} ({len(chain_df):,} rows)")
print(f"Saved -> {OUT_DIR / 'Final_NotFound_Branches.csv'} ({len(notfound_df):,} rows)")
print(f"Saved -> {OUT_DIR / 'Final_NotSearched_Branches.csv'} ({len(notsearched_df):,} rows)")
print(f"Saved -> {OUT_DIR / 'New_Discovered_Listings.csv'} ({len(bonus_df):,} rows)")
