"""
patch_taxonomy.py
Applies all taxonomy fixes to final_std_terms_taxonomy.csv:
  1. Fix wrong taxonomy column assignments (steak→core_food, hot beverage→beverage, etc.)
  2. Remove exact duplicates (addon x3, combo x2, wrap x2, lassi x2)
  3. Rename bad catch-all terms to broader names
  4. Remove non-food / ingredient-only entries (gum, sugar, seasonings, garnishes)
  5. Add 25 missing UAE/South Asian/Middle Eastern terms

Saves as final_std_terms_taxonomy.csv (original backed up as _taxonomy_backup.csv)
"""

from pathlib import Path
import shutil
import polars as pl

SRC = Path(__file__).parent / "final_std_terms_taxonomy.csv"
BAK = Path(__file__).parent / "final_std_terms_taxonomy_backup.csv"

# ── 1. Load ─────────────────────────────────────────────────────────────────
shutil.copy(SRC, BAK)
print(f"Backup saved → {BAK.name}")

df = pl.read_csv(SRC)
before_count = len(df)
print(f"Before: {before_count} terms")

# ── 2. Fix wrong taxonomy assignments ───────────────────────────────────────
taxonomy_fixes = {
    "steak":                    "core_food",    # was 'beverage' ← clear error
    "hot beverage":             "beverage",     # was 'core_food'
    "steamed":                  "core_food",    # was 'beverage'
    "non-alcoholic beer":       "beverage",     # was 'core_food'
    "cocktail":                 "beverage",     # was 'core_food'
    "beer":                     "beverage",     # was 'core_food'
    "beverages":                "beverage",     # was 'core_food'
    "milk sweet":               "dessert",      # was 'beverage'
    "steamed vegetable sides":  "side",         # was 'beverage'
    "pasta in alfredo sauce":   "core_food",    # was 'addon'
    "flavored milk":            "beverage",     # was 'addon'
    "functional beverage":      "beverage",     # was 'core_food'
    "american pancake breakfast": "breakfast",  # was 'dessert'
    "pudding":                  "dessert",      # was 'accompaniment'
    "rasgulla":                 "dessert",      # was 'core_food'
    "rasgulla":                 "dessert",
    "smoothie":                 "beverage",     # was 'core_food' (line 69 shows beverage)
    "lassi":                    "beverage",     # standalone
    "milkshake":                "beverage",
    "hot sides":                "side",         # was 'core_food'
    "rice dish":                "core_food",    # was 'side'
    "rice cake":                "core_food",    # was 'side'
    "sides and snacks":         "side",
    "indian snack":             "side",
    "savory snack":             "side",
}

df = df.with_columns(
    pl.struct(["Std_terms", "taxonomy"]).map_elements(
        lambda r: taxonomy_fixes.get(r["Std_terms"], r["taxonomy"]),
        return_dtype=pl.Utf8,
    ).alias("taxonomy")
)

# ── 3. Rename bad catch-all terms ────────────────────────────────────────────
rename_map = {
    "kerala vattichathu curry":       "kerala curry",
    "filipino mung bean shrimp curry": "filipino curry",
    "makhaniya biscuit":              "indian biscuits and snacks",
    "bean starters":                  "bean dish",
    "lassi/ butter milk":             "lassi",            # consolidate with lassi
    "south indian/sri lankan":        "south indian dish",
    "retail & misc":                  "marketing/non-standard menu",
    "spicy dry":                      "dry fry",          # same meaning, pick one
    "Indo-Chinese":                   "indo-chinese",     # normalise case
    "meal/juice":                     "meal + juice",     # consistency
}

df = df.with_columns(
    pl.col("Std_terms").replace(rename_map).alias("Std_terms")
)

# ── 4. Remove entries ─────────────────────────────────────────────────────────
REMOVE = {
    "gum",           # chewing gum, not a menu item
    "sugar",         # ingredient only
    "seasonings",    # ingredient only
    "garnishes",     # ingredient only
    "stocks and bases",  # ingredient / kitchen prep
    "dry fry",       # keep 'spicy dry' renamed to 'dry fry', but remove the ORIGINAL dry fry to avoid dup after rename
    "lassi/ butter milk",   # already renamed above; drop the raw row if still present
}

df = df.filter(~pl.col("Std_terms").is_in(list(REMOVE)))

# ── 5. Remove duplicates (keep first occurrence) ──────────────────────────────
df = df.unique(subset=["Std_terms"], keep="first", maintain_order=True)

# ── 6. Add missing UAE / South Asian / Middle Eastern terms ──────────────────
NEW_TERMS = [
    # Gulf / Arabic essentials
    ("biryani",         "core_food"),   # 863 UAE restaurants serve biryani; not in taxonomy!
    ("mandi",           "core_food"),   # Gulf slow-cooked rice+meat
    ("machboos",        "core_food"),   # UAE national dish (spiced rice+meat)
    ("harees",          "core_food"),   # Gulf wheat-meat porridge (≠ hareeseh which is Lebanese)
    ("thareed",         "core_food"),   # Emirati bread stew
    ("manakish",        "core_food"),   # Lebanese flatbread (manoushe also kept)
    ("kibbeh",          "core_food"),   # Levantine bulgur+meat
    ("tabbouleh",       "core_food"),   # Lebanese parsley salad
    ("fattoush",        "core_food"),   # Lebanese bread salad
    ("mujadara",        "core_food"),   # Lentil+rice Levantine
    ("mloukhieh",       "core_food"),   # Jew's mallow stew (Egypt/Levant)
    ("mansaf",          "core_food"),   # Jordanian lamb+rice+jameed
    ("freekeh",         "core_food"),   # Roasted green wheat dish
    # South Asian essentials missing
    ("fried rice",      "core_food"),   # Huge category, currently wrongly mapped
    ("naan",            "core_food"),   # South Asian leavened bread
    ("paratha",         "core_food"),   # South Asian layered flatbread
    ("roti",            "core_food"),   # Generic South Asian flatbread
    ("idli",            "core_food"),   # South Indian steamed rice cake
    ("upma",            "core_food"),   # South Indian semolina dish
    ("congee",          "core_food"),   # Rice porridge (Asian)
    # Asian essentials
    ("sushi",           "core_food"),   # Japanese; only sashimi+tempura in taxonomy
    ("ramen",           "core_food"),   # Japanese noodle soup
    ("udon",            "core_food"),   # Japanese wheat noodles
    ("pad thai",        "core_food"),   # Thai stir-fry noodles
    ("pasta",           "core_food"),   # Generic; only pasta variants existed
    # Generic catch-all fix
    ("ghee rice",       "core_food"),   # South Indian ghee-cooked rice
    ("chicken rice",    "core_food"),   # Hainanese-style; very common in UAE
    ("gozleme",         "core_food"),   # Turkish flatbread common in Dubai
]

# Only add if not already present
existing = set(df["Std_terms"].to_list())
truly_new = [(t, tx) for t, tx in NEW_TERMS if t not in existing]
print(f"\nNew terms to add: {len(truly_new)}")
for t, tx in truly_new:
    print(f"  + {t:35s}  [{tx}]")

new_df = pl.DataFrame({"Std_terms": [t for t, _ in truly_new],
                        "taxonomy":  [tx for _, tx in truly_new]})
df = pl.concat([df, new_df])

# ── 7. Save ──────────────────────────────────────────────────────────────────
df.write_csv(SRC)
after_count = len(df)

print(f"\nAfter:  {after_count} terms  (was {before_count})")
print(f"Net change: {after_count - before_count:+d}")
print(f"\nSaved → {SRC.name}")

# ── 8. Summary of all changes ────────────────────────────────────────────────
print("\n" + "="*60)
print("TAXONOMY FIXES APPLIED")
print("="*60)
print(f"  Taxonomy column corrections : {len(taxonomy_fixes)} terms fixed")
print(f"  Terms renamed (catch-alls)  : {len(rename_map)} renames")
print(f"  Terms removed               : {len(REMOVE)} removed")
print(f"  Duplicates removed          : {before_count - len(REMOVE) - after_count + len(truly_new)} removed")
print(f"  New terms added             : {len(truly_new)} added")
print(f"\nBreakdown by taxonomy:")
for row in df.group_by("taxonomy").agg(pl.len().alias("count")).sort("count", descending=True).to_dicts():
    print(f"  {row['taxonomy']:35s}  {row['count']:4d}")
