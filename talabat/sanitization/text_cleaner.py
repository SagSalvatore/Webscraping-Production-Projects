"""
text_cleaner.py — Text sanitization for UAE food aggregator scraping pipeline.

Observed data quality issues in Talabat scraped data:
  1. Menu categories have trailing/embedded emojis  (🍔 🔥 ➕ 🥗 🍕 etc.)
  2. Zero-width joiners (‍) embedded in emoji sequences inside categories
  3. Item names have double spaces from copy-paste artifacts ("Egyptian  Pizza")
  4. Bilingual categories with control characters ("Soup👨‍🍳🍲الشوربة")

Design principles:
  - Arabic text is PRESERVED — bilingual categories are valid for UAE market
  - Item name emojis are PRESERVED — some brands use them intentionally
  - Only CATEGORY gets emoji-stripped (per user requirement)
  - All functions are pure (no side effects) and idempotent
"""

import re

# ── Emoji / decorative symbol ranges ──────────────────────────────────────────
# Covers the full Unicode emoji spec plus dingbats and misc symbol blocks
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # Emoticons
    "\U0001F300-\U0001F5FF"   # Misc Symbols & Pictographs (🍔 🍕 🌮 etc.)
    "\U0001F680-\U0001F6FF"   # Transport & Map Symbols
    "\U0001F700-\U0001F77F"   # Alchemical Symbols
    "\U0001F780-\U0001F7FF"   # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"   # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"   # Supplemental Symbols (🥗 🥣 🧁 etc.)
    "\U0001FA00-\U0001FA6F"   # Chess Symbols
    "\U0001FA70-\U0001FAFF"   # Symbols & Pictographs Extended-A
    "\U00002702-\U000027B0"   # Dingbats (✂ ✈ ➕ etc.)
    "\U000024C2-\U0001F251"   # Enclosed chars, mahjong, playing cards
    "\U00002600-\U000026FF"   # Misc Symbols (☀ ⭐ ♨ ❄ etc.)
    "‍"                   # Zero-Width Joiner (used inside emoji sequences)
    "️"                   # Variation Selector-16 (forces emoji presentation)
    "⃣"                   # Combining Enclosing Keycap (1️⃣ etc.)
    "​"                   # Zero-Width Space
    "‌"                   # Zero-Width Non-Joiner
    "­"                   # Soft Hyphen (invisible, used as separator)
    "]+",
    re.UNICODE,
)

# Control characters (ASCII 0x00–0x1F except tab 0x09 and newline 0x0A/0x0D)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Collapse multiple consecutive spaces/tabs to a single space
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# (Separator normalization removed — hyphens in words like "Add-Ons" must not be expanded)


# ── Core primitives ────────────────────────────────────────────────────────────

def strip_emojis(text: str) -> str:
    """
    Remove emoji and decorative symbol characters. Arabic/Latin text preserved.
    Replaces emoji with a space (not empty string) to prevent word merging:
      "Soup👨‍🍳الشوربة" → "Soup  الشوربة" → (after normalize) "Soup الشوربة"
    """
    return _EMOJI_RE.sub(" ", text)


def strip_controls(text: str) -> str:
    """Remove non-printable ASCII control characters."""
    return _CONTROL_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    """
    Collapse double spaces → single, strip leading/trailing whitespace.
    Also removes control characters first.
    """
    text = strip_controls(text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


# ── Field-level cleaners ───────────────────────────────────────────────────────

def clean_category(cat: str) -> str:
    """
    Clean a menu category string:
      - Strip all emoji / decorative symbols
      - Remove invisible control characters (including ‍ ZWJ)
      - Collapse double spaces, strip whitespace
      - Normalize separator spacing in bilingual text (e.g. "Soup  - الشوربة" → "Soup - الشوربة")

    Arabic text is fully preserved — bilingual (EN+AR) categories are valid.

    Examples:
      "BURGERS 🍔"          → "BURGERS"
      "Extras ➕"           → "Extras"
      "Soup👨‍🍳🍲الشوربة" → "Soup الشوربة"
      "Add-Ons ➕"          → "Add-Ons"
      "Appetizers  🥗 مقبلات" → "Appetizers مقبلات"
    """
    if not cat:
        return cat
    cat = strip_emojis(cat)
    cat = normalize_whitespace(cat)
    return cat


def clean_item_name(name: str) -> str:
    """
    Clean a menu item name:
      - Remove control characters
      - Normalize double spaces (common copy-paste artifact)
      - Emojis are intentionally KEPT (brands use them in item names)

    Examples:
      "Basterma Egyptian  Pizza" → "Basterma Egyptian Pizza"
      "Avocado Oil  Now"         → "Avocado Oil Now"
    """
    if not name:
        return name
    return normalize_whitespace(name)


def clean_description(desc: str) -> str:
    """Normalize whitespace in description. Emojis preserved."""
    if not desc:
        return desc
    return normalize_whitespace(desc)


# ── Item-level and list-level sanitizers ──────────────────────────────────────

def sanitize_menu_item(item: dict) -> dict:
    """
    Apply all field cleaners to a menu item dict.
    Mutates in-place AND returns the item (safe to use in list comprehensions).
    Idempotent — safe to call multiple times.
    """
    if item.get("category"):
        item["category"] = clean_category(item["category"])
    if item.get("item_name"):
        item["item_name"] = clean_item_name(item["item_name"])
    if item.get("description"):
        item["description"] = clean_description(item["description"])
    return item


def sanitize_items(items: list[dict]) -> list[dict]:
    """Apply sanitize_menu_item to every item in the list. Returns the same list."""
    for item in items:
        sanitize_menu_item(item)
    return items
