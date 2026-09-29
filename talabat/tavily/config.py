"""
config.py — Classification constants and cost controls.
"""
import re

# ── Model & cost settings ────────────────────────────────────────────────────
OPENAI_MODEL       = "gpt-4o-mini"
OPENAI_BATCH_SIZE  = 5       # restaurants per single OpenAI call (reduces cost 5x)
OPENAI_TEMPERATURE = 0.1
OPENAI_MAX_TOKENS  = 1200    # per batch call

TAVILY_MAX_RESULTS = 3       # results per search (fewer = faster & cheaper)
TAVILY_SEARCH_DEPTH = "basic" # "basic"=1 credit  vs "advanced"=2 credits

# Hard budget guards (abort if exceeded)
TAVILY_CREDIT_LIMIT  = 750   # stop at 750 of 800 available (leave buffer)
OPENAI_TOKEN_LIMIT   = 280_000  # ~$0.042 input + ~$0.042 output ≈ well under $5

# ── Deliveroo / platform suffix stripping ───────────────────────────────────
_PLATFORM_SUFFIXES = re.compile(
    r"\s+delivery\s+from\b.*$"
    r"|\s+order\s+with\b.*$"
    r"|\s*[-–|]\s*(al\s+\w+|jvc|jbr|jlt|difc|silicon oasis|motor city"
    r"|mussafah|mushrif|wasl|barsha|karama|nahda|mirdif|marina"
    r"|jumeirah\s+\w*|downtown|al\s+safa)\s*$",
    re.IGNORECASE,
)
_TRAILING_LOCATION = re.compile(
    r",?\s*\b(uae|dubai|abu dhabi|sharjah|ajman)\b.*$",
    re.IGNORECASE,
)

def clean_name(raw: str) -> str:
    """Strip delivery-platform boilerplate to get the core brand name."""
    s = raw.strip()
    s = _PLATFORM_SUFFIXES.sub("", s)
    s = _TRAILING_LOCATION.sub("", s)
    return s.strip(" -–|,")


# ── Tier-1 pre-classification lookup (no API calls needed) ──────────────────
# Format: lowercase_brand_fragment → (classification, outlet_type, chained_outlet_type, type_of_restaurant)
KNOWN_MNC = {
    # ── Global QSR / Fast Food ──────────────────────────────────────────────
    "mcdonald":       ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "kfc":            ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "pizza hut":      ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "subway":         ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "burger king":    ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "hardee":         ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "papa john":      ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "domino":         ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "popeyes":        ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "taco bell":      ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "wendy":          ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "raising cane":   ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "wingstop":       ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "texas chicken":  ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "jollibee":       ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "kyochon":        ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "new york fries": ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "church's":       ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "maestro pizza":  ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "makers of milkshakes": ("MNC Chain", "Chain", "MNC Chain", "Café / Coffee Shop"),
    "mcdavid":        ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "sonic drive":    ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "dunkin":         ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "baskin":         ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "dairy queen":    ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "baskin robbins": ("MNC Chain", "Chain", "MNC Chain", "Quick-Service Restaurant (QSR)"),
    "krispy kreme":   ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "cinnabon":       ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    # ── Fast-Casual ──────────────────────────────────────────────────────────
    "shake shack":    ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "five guys":      ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "dave's hot chicken": ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "tortilla":       ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "nando":          ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "wagamama":       ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "chipotle":       ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "panda express":  ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "buffalo wings":  ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "wok to walk":    ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "pita pit":       ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "schlotzsky":     ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "oakberry":       ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    "pinkberry":      ("MNC Chain", "Chain", "MNC Chain", "Fast-Casual Restaurant"),
    # ── Casual / Fine Dining ─────────────────────────────────────────────────
    "ihop":           ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "pizzaexpress":   ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "pizza express":  ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "tgi friday":     ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "applebee":       ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "chili's":        ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "the cheesecake factory": ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "p.f. chang":     ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "hard rock":      ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "bubba gump":     ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "denny":          ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "tony roma":      ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "outback":        ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "vapiano":        ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "black tap":      ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "nobu":           ("MNC Chain", "Chain", "MNC Chain", "Fine Dining Restaurant"),
    "zuma":           ("MNC Chain", "Chain", "MNC Chain", "Fine Dining Restaurant"),
    "coya":           ("MNC Chain", "Chain", "MNC Chain", "Fine Dining Restaurant"),
    "nusr-et":        ("MNC Chain", "Chain", "MNC Chain", "Fine Dining Restaurant"),
    "salt bae":       ("MNC Chain", "Chain", "MNC Chain", "Fine Dining Restaurant"),
    "texas de brazil": ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "fogo de chao":   ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "benihana":       ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "the melting pot": ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "ruby tuesday":   ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "pizza 4p":       ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    "original pancake": ("MNC Chain", "Chain", "MNC Chain", "Casual Dining Restaurant"),
    # ── Coffee / Café chains ─────────────────────────────────────────────────
    "starbucks":      ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "tim hortons":    ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "costa coffee":   ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "caribou coffee": ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "peet's coffee":  ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "cotti coffee":   ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "% arabica":      ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "black coffee":   ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "juan valdez":    ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "coffee bean":    ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "second cup":     ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "gloria jean":    ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "lavazza":        ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "illy":           ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "hollys coffee":  ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    "mcdonalds mccafé": ("MNC Chain", "Chain", "MNC Chain", "Coffee Shop"),
    # ── Bakery / Patisserie chains ────────────────────────────────────────────
    "paul bakery":    ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "paul restaurant":("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "le pain quotidien": ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "laduree":        ("MNC Chain", "Chain", "MNC Chain", "Patisserie"),
    "ladurée":        ("MNC Chain", "Chain", "MNC Chain", "Patisserie"),
    "pret a manger":  ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "eric kayser":    ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
    "pierre hermé":   ("MNC Chain", "Chain", "MNC Chain", "Patisserie"),
    "fauchon":        ("MNC Chain", "Chain", "MNC Chain", "Patisserie"),
    "breadtalk":      ("MNC Chain", "Chain", "MNC Chain", "Bakery & Café"),
}

KNOWN_LOCAL_CHAIN = {
    # ── UAE / GCC local QSR chains ────────────────────────────────────────────
    "albaik":            ("Local Chain", "Chain", "Local Chain", "Quick-Service Restaurant (QSR)"),
    "salt":              ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "zaatar w zeit":     ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "chicking":          ("Local Chain", "Chain", "Local Chain", "Quick-Service Restaurant (QSR)"),
    "kcal":              ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "operation falafel": ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "wokyo":             ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "drift burgers":     ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "wrap & roll":       ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "healthy bowlz":     ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "kebab express":     ("Local Chain", "Chain", "Local Chain", "Quick-Service Restaurant (QSR)"),
    "ravi restaurant":   ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "london dairy":      ("Local Chain", "Chain", "Local Chain", "Quick-Service Restaurant (QSR)"),
    "salam restaurant":  ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "punjab grill":      ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "grills & more":     ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "the noodle house":  ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "fuddruckers":       ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "pickl":             ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "burger fuel":       ("Local Chain", "Chain", "Local Chain", "Fast-Casual Restaurant"),
    "the maine":         ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "legends":           ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    "more cafe":         ("Local Chain", "Chain", "Local Chain", "Café / Coffee Shop"),
    "o'learys":          ("Local Chain", "Chain", "Local Chain", "Casual Dining Restaurant"),
    # ── UAE / GCC local bakery & café chains ─────────────────────────────────
    "la brioche":        ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "mister baker":      ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "bakerist":          ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "chicha bakehouse":  ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "hot bread":         ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "gerard":            ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "magnolia bakery":   ("Local Chain", "Chain", "Local Chain", "Bakery & Café"),
    "karak house":       ("Local Chain", "Chain", "Local Chain", "Café / Coffee Shop"),
    "taza":              ("Local Chain", "Chain", "Local Chain", "Café / Coffee Shop"),
    "caffè nero":        ("Local Chain", "Chain", "Local Chain", "Coffee Shop"),
}


def pre_classify(name: str):
    """
    Returns (classification, outlet_type, chained_outlet_type, type_of_restaurant)
    if brand is in our known lookup, else None (requires API call).
    """
    low = name.lower()
    for fragment, result in KNOWN_MNC.items():
        if fragment in low:
            return result
    for fragment, result in KNOWN_LOCAL_CHAIN.items():
        if fragment in low:
            return result
    return None


# ── Output column names (matching final_output.xlsx expectations) ───────────
COL_CLASSIFICATION    = "classification"
COL_OUTLET_TYPE       = "UAE_Outlet Type"
COL_CHAINED_TYPE      = "UAE_Chained Outlet Type"
COL_TYPE_OF_REST      = "UAE_Type of Restaurants"
COL_CONFIDENCE        = "confidence"
COL_REASONING         = "reasoning"
COL_MATCHED_UAE_NAME  = "matched_uae_name"

# ── Type of Restaurant options (for the prompt) ──────────────────────────────
RESTAURANT_TYPES = [
    "Quick-Service Restaurant (QSR)",
    "Fast-Casual Restaurant",
    "Casual Dining Restaurant",
    "Fine Dining Restaurant",
    "Café / Coffee Shop",
    "Bakery & Café",
    "Patisserie / Dessert Shop",
    "Food Court Stall",
    "Cloud Kitchen",
    "Catering Service",
    "Bar & Grill",
]
