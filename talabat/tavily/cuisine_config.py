"""
cuisine_config.py — Taxonomy, routing rules, and shared constants for
cuisine classification.
"""
import re

# ── Cuisine Type taxonomy (cleaned — removed non-food entries "Lemon juice", "Butter") ──
CUISINE_TYPES: list[str] = [
    "Asian",
    "American",
    "Café",
    "Multi-Cuisine",
    "Middle Eastern and North African",
    "Bakery",
    "Mediterranean",
    "European",
    "Beverages",
]

# ── Sub-cuisine taxonomy (from sub-cuisine.csv) ───────────────────────────────
SUB_CUISINE_TYPES: list[str] = [
    "Chinese",
    "American",
    "Café",
    "Indian, Arabic",
    "North American",
    "Lebanese",
    "Japanese",
    "Mexican",
    "Indian",
    "Bakeries",
    "Arabic, Egyptian",
    "Asian",
    "International",
    "Indian, Pakistani",
    "Arabic",
    "Italian",
    "North Indian, Indian",
    "Thai, Asian",
    "American, Italian",
    "Arabic, Lebanese",
    "Moroccan, Arabic",
    "Cafe",
    "Middle Eastern, North American",
    "North American, Mexican",
    "Chinese, Asian",
    "Asian, Japanese, International",
    "Japanese, Asian",
    "Asian, Japanese",
    "Indian, North Indian",
    "Beverages",
    "Indian, Asian",
    "Pakistani",
    "Lebanese, Arabic",
    "Vietnamese, Asian",
    "Arabic, North American",
    "Asian, Middle Eastern, American",
    "Middle Eastern, Asian",
    "Kerala, Indian, South Indian",
    "Japanese, International, Asian",
    "Middle Eastern, Indian, Western, European, Healthy, Desserts, Mexican, Seafood, Bakery",
    "Middle Eastern, Western",
    "Middle Eastern",
]

# ── Routing: KEY CUISINES quality check ───────────────────────────────────────

_GARBLED_PATTERN = re.compile(
    r"^[a-z]"          # starts with lowercase → truncation artefact (e.g. "ican·Pizza")
    r"|^[·\s]"         # starts with bullet/space → missing beginning
)

def needs_tavily(key_cuisines: str | None) -> bool:
    """
    Returns True when KEY CUISINES is absent or too garbled to be useful.
    Mid-string bullets (·) are treated as comma-separators and are acceptable.
    If True → Tavily web search needed before OpenAI.
    """
    if not isinstance(key_cuisines, str) or not key_cuisines.strip():
        return True
    return bool(_GARBLED_PATTERN.search(key_cuisines.strip()))


# ── OpenAI config ─────────────────────────────────────────────────────────────
OPENAI_MODEL       = "gpt-4o-mini"
OPENAI_BATCH_SIZE  = 10
OPENAI_MAX_TOKENS  = 900
OPENAI_TEMPERATURE = 0.0

# ── Tavily config ─────────────────────────────────────────────────────────────
TAVILY_MAX_RESULTS  = 3
TAVILY_SEARCH_DEPTH = "basic"
TAVILY_CONCURRENCY  = 20

# ── Async concurrency ─────────────────────────────────────────────────────────
OPENAI_CONCURRENCY = 20

# ── Output column names ───────────────────────────────────────────────────────
COL_CUISINE_TYPE    = "UAE_Cuisine Type"
COL_SUB_CUISINE     = "UAE_Sub- cuisine types"
COL_CUISINE_CONF    = "cuisine_confidence"
COL_CUISINE_REASON  = "cuisine_reasoning"
COL_MATCHED_UAE     = "matched_uae_name"
