"""
build_final_results.py — merges Phase 1 (Tavily+LLM web search) and Phase 2
(Zomato/Deliveroo/noon structured scrape) into one final per-name table,
then fans it back out to branch level (one row per Branch_ID, matching the
Apify-side deliverable format) for the ~5,145 "Not Found" names.

Usage:
  python tav/build_final_results.py output/production/*.jsonl output/platform_scrape/enrichment_20260705_195733.csv
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import config  # noqa: E402

FINAL_DIR = config.OUTPUT_DIR / "final"


def load_phase1(patterns: list[str]) -> pd.DataFrame:
    files = sorted(f for pattern in patterns if pattern.endswith(".jsonl") for f in glob.glob(pattern))
    rows = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


def merge(p1: pd.DataFrame, p2_path: str) -> pd.DataFrame:
    p2 = pd.read_csv(p2_path, encoding="utf-8-sig")
    p2_ok = p2[p2["Scraped_OK"] == True].set_index("Matched_Talabat_Name")  # noqa: E712

    final_rows = []
    for _, row in p1.iterrows():
        name = row["Talabat_Restaurant_Name_Clean"]
        found = bool(row["Found"])
        address = row["Address"]
        phone = row["Phone"]
        website = row["Website"]
        rating = row["Rating"]
        review_count = row["Review_Count"]
        source = row["Source"]

        if name in p2_ok.index:
            p2row = p2_ok.loc[name]
            if isinstance(p2row, pd.DataFrame):
                p2row = p2row.iloc[0]
            if not found:
                found = True
                address = p2row["Address"]
                phone = p2row["Phone"]
                rating = p2row["Rating"]
                review_count = p2row["Review_Count"]
                website = p2row["URL"]
                source = f"phase2_{p2row['Platform']}"
            elif address == "Chain":
                address = p2row["Address"]
                phone = p2row["Phone"] if pd.notna(p2row["Phone"]) else phone
                rating = p2row["Rating"] if pd.notna(p2row["Rating"]) else rating
                review_count = p2row["Review_Count"] if pd.notna(p2row["Review_Count"]) else review_count
                website = p2row["URL"]
                source = f"{source}+phase2_{p2row['Platform']}"

        final_rows.append({
            "Talabat_Restaurant_Name_Clean": name,
            "Talabat_Restaurant_Name_Raw": row["Talabat_Restaurant_Name_Raw"],
            "Area_Name": row["Area_Name"],
            "Branch_IDs": row["Branch_IDs"],
            "Restaurant_IDs": row["Restaurant_IDs"],
            "Found": found,
            "Status": "Chain" if address == "Chain" else ("Matched" if found else "Not Found"),
            "Address": address if found and address != "Chain" else None,
            "Phone": phone if found else None,
            "Website": website if found else None,
            "Rating": rating if found else None,
            "Review_Count": review_count if found else None,
            "Google_Maps_URL": row["Google_Maps_URL"] if found else None,
            "Source": source if found else None,
            "Notes": row["Notes"],
        })

    return pd.DataFrame(final_rows)


def fan_out_to_branches(df: pd.DataFrame) -> pd.DataFrame:
    branch_rows = []
    for _, row in df.iterrows():
        branch_ids = row["Branch_IDs"] or []
        restaurant_ids = row["Restaurant_IDs"] or []
        for bid, rid in zip(branch_ids, restaurant_ids):
            r = row.to_dict()
            r["Branch_ID"] = bid
            r["Restaurant_ID"] = rid
            del r["Branch_IDs"]
            del r["Restaurant_IDs"]
            branch_rows.append(r)
    return pd.DataFrame(branch_rows)


def main(patterns: list[str], phase2_csv: str) -> None:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    p1 = load_phase1(patterns)
    print(f"Loaded {len(p1)} unique-name results from phase 1")

    merged = merge(p1, phase2_csv)
    branch_df = fan_out_to_branches(merged)

    name_csv = FINAL_DIR / "TavRecovery_ByName.csv"
    name_json = FINAL_DIR / "TavRecovery_ByName.json"
    merged.to_csv(name_csv, index=False, encoding="utf-8-sig")
    merged.to_json(name_json, orient="records", indent=2, force_ascii=False)

    branch_csv = FINAL_DIR / "TavRecovery_ByBranch.csv"
    branch_json = FINAL_DIR / "TavRecovery_ByBranch.json"
    branch_df.to_csv(branch_csv, index=False, encoding="utf-8-sig")
    branch_df.to_json(branch_json, orient="records", indent=2, force_ascii=False)

    status_counts = merged["Status"].value_counts()
    print(f"\n{'='*70}")
    print(f"FINAL TAV RECOVERY RESULTS — {len(merged)} unique names -> {len(branch_df)} branch rows")
    print(f"{'='*70}")
    for status, cnt in status_counts.items():
        print(f"  {status:<12} {cnt:,} names")
    print(f"\nSaved -> {name_csv}")
    print(f"Saved -> {name_json}")
    print(f"Saved -> {branch_csv}")
    print(f"Saved -> {branch_json}")
    print(f"{'='*70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase1_patterns", nargs="+", help="Phase-1 JSONL glob pattern(s), plus the phase-2 enrichment CSV as the last arg")
    args = parser.parse_args()
    *patterns, phase2_csv = args.phase1_patterns
    main(patterns, phase2_csv)
