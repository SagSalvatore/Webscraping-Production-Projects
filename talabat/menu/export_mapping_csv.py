import polars as pl
from pathlib import Path

parquet = Path(__file__).parent / "master_mapping.parquet"
df = pl.read_parquet(parquet)

df = df.with_columns(
    pl.col("item_key_clean").str.replace_all("_", " ").alias("item_name_readable")
)

df = df.select([
    "item_key_clean",
    "item_name_readable",
    "std_term",
    "taxonomy",
    pl.col("confidence").round(4),
    "method",
    "review_required",
])

# review_required=True first, then worst confidence first (easiest to spot errors)
df = df.sort(["review_required", "confidence", "method"], descending=[True, False, False])

out = Path(__file__).parent / "std_term_mapping_unique.csv"
df.write_csv(out)
print(f"Exported {len(df):,} rows -> {out}")
print(f"File size: {out.stat().st_size / 1024 / 1024:.1f} MB")

review = df.filter(pl.col("review_required") == True)
print(f"\nreview_required=True: {len(review):,} unique keys (worst confidence first):")
for row in review.head(15).to_dicts():
    print(f"  {row['method']:9s} | {row['item_name_readable'][:30]:30s} -> {str(row['std_term'])[:25]:25s} | {row['confidence']:.3f}")

print("\nBy method:")
for row in df.group_by("method").agg(pl.len().alias("count")).sort("count", descending=True).to_dicts():
    rev_count = len(df.filter((pl.col("method") == row["method"]) & (pl.col("review_required") == True)))
    print(f"  {row['method']:9s}  {row['count']:6,}  ({rev_count:,} need review)")
