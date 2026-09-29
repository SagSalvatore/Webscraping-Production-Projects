"""Find menu entries that are NOT food but carry a food std_term and fabricated
ingredients - judged on all THREE signals together: item name, description and
menu category.

WHY THREE AND NOT ONE. Per Sagar: the std_term and ingredients were originally
decided from name + description + category in combination, so detecting their
errors has to use the same combination. Each signal alone is provably wrong:

  name only      `Green Machine` is a smoothie; `Half Machine Chicken` is food.
  category only  `Gift Boxes` is 189 items of baklava and dates - a gift box of
                 baklava IS food. Trusting the category name would have stripped
                 correct labels off ~2,500 real products.
  description    absent on 79,796 entries, so it can confirm but never decide.

HEAD-NOUN LOGIC. English compounds put the head last: `Cake Knife` is a knife,
`Chocolate Balloon` is a balloon. A flat keyword match sees "cake"/"chocolate"
and vetoes both. So the name is parsed for its head noun after stripping size
and SKU tails (`30 Cm`, `Pp289`, `500 Gr`):

  NONFOOD_HEAD    knife, balloon, candle, bouquet, teddy ... -> non-food whatever
                  the modifiers say.
  CONTAINER_HEAD  box, hamper, basket, tray, pack, set ... -> NEUTRAL. A
                  container inherits its contents, so `Baklava Gift Box` stays
                  food and `Cutlery Box` does not.

THE FOOD VETO. Any food evidence in name, description or category clears the
item, except when the head noun is unambiguously non-food. This is deliberately
biased towards leaving items alone: a missed non-food item is one wrong label,
but a wrongly-stripped food item destroys a correct one.

Report only - writes a CSV for review and changes nothing.

    python detect_nonfood_items.py
    python detect_nonfood_items.py --min-tier B
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ijson

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FILE = DATA / "July_menu_update.json"
OUT_CSV = DATA / "nonfood_candidates.csv"
OUT_JSON = DATA / "nonfood_candidates_report.json"

sys.stdout.reconfigure(encoding="utf-8")

NON_FOOD_TERMS = {"Marketing/Non-Standard Menu", "Service And Packaging"}

# --- head nouns -----------------------------------------------------------
# Unambiguously not edible. Presence as the HEAD of the name is decisive.
NONFOOD_HEAD = {
    # flowers
    "bouquet", "bouquets", "flower", "flowers", "rose", "roses", "orchid",
    "orchids", "tulip", "tulips", "lily", "lilies", "carnation", "carnations",
    "hydrangea", "sunflowers", "peony", "peonies", "anthurium", "gypsophila",
    "vase", "stem", "stems", "arrangement", "wreath", "garland", "plant",
    # party
    "balloon", "balloons", "candle", "candles", "banner", "bunting",
    "confetti", "streamer", "streamers", "sparkler", "sparklers", "ribbon",
    "topper", "toppers", "sticker", "stickers", "invitation", "invitations",
    # toys / gifts
    "teddy", "bear", "plush", "toy", "toys", "doll", "puzzle", "figurine",
    "keychain", "keyring", "mug", "frame", "photoframe", "cushion", "pillow",
    "card", "cards", "envelope", "voucher", "cutout",
    # stationery
    "stationery", "notebook", "pencil", "pencils", "eraser", "sharpener",
    "stapler", "crayon", "crayons", "marker", "markers", "highlighter",
    "folder", "binder", "scissors", "tape", "glue",
    # cleaning / household
    "detergent", "bleach", "disinfectant", "sanitizer", "cleaner", "scrubber",
    "sponge", "mop", "broom", "duster", "freshener", "repellent", "insecticide",
    # personal care
    "shampoo", "conditioner", "soap", "toothpaste", "toothbrush", "razor",
    "deodorant", "perfume", "lotion", "sunscreen", "diaper", "diapers",
    "wipes", "pads", "sanitizers", "mask", "masks", "gloves",
    # tableware / packaging / hardware
    "cutlery", "knife", "knives", "fork", "forks", "spoon", "spoons",
    "straw", "straws", "napkin", "napkins", "tissue", "tissues", "foil",
    "clingfilm", "charger", "cable", "battery", "batteries", "bulb",
    "lighter", "matchbox", "candleholder", "holder", "opener", "cutter",
    "grinder", "shaker", "tongs", "skewer", "skewers", "apron", "towel",
    "bag", "bags",
}

# Take their food-ness from what they contain, so they decide nothing alone.
CONTAINER_HEAD = {
    "box", "boxes", "basket", "baskets", "hamper", "hampers", "tray", "trays",
    "pack", "packs", "packet", "packets", "set", "sets", "collection",
    "bundle", "combo", "platter", "assortment", "selection", "gift", "gifts",
    "special", "deal", "deals", "offer", "hamper", "kit", "surprise",
}

# Trailing adjectives that qualify the head without being it: `Teddy 30 Cm Red`
# is a teddy, not a `red`. Popped like containers so the real noun surfaces.
# `orange` is deliberately absent - it is a fruit far more often than a colour.
MODIFIER_TAIL = {
    "red", "blue", "pink", "white", "black", "green", "gold", "golden",
    "silver", "yellow", "purple", "brown", "grey", "gray", "beige", "ivory",
    "multicolor", "multicolour", "rainbow", "assorted", "mixed", "mix",
    "large", "small", "medium", "mini", "big", "extra", "xl", "xxl", "jumbo",
    "regular", "tall", "long", "giant", "single", "double", "piece", "pieces",
    "new", "premium", "luxury", "classic", "plain", "printed", "colour",
    "color", "size", "pcs", "pc",
}

# Size / SKU tails to strip before locating the head.
TAIL = re.compile(
    r"\b(\d+(\.\d+)?\s*(cm|mm|m|inch|in|kg|kgs|g|gr|gm|gms|ml|l|ltr|litre|pc|"
    r"pcs|piece|pieces|pack|packs|set|sets|x)\b|pp\s?\d+|no\.?\s?\d+|"
    r"\d+\s*[-x]\s*\d+|#\d+|\(\s*\d+\s*\))", re.I)
NUMTAIL = re.compile(r"[\s\-,/()\[\]]+$")

# --- lexicons -------------------------------------------------------------
NONFOOD_RX = re.compile(
    r"\b(bouquet|floral|flower|helium|balloon|teddy|plush|greeting card|"
    r"gift card|stationery|stationary|notebook|crayon|detergent|bleach|"
    r"dishwash|laundry|fabric softener|disinfectant|sanitiz|toilet|shampoo|"
    r"toothpaste|toothbrush|deodorant|perfume|diaper|sanitary|wet wipes|"
    r"cutlery|serviette|cling film|charger|usb|battery|light bulb|"
    r"party (?:hat|popper|material|suppl)|art ?& ?craft|scented candle|"
    r"party candle|number candle|birthday candle|vase|artificial|"
    r"non[- ]edible|not edible|decoration|decor\b|ornament)", re.I)

# Food evidence. Broad on purpose - this is the safety net that protects
# genuine products, so over-matching here is the safe direction.
FOOD_RX = re.compile(
    r"\b(chicken|beef|lamb|mutton|veal|duck|turkey|fish|salmon|tuna|prawn|"
    r"shrimp|crab|lobster|seafood|meat|egg|omelette|cheese|milk|yogurt|"
    r"yoghurt|labneh|butter|ghee|bread|bun|toast|croissant|rice|biryani|"
    r"pasta|noodle|spaghetti|burger|pizza|sandwich|shawarma|wrap|taco|"
    r"salad|soup|broth|juice|coffee|espresso|latte|cappuccino|mocha|tea|"
    r"karak|shake|smoothie|frappe|soda|cola|pepsi|water|lemonade|mojito|"
    r"cake|pastry|dessert|ice cream|gelato|biscuit|cookie|brownie|donut|"
    r"doughnut|waffle|pancake|crepe|muffin|cupcake|pudding|custard|"
    r"chocolate|candy|toffee|caramel|honey|date|dates|kunafa|baklava|"
    r"halwa|ladoo|barfi|sweets|nuts|almond|pistachio|cashew|walnut|hazelnut|"
    r"peanut|kebab|shish|tikka|curry|masala|dal|paneer|samosa|falafel|"
    r"hummus|moutabal|tabbouleh|fattoush|mandi|machboos|shakshuka|foul|"
    r"manakish|fatayer|sambousek|grill|roast|fried|fries|nugget|wings|"
    r"steak|shawaya|sauce|dip|mayo|ketchup|mustard|syrup|jam|honey|olive|"
    r"spice|flour|sugar|vegetable|fruit|mango|banana|apple|orange|"
    r"strawberry|avocado|potato|tomato|onion|garlic|corn|beans|lentil|"
    r"soup|meal|combo meal|breakfast|lunch|dinner|snack|platter of|"
    r"edible|flavour|flavor|taste|serving|portion|gram of|calorie)", re.I)

# Food words that are ALSO common non-food modifiers - excluded from the veto
# when they only appear as a decorative descriptor.
SOFT_FOOD = re.compile(r"\b(chocolate|candy|cake|strawberry|vanilla)\b", re.I)


def tokens(s):
    return re.findall(r"[a-z]+", (s or "").lower())


def head_noun(name):
    """Last meaningful token, after size/SKU tails are stripped."""
    s = TAIL.sub(" ", name or "")
    s = NUMTAIL.sub("", s.strip())
    tk = tokens(s)
    # `Cutlery Gift Box` -> `cutlery`; `Teddy 30 Cm Red` -> `teddy`. The len>1
    # guard stops a name that is ONLY containers/adjectives from emptying out.
    while len(tk) > 1 and (tk[-1] in CONTAINER_HEAD or tk[-1] in MODIFIER_TAIL):
        tk.pop()
    return tk[-1] if tk else ""


def classify(name, desc, section, cat_ratio):
    """Return (tier, reasons) or (None, reasons) if the item looks like food."""
    reasons = []
    nm, ds, sc = name or "", desc or "", section or ""

    head = head_noun(nm)
    head_nf = head in NONFOOD_HEAD

    nf_name = bool(NONFOOD_RX.search(nm))
    nf_desc = bool(NONFOOD_RX.search(ds))
    nf_sect = bool(NONFOOD_RX.search(sc))
    nf_fields = sum((nf_name or head_nf, nf_desc, nf_sect))

    food_name = bool(FOOD_RX.search(nm))
    food_desc = bool(FOOD_RX.search(ds))
    food_sect = bool(FOOD_RX.search(sc))

    # The DESCRIPTION is the only field that states what the item actually IS,
    # so it outranks even an unambiguous head noun. `Artisanal Black Tray With
    # Flower` reads "Edible Chocolate Flowers" and `Valentine Cake In Flower
    # Bag` reads "Cake To Devour" - both are food despite non-food heads, and
    # both are exactly the Valentine's gift-combo shape (roses AND chocolates)
    # where the food half is the substance. Never overridden.
    if food_desc:
        reasons.append("food_in_description")
        return None, reasons

    # A head noun that cannot be eaten overrides food words in the NAME and
    # CATEGORY only: `Cake Knife` is a knife, `Chocolate Balloon` is a balloon.
    if not head_nf and (food_name or food_sect):
        reasons.append("food_evidence")
        return None, reasons

    if head_nf:
        reasons.append(f"head={head}")
    if nf_name:
        reasons.append("name")
    if nf_desc:
        reasons.append("desc")
    if nf_sect:
        reasons.append("section")
    if cat_ratio >= 0.8:
        reasons.append(f"cat_{cat_ratio:.0%}_nonfood")

    strong_cat = cat_ratio >= 0.8
    # A: name says non-food AND the category agrees (either by its own name or
    #    because the reviewer already called most of its items non-food)
    if (head_nf or nf_name) and (nf_sect or strong_cat):
        return "A", reasons
    # B: two independent fields agree, no food anywhere
    if nf_fields >= 2:
        return "B", reasons
    # C: an unambiguous head noun, nothing contradicting it
    if head_nf and not (food_name or food_desc or food_sect):
        return "C", reasons
    return None, reasons


def main(args):
    print("=" * 78)
    print("  THREE-SIGNAL NON-FOOD DETECTOR  (name + description + category)")
    print("=" * 78)

    # pass 1 - how the reviewer already labelled each category
    print("\n  pass 1/2  measuring category composition ...")
    cat = defaultdict(Counter)
    with open(FILE, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            for it in rec.get("menu_items") or []:
                nf = it.get("std_term") in NON_FOOD_TERMS
                cat[it.get("section") or ""]["nf" if nf else "f"] += 1
    ratio = {}
    for s, c in cat.items():
        tot = c["nf"] + c["f"]
        ratio[s] = c["nf"] / tot if tot >= 5 else 0.0
    print(f"            {len(cat):,} categories")

    # pass 2 - detect
    print("  pass 2/2  scanning items ...")
    tiers = Counter()
    rows = []
    n_item = 0
    seen_key = set()
    with open(FILE, "rb") as f:
        for rec in ijson.items(f, "item", use_float=True):
            sid = rec.get("source_id")
            sname = rec.get("source_name")
            for it in rec.get("menu_items") or []:
                n_item += 1
                term = it.get("std_term")
                if term in NON_FOOD_TERMS:
                    continue                      # already correct
                nm = it.get("name") or ""
                ds = it.get("description") or ""
                sc = it.get("section") or ""
                tier, why = classify(nm, ds, sc, ratio.get(sc, 0.0))
                if not tier:
                    continue
                tiers[tier] += 1
                rows.append({
                    "tier": tier, "source_id": sid, "restaurant": sname,
                    "item_name": nm, "menu_category": sc,
                    "description": ds[:180], "current_std_term": term,
                    "ingredients": "; ".join(it.get("ingredients") or []),
                    "n_ingredients": len(it.get("ingredients") or []),
                    "price_aed": it.get("price"), "evidence": ",".join(why),
                })
                seen_key.add((nm, sc))

    print(f"\n  menu entries scanned : {n_item:,}")
    print(f"  candidates           : {len(rows):,}"
          f"   ({len(rows)/n_item*100:.2f}% of the file)")
    print(f"  distinct (name,category) pairs : {len(seen_key):,}")
    print("\n  by tier:")
    for t, label in (("A", "name + category agree"),
                     ("B", "two fields agree"),
                     ("C", "unambiguous head noun")):
        print(f"     {t}  {tiers[t]:>7,}   {label}")

    order = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda r: (order[r["tier"]], -(r["price_aed"] or 0)))

    for t in ("A", "B", "C"):
        sel = [r for r in rows if r["tier"] == t]
        if not sel:
            continue
        print(f"\n  --- TIER {t}  ({len(sel):,} rows) ---")
        shown = set()
        n = 0
        for r in sel:
            k = (r["item_name"], r["menu_category"])
            if k in shown:
                continue
            shown.add(k)
            n += 1
            if n > 12:
                break
            print(f"     {r['item_name'][:34]:36} | {r['menu_category'][:20]:22}"
                  f" | {str(r['current_std_term'])[:20]:22} "
                  f"| {r['n_ingredients']} ing | {r['price_aed']}")
            if r["description"]:
                print(f"         desc: {r['description'][:78]}")

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["tier"])
        w.writeheader()
        w.writerows(rows)
    OUT_JSON.write_text(json.dumps({
        "menu_entries": n_item, "candidates": len(rows),
        "by_tier": dict(tiers),
        "distinct_name_category": len(seen_key),
    }, indent=2), encoding="utf-8")
    print(f"\n  -> {OUT_CSV.name}  ({len(rows):,} rows)")
    print(f"  -> {OUT_JSON.name}")
    print("\n  REPORT ONLY - the deliverable was not modified.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min-tier", default="C")
    sys.exit(main(p.parse_args()))
