#!/usr/bin/env python3
"""
post_process.py — Fix marketing/non-standard menu AND Others over-classification.

After the full classification run, two buckets contain misclassified food items:
  1. "marketing/non-standard menu" — assigned to food items the LLM didn't recognise
     (e.g. wagyu steak with a product code, Japanese dish names, ethnic food names)
  2. "Others" — assigned to items that DO have a clear food keyword in name/category
     (e.g. "ice cream red velvet", "bbq wings 4pcs", "mutton kebab karahi")

This script rescans BOTH buckets with keyword matching (no API cost) and
reclassifies items where a food signal is found.

Run AFTER menu_classifier.py completes:
    python talabat/menu/classification/post_process.py

Input:   classification/output_menu_classified.json
Output:  classification/output_menu_classified_fixed.json
         classification/output_menu_classified_fixed.xlsx
         classification/post_process_report.txt
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import polars as pl
import xlsxwriter
from loguru import logger

HERE        = Path(__file__).parent
INPUT_JSON  = HERE / "output_menu_classified.json"
OUTPUT_JSON = HERE / "output_menu_classified_fixed.json"
OUTPUT_XLSX = HERE / "output_menu_classified_fixed.xlsx"
REPORT_TXT  = HERE / "post_process_report.txt"

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")


# ── Keyword rules (item name / description) ───────────────────────────────────
# More specific entries must come BEFORE generic ones (e.g. "rib-eye" before "rib")
NAME_RULES: list[tuple[str, str]] = [
    # ── Steak cuts ──────────────────────────────────────────────────────────
    ("wagyu",            "steak"),
    ("ribeye",           "steak"),
    ("rib-eye",          "steak"),
    ("striploin",        "steak"),
    ("sirloin",          "steak"),
    ("tenderloin",       "steak"),
    ("t-bone",           "steak"),
    ("brisket",          "steak"),
    ("tomahawk",         "steak"),
    # ── Japanese / sushi ────────────────────────────────────────────────────
    ("maguro",           "sashimi"),
    ("hamachi",          "sashimi"),
    ("otoro",            "sashimi"),
    ("unagi",            "sashimi"),
    ("ebi",              "sashimi"),
    ("tobiko",           "sashimi"),
    ("nigiri",           "sashimi"),
    ("temaki",           "sashimi"),
    ("maki",             "sashimi"),
    ("sashimi",          "sashimi"),
    ("sushi",            "sashimi"),
    ("gimbap",           "sashimi"),
    ("poke",             "sashimi"),
    ("tonkatsu",         "grill"),
    ("katsu",            "grill"),
    ("teriyaki",         "grill"),
    ("tempura",          "seafood platter"),
    ("ramen",            "noodle"),
    ("udon",             "noodle"),
    ("soba",             "noodle"),
    ("miso",             "soup"),
    # ── Mexican / Tex-Mex ───────────────────────────────────────────────────
    ("quesadilla",       "tacos"),
    ("quesadillas",      "tacos"),
    ("taco",             "tacos"),
    ("burrito",          "wrap"),
    # ── Lebanese / Levantine ────────────────────────────────────────────────
    ("safiha",           "bread and bakery"),
    ("sfeeha",           "bread and bakery"),
    ("manoushe",         "manoushe"),
    ("manaqeesh",        "manoushe"),
    ("fatayer",          "bread and bakery"),
    ("arayes",           "bread and bakery"),
    ("kibbeh",           "appetizer"),
    ("warak",            "appetizer"),
    ("sambousek",        "appetizer"),
    ("fatteh",           "fatteh"),
    ("fattoush",         "salad"),
    ("tabbouleh",        "salad"),
    ("shawarma",         "shawarma"),
    ("falafel",          "falafel"),
    ("homos",            "hummus"),
    ("hummus",           "hummus"),
    ("kunafa",           "kunafa"),
    ("knafeh",           "kunafa"),
    ("baklava",          "baklava"),
    ("basbousa",         "basbousa"),
    ("maamoul",          "maamoul"),
    ("halawa",           "sweets & desserts"),
    ("halva",            "sweets & desserts"),
    ("mahalabia",        "pudding"),
    # ── Arabic rice dishes ───────────────────────────────────────────────────
    ("mandi",            "rice dish"),
    ("kabsa",            "rice dish"),
    ("machboos",         "rice dish"),
    ("ouzi",             "rice dish"),
    ("harees",           "rice dish"),
    ("biryani",          "rice dish"),
    # ── Indian / Pakistani ──────────────────────────────────────────────────
    ("tikka",            "tikka"),
    ("tandoori",         "grill"),
    ("korma",            "indian/pakistani veg curry"),
    ("daal",             "indian/pakistani veg curry"),
    ("dal ",             "indian/pakistani veg curry"),   # trailing space avoids "dalal"
    ("saag",             "indian/pakistani veg curry"),
    ("paneer",           "indian/pakistani veg curry"),
    ("palak",            "indian/pakistani veg curry"),
    ("chana",            "indian/pakistani veg curry"),
    ("rajma",            "indian/pakistani veg curry"),
    ("idli",             "dosa"),
    ("dosa",             "dosa"),
    ("vada",             "dosa"),
    ("paratha",          "bread and bakery"),
    ("naan",             "bread and bakery"),
    ("roti",             "bread and bakery"),
    ("pav bhaji",        "pav bhaji"),
    ("lassi",            "lassi/ butter milk"),
    ("raita",            "dairy and yogurt"),
    # ── Filipino ────────────────────────────────────────────────────────────
    ("tosilog",          "breakfast plate"),
    ("longsilog",        "breakfast plate"),
    ("tapsilog",         "breakfast plate"),
    ("silog",            "breakfast plate"),
    ("sisig",            "grill"),
    ("adobo",            "grill"),
    ("lechon",           "grill"),
    ("pancit",           "noodle"),
    # ── Korean ──────────────────────────────────────────────────────────────
    ("bibimbap",         "rice dish"),
    ("bulgogi",          "grill"),
    ("galbi",            "grill"),
    ("japchae",          "noodle"),
    ("tteokbokki",       "savory snack"),
    ("doenjang",         "soup"),
    # ── Protein / generic meat ───────────────────────────────────────────────
    ("chicken",          "grill"),
    ("beef",             "grill"),
    ("lamb",             "grill"),
    ("mutton",           "grill"),
    ("veal",             "grill"),
    ("pork",             "grill"),
    ("duck",             "grill"),
    ("turkey",           "grill"),
    # ── Seafood ─────────────────────────────────────────────────────────────
    ("prawn",            "seafood platter"),
    ("shrimp",           "seafood platter"),
    ("lobster",          "seafood platter"),
    ("crab",             "seafood platter"),
    ("octopus",          "seafood platter"),
    ("squid",            "seafood platter"),
    ("hammour",          "fish"),
    ("salmon",           "seafood platter"),
    ("tuna",             "seafood platter"),
    # ── Pasta / noodle ──────────────────────────────────────────────────────
    ("spaghetti",        "pasta (tomato marinara)"),
    ("penne",            "pasta (tomato marinara)"),
    ("fettuccine",       "pasta (tomato marinara)"),
    ("linguine",         "pasta (tomato marinara)"),
    ("lasagna",          "pasta (tomato marinara)"),
    ("noodle",           "noodle"),
    # ── Bread ───────────────────────────────────────────────────────────────
    ("pita",             "bread and bakery"),
    ("baguette",         "baguette"),
    ("croissant",        "croissant"),
    ("flatbread",        "bread and bakery"),
    ("bread",            "bread and bakery"),
    # ── Sweets / desserts ────────────────────────────────────────────────────
    ("cheesecake",       "sweets & desserts"),
    ("mousse",           "mousse"),
    ("pudding",          "pudding"),
    ("ice cream",        "ice cream"),
    ("gelato",           "ice cream"),
    ("sorbet",           "ice cream"),
    ("waffle",           "waffle"),
    ("crepe",            "crepe"),
    ("cake",             "sweets & desserts"),
    ("tart",             "sweets & desserts"),
    ("cookie",           "cookie"),
    ("brownie",          "sweets & desserts"),
    ("macaron",          "sweets & desserts"),
    ("donut",            "sweets & desserts"),
    ("dates",            "dates"),
    # ── Drinks ──────────────────────────────────────────────────────────────
    ("milkshake",        "milkshake"),
    ("smoothie",         "smoothie"),
    ("juice",            "juice"),
    ("lemonade",         "juice"),
    ("cappuccino",       "hot beverage"),
    ("espresso",         "hot beverage"),
    ("americano",        "hot beverage"),
    ("latte",            "hot beverage"),
    ("coffee",           "hot beverage"),
    ("matcha",           "matcha"),
    ("green tea",        "hot beverage"),
    ("herbal tea",       "hot beverage"),
    ("iced tea",         "iced tea"),
    ("mojito",           "mojito"),
    ("mocktail",         "beverages"),
    ("kombucha",         "kombucha"),
    ("cola",             "soft drink"),
    ("pepsi",            "soft drink"),
    ("7up",              "soft drink"),
    ("sprite",           "soft drink"),
    ("water",            "sparkling water"),
    # ── Misc food ───────────────────────────────────────────────────────────
    ("salad",            "salad"),
    ("soup",             "soup"),
    ("stew",             "soup"),
    ("broth",            "soup"),
    ("burger",           "burger"),
    ("sandwich",         "sandwich"),
    ("wrap",             "wrap"),
    ("pizza",            "pizza"),
    ("kebab",            "kebab"),
    ("kabab",            "kebab"),
    ("doner",            "kebab"),
    ("fries",            "hot sides"),
    ("chips",            "hot sides"),
    ("popcorn",          "savory snack"),
    ("nuts",             "nuts"),
    ("rice",             "rice dish"),
]

# ── Category rules ────────────────────────────────────────────────────────────
CATEGORY_RULES: list[tuple[str, str]] = [
    ("sushi",            "sashimi"),
    ("maki",             "sashimi"),
    ("poke",             "sashimi"),
    ("wrap",             "wrap"),
    ("appetizer",        "appetizer"),
    ("starter",          "appetizer"),
    ("mezza",            "appetizer"),
    ("mezze",            "appetizer"),
    ("grill",            "grill"),
    ("grilled",          "grill"),
    ("bbq",              "grill"),
    ("beef",             "grill"),
    ("lamb",             "grill"),
    ("mutton",           "grill"),
    ("meat",             "grill"),
    ("burger",           "burger"),
    ("pizza",            "pizza"),
    ("salad",            "salad"),
    ("soup",             "soup"),
    ("stew",             "soup"),
    ("egg",              "egg plates"),
    ("bread",            "bread and bakery"),
    ("bakery",           "bread and bakery"),
    ("flatbread",        "bread and bakery"),
    ("noodle",           "noodle"),
    ("ramen",            "noodle"),
    ("pasta",            "pasta (tomato marinara)"),
    ("kebab",            "kebab"),
    ("kabab",            "kebab"),
    ("sandwich",         "sandwich"),
    ("dessert",          "sweets & desserts"),
    ("sweet",            "sweets & desserts"),
    ("juice",            "juice"),
    ("smoothie",         "smoothie"),
    ("mocktail",         "beverages"),
    ("beverage",         "beverages"),
    ("drink",            "beverages"),
    ("side",             "hot sides"),
    ("rice",             "rice dish"),
    ("biryani",          "rice dish"),
    ("mandi",            "rice dish"),
    ("kabsa",            "rice dish"),
    ("seafood",          "seafood platter"),
    ("fish",             "seafood platter"),
    ("prawn",            "seafood platter"),
    ("snack",            "savory snack"),
    ("water",            "sparkling water"),
    ("chocolate",        "chocolate"),
    ("dosa",             "dosa"),
    ("idli",             "dosa"),
    ("crepe",            "crepe"),
    ("steak",            "steak"),
    ("wing",             "wings"),
    ("strip",            "strips"),
    ("tender",           "strips"),
    ("shawarma",         "shawarma"),
    ("taco",             "tacos"),
    ("waffle",           "waffle"),
    ("cookie",           "cookie"),
    ("ice cream",        "ice cream"),
    ("gelato",           "ice cream"),
    ("milkshake",        "milkshake"),
    ("frappe",           "milkshake"),
    ("coffee",           "hot beverage"),
    ("hot drink",        "hot beverage"),
    ("breakfast",        "breakfast plate"),
    ("curry",            "indian/pakistani veg curry"),
    ("masala",           "indian/pakistani veg curry"),
    ("hummus",           "hummus"),
    ("kunafa",           "kunafa"),
    ("baklava",          "baklava"),
    ("maamoul",          "maamoul"),
    ("mojito",           "mojito"),
    ("matcha",           "matcha"),
    ("lassi",            "lassi/ butter milk"),
    ("buttermilk",       "lassi/ butter milk"),
    ("yogurt",           "dairy and yogurt"),
    ("raita",            "dairy and yogurt"),
    ("meatball",         "grill"),
    ("chicken",          "grill"),
    ("momo",             "dim sum and dumplings"),
    ("dumpling",         "dim sum and dumplings"),
    ("tikka",            "tikka"),
]


def _match(text: str, rules: list[tuple[str, str]], valid: set[str]) -> str | None:
    t = text.lower()
    for kw, term in rules:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, t) and term in valid:
            return term
    return None


def reclassify(name: str, cat: str, desc: str, valid: set[str]) -> str | None:
    return (
        _match(name, NAME_RULES, valid)
        or _match(cat, CATEGORY_RULES, valid)
        or _match(desc, NAME_RULES, valid)
    )


# Terms that are genuinely "Others" or "marketing/non-standard menu" even if
# keywords match — prevents over-eager reclassification of true non-food items
SKIP_IF_CAT_CONTAINS = {
    "merchandise", "stationery", "art", "craft", "flower", "balloon",
    "gift card", "voucher", "loyalty", "service charge", "packaging",
}

# Words that confirm an item is genuinely a soup — if ANY appear in name/cat/desc
# the item stays as "soup" and is NOT re-examined
REAL_SOUP_SIGNALS = {
    "soup", "broth", "stock", "consomme", "consommé", "stew", "chowder",
    "bisque", "porridge", "congee", "shorba", "shorba", "harira", "minestrone",
    "gazpacho", "mulligatawny", "rasam", "dal soup", "lentil soup",
    "chicken soup", "tomato soup", "mushroom soup", "cream soup",
}

# Add these to NAME_RULES — catch items misclassified as soup
SOUP_FIX_RULES: list[tuple[str, str]] = [
    # Confectionery spreads / chocolate
    ("spread",         "sweet spreads"),
    ("paste",          "sweet spreads"),
    ("cacao",          "chocolate"),
    ("cocoa",          "chocolate"),
    ("praline",        "chocolate"),
    ("truffle",        "chocolate"),
    ("ganache",        "chocolate"),
    ("bonbon",         "chocolate"),
    ("hazelnut",       "sweet spreads"),
    # Items with ml volume descriptors that look liquid but are confections
    ("200ml",          "sweets & desserts"),
    ("500ml",          "sweets & desserts"),
    # Protein / grill items that might have been wrongly souped
    ("chicken",        "grill"),
    ("beef",           "grill"),
    ("lamb",           "grill"),
    ("mutton",         "grill"),
    ("fish",           "fish"),
    ("prawn",          "seafood platter"),
    ("shrimp",         "seafood platter"),
    # Rice / noodle that the LLM confused with soup
    ("noodle",         "noodle"),
    ("ramen",          "noodle"),
    ("biryani",        "rice dish"),
    ("rice",           "rice dish"),
]


def run(input_json: Path = INPUT_JSON,
        output_json: Path = OUTPUT_JSON,
        output_xlsx: Path = OUTPUT_XLSX) -> dict:
    logger.info("Loading valid terms ...")
    df = pl.read_csv(HERE / "standard_term_table.csv", infer_schema_length=0)
    valid: set[str] = set(df["Standard Term"].to_list()) | {"Others"}

    logger.info("Loading classified records from {}", input_json.name)
    with open(input_json, encoding="utf-8") as f:
        records: list[dict] = json.load(f)

    total     = len(records)
    # Buckets to re-examine: Others, marketing/non-standard, and wrongly-classified soup
    targets   = {"marketing/non-standard menu", "Others"}
    mkt_count = sum(1 for r in records if r.get("new_std_term") == "marketing/non-standard menu")
    oth_count = sum(1 for r in records if r.get("new_std_term") == "Others")
    soup_cand = [
        r for r in records
        if r.get("new_std_term") == "soup"
        and not any(
            sig in (r.get("item_name","") + r.get("category","") + r.get("description","")).lower()
            for sig in REAL_SOUP_SIGNALS
        )
    ]
    cand_list = [r for r in records if r.get("new_std_term") in targets] + soup_cand

    logger.info("Total records          : {:,}", total)
    logger.info("marketing/non-standard : {:,}  ({:.1f}%)", mkt_count, 100*mkt_count/total)
    logger.info("Others                 : {:,}  ({:.1f}%)", oth_count, 100*oth_count/total)
    logger.info("Suspicious soup items  : {:,}  (no real soup keyword)", len(soup_cand))
    logger.info("Total to re-examine    : {:,}", len(cand_list))

    fixed = 0
    stayed = 0
    fix_log: list[tuple] = []

    # Build a set of row IDs for soup candidates so we can detect them quickly
    soup_cand_ids = {id(r) for r in soup_cand}

    for r in records:
        is_soup_cand = id(r) in soup_cand_ids
        if r.get("new_std_term") not in targets and not is_soup_cand:
            continue

        cat_lower = r.get("category", "").lower()
        # Skip items whose category clearly indicates non-food
        if any(skip in cat_lower for skip in SKIP_IF_CAT_CONTAINS):
            r["post_processed"] = False
            stayed += 1
            continue

        # For soup candidates, use the specialised SOUP_FIX_RULES first
        if is_soup_cand:
            all_text = " ".join([
                r.get("item_name", ""),
                r.get("category", ""),
                r.get("description", ""),
            ])
            new_term = _match(all_text, SOUP_FIX_RULES, valid)
            if not new_term:
                r["post_processed"] = False
                stayed += 1
                continue
        else:
            new_term = reclassify(
                r.get("item_name", ""),
                r.get("category", ""),
                r.get("description", ""),
                valid,
            )

        if new_term:
            old_term = r["new_std_term"]
            fix_log.append((r["item_name"], r.get("category", ""), old_term, new_term))
            r["new_std_term"]   = new_term
            r["post_processed"] = True
            fixed += 1
        else:
            r["post_processed"] = False
            stayed += 1

    pct_fixed = 100 * fixed / max(len(cand_list), 1)
    logger.success("Re-classified : {:,}  ({:.1f}% of target items)", fixed, pct_fixed)
    logger.info   ("Stayed        : {:,}", stayed)

    # ── JSON output ───────────────────────────────────────────────────────────
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    logger.success("JSON  -> {}", output_json.name)

    # ── Excel output ──────────────────────────────────────────────────────────
    wb       = xlsxwriter.Workbook(str(output_xlsx))
    ws       = wb.add_worksheet("Menu")
    hdr_fmt  = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "#FFFFFF"})
    fix_fmt  = wb.add_format({"bg_color": "#E2EFDA", "font_color": "#375623"})
    stay_fmt = wb.add_format({"bg_color": "#FCE4D6", "font_color": "#843C00"})

    headers = ["Item Name", "Category", "Description",
               "existing_std_term", "new_std_term", "post_processed"]
    for c, h in enumerate(headers):
        ws.write(0, c, h, hdr_fmt)
    ws.set_column(0, 0, 40); ws.set_column(1, 1, 25)
    ws.set_column(2, 2, 50); ws.set_column(3, 3, 28)
    ws.set_column(4, 4, 28); ws.set_column(5, 5, 15)

    for i, r in enumerate(records):
        pp  = r.get("post_processed")
        fmt = fix_fmt if pp is True else (stay_fmt if pp is False else None)
        ws.write(i+1, 0, r.get("item_name",        ""))
        ws.write(i+1, 1, r.get("category",         ""))
        ws.write(i+1, 2, r.get("description",      ""))
        ws.write(i+1, 3, r.get("existing_std_term",""))
        ws.write(i+1, 4, r.get("new_std_term",     ""), fmt)
        ws.write(i+1, 5, ("fixed" if pp is True else
                          "kept"  if pp is False else ""), fmt)
    wb.close()
    logger.success("Excel -> {}", output_xlsx.name)

    # ── Report ────────────────────────────────────────────────────────────────
    fix_by_term  = Counter(new_t for _, _, _, new_t in fix_log)
    fix_by_old   = Counter(old_t for _, _, old_t, _ in fix_log)
    final_counts = Counter(r.get("new_std_term") for r in records)
    final_mkt    = sum(1 for r in records if r.get("new_std_term") == "marketing/non-standard menu")
    final_oth    = sum(1 for r in records if r.get("new_std_term") == "Others")

    lines = [
        "=" * 62,
        "POST-PROCESS REPORT",
        "=" * 62,
        f"Total records              : {total:,}",
        f"marketing/non-std (before) : {mkt_count:,}  ({100*mkt_count/total:.1f}%)",
        f"Others (before)            : {oth_count:,}  ({100*oth_count/total:.1f}%)",
        f"Total re-examined          : {len(cand_list):,}",
        f"Re-classified (fixed)      : {fixed:,}  ({pct_fixed:.1f}% of examined)",
        f"Stayed unchanged           : {stayed:,}",
        f"marketing/non-std (after)  : {final_mkt:,}  ({100*final_mkt/total:.1f}%)",
        f"Others (after)             : {final_oth:,}  ({100*final_oth/total:.1f}%)",
        "",
        "-- FIXED FROM (original term) ---------------------------------",
    ]
    for term, cnt in fix_by_old.most_common():
        lines.append(f"  {term:<42s} {cnt:5d}")

    lines += ["", "-- RE-CLASSIFIED INTO (top 20) --------------------------------"]
    for term, cnt in fix_by_term.most_common(20):
        lines.append(f"  {term:<42s} {cnt:5d}")

    lines += [
        "",
        "-- FINAL TERM DISTRIBUTION (top 20) ---------------------------",
    ]
    for term, cnt in final_counts.most_common(20):
        bar = "#" * min(int(cnt / max(total, 1) * 60), 60)
        lines.append(f"  {term:<42s} {cnt:6d}  {bar}")

    lines += ["", "-- SAMPLE FIX LOG (first 50) ----------------------------------"]
    for name, cat, old_t, new_t in fix_log[:50]:
        lines.append(f"  [{cat[:15]}] {name[:35]:<35s}  {old_t[:20]:<20s} -> {new_t}")
    if len(fix_log) > 50:
        lines.append(f"  ... and {len(fix_log)-50} more")
    lines += ["", "=" * 62]

    report = "\n".join(lines)
    print(report)
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write(report)
    logger.success("Report -> {}", REPORT_TXT.name)

    return {"total": total, "fixed": fixed, "stayed": stayed}


if __name__ == "__main__":
    run()
