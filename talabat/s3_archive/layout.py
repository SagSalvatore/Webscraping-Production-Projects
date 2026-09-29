"""Where every Talabat file belongs in s3://rii-data-dump - the ONE definition.

inventory.py, upload_to_s3.py, verify_s3.py and prune_local.py all import this,
so a file can never be inventoried under one key and uploaded under another.

LAYERS follow the pipeline, not the folder tree:

  bronze/   exactly what was captured - zone crawls, raw menus, raw Google /
            Serper / Apify / LLM responses. Re-fetching costs money or is
            impossible, so this is the layer worth keeping longest.
  silver/   everything made from bronze by our own rules - sanitized menus,
            std_term and ingredient mapping, kNN + manual review decisions,
            classification, area and address repair, validation reports.
  gold/     what was handed over - the unified deliverable, the monthly Tech
            exports, the logo packages, and their manifests.
  reference/ the controlled vocabularies every month is mapped against
            (area_list.csv, the ingredient taxonomy, std_term taxonomy). Not
            month-partitioned; versioned by S3 object version.
  ops/      logs and checkpoints. Kept only so a past run can be explained.

KEY SHAPE

  talabat/<layer>/<month>/<stage>/<path-under-its-source-folder>
  talabat/reference/<name>
  e.g. talabat/gold/2026-09/unified/talabat_unified_202609.jsonl
       talabat/bronze/2026-09/03_menus_raw/refresh_202609/scrape/menu_items.jsonl

Month comes from the file's own name or folder (202609, sept, september, the
refresh_202609 folder), never from its mtime - a file copied last week can hold
July data. Anything genuinely cross-month goes to <layer>/_cross_month/.

ACTIONS (what prune_local.py may do once S3 has a verified copy)

  archive        upload, then it may be deleted locally
  archive_keep   upload, but KEEP on disk - the pipelines read it every month
                 (vocabularies, caches that cost credits to rebuild, the
                 current month's deliverable)
  covered        a duplicate of something already archived (the loose logo
                 folders inside their zip) - deletable once the cover is verified
  skip           code, tests, junk - never uploaded, never deleted by us
  review         no rule matched; a human decides before anything happens
"""
import re
from pathlib import PurePosixPath

BUCKET = "rii-data-dump"                      # arn:aws:s3:::rii-data-dump
REGION = "us-west-2"                          # US West (Oregon)
ROOT_PREFIX = "talabat"
# The month whose deliverable must stay on disk: next month's build reads it as
# its base. Bump this after each handover, re-run inventory.py, and last
# month's file becomes deletable (restore_from_s3.py brings it back).
CURRENT_MONTH = "202609"

SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".git", ".idea", ".vscode",
             "node_modules", ".venv", "venv", "env", ".ruff_cache", "site-packages"}
CODE_EXT = {".py", ".pyc", ".pyi", ".ipynb", ".md", ".txt", ".ini", ".cfg", ".toml",
            ".yaml", ".yml", ".sql", ".sh", ".bat", ".ps1", ".html", ".js", ".css",
            ".gitignore", ".example"}
DATA_EXT = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".xlsx", ".xls", ".xlsm",
            ".parquet", ".zip", ".gz", ".bak", ".db", ".sqlite", ".dump", ".png",
            ".jpg", ".jpeg", ".svg", ".webp", ".pdf"}

# '\b' does NOT break on '_', so r"\baugust\b" never matches 'August_menu'.
# These lookarounds treat any non-letter, including '_', as the boundary.
def _word(p):
    return re.compile(rf"(?<![a-z]){p}(?![a-z])", re.I)


MONTH_PATTERNS = (
    (re.compile(r"20(\d{2})(0[1-9]|1[0-2])"), None),                 # 202609, 20260619
    (_word(r"jul(y)?"), "2026-07"),
    (_word(r"aug(ust)?"), "2026-08"),
    (_word(r"sep(t|tember)?"), "2026-09"),
    (_word(r"jun(e)?"), "2026-06"),
)
CROSS = "_cross_month"

# A cohort folder whose files carry no month token of their own. Only for
# folders that ran for exactly ONE cycle - never a guess:
#   menu/           the first (June) cycle - the menu tracker
#   August_menu/    the August batch            August_classification/ likewise
#   menu_refresh/   the July->August refresh (July_menu_update.json shipped with
#                   the August deliverable); the 202609 cycle has its own
#                   refresh_202609/ folder, which the numeric pattern catches
#   export/         July's deliverable (talabat_export.json)
#   september/      the September cycle
#   area_classification/, address_audit/  the September location repair
#   images/         July ran unprefixed; later cohorts prefix every file
FOLDER_MONTH = {
    "menu": "2026-06",
    "August_menu": "2026-08",
    "August_classification": "2026-08",
    "menu_refresh": "2026-08",
    "export": "2026-07",
    "september": "2026-09",
    "area_classification": "2026-09",
    "address_audit": "2026-09",
    "images": "2026-07",
}

# ---------------------------------------------------------------- rules
# (regex on the POSIX path relative to talabat/, layer, stage, action)
# First match wins, so put the specific ones first.
RULES = [
    # ---------------------------------------------------------- skip
    (r"^tests/", None, None, "skip"),
    (r"(^|/)(requirements|readme|notes|context)[^/]*$", None, None, "skip"),

    # ---------------------------------------------------------- reference
    (r"^area_classification/area_list\.csv$", "reference", "areas", "archive_keep"),
    (r"^August_menu/ingredients_export_02\.json$", "reference", "ingredients", "archive_keep"),
    (r"^menu/final_std_terms_taxonomy\.csv", "reference", "std_terms", "archive_keep"),
    (r"^menu_refresh/data/std_term_canonical_ingredients\.json$", "reference", "std_terms", "archive_keep"),
    (r"^unified/data/(known_branch_ids|arabic_fixes)\.json$", "reference", "unified", "archive_keep"),

    # ---------------------------------------------------------- gold
    # Only the CURRENT month's deliverable stays on disk - next month's build
    # reads it as its base. Older months and every .bak are one restore away.
    (rf"^unified/data/talabat_unified_{CURRENT_MONTH}\.jsonl$", "gold", "unified", "archive_keep"),
    (r"^unified/data/talabat_unified_\d{6}\.jsonl$", "gold", "unified", "archive"),
    (r"^unified/data/talabat_unified_\d{6}\.jsonl\..*bak$", "gold", "unified/previous_versions", "archive"),
    (r"^unified/data/unified_(build_report|validation)[^/]*$", "gold", "unified", "archive"),
    (r"^unified/data/talabat_unified_\d{6}\.extra_validation\.json$", "gold", "unified", "archive"),
    (r"^september/data/September_export\.json$", "gold", "exports", "archive_keep"),
    (r"^(August_menu/data/August_export|september/data/September_export)\.json\..*bak$",
     "gold", "exports/previous_versions", "archive"),
    (r"^August_menu/data/August_export\.json$", "gold", "exports", "archive"),
    # their reports and side files (August_export_validation.json, ...)
    (r"^(August_menu/data/August_export|september/data/September_export)", "gold", "exports", "archive"),
    (r"^menu_refresh/data/[^/]*menu_update[^/]*$", "gold", "exports", "archive"),
    (r"^export/", "gold", "exports", "archive"),
    (r"^images/data/[^/]*logos_\d{6}\.zip$", "gold", "logos", "archive"),
    (r"^images/data/[^/]*logos_manifest\.[^/]*$", "gold", "logos", "archive_keep"),
    (r"^images/data/[^/]*_logos/", "gold", "logos", "covered"),
    (r"^images/data/downloaded/", "gold", "logos", "covered"),

    # ---------------------------------------------------------- bronze
    (r"^listing_scraper/", "bronze", "01_zones", "archive"),
    (r"^september/data/(zone_snapshot|zone_delta|area_probe)[^/]*$", "bronze", "01_zones", "archive"),
    (r"^data/urls/", "bronze", "02_listings", "archive"),
    (r"^url_collector/", "bronze", "02_listings", "archive"),
    (r"^Restaurant Identifier/", "bronze", "02_listings", "archive"),
    (r"^(talabat_2_0|talabat-selenium)/", "bronze", "02_listings", "archive"),
    (r"^map_version2/", "silver", "05_classification", "archive"),
    (r"^september/data/(newly_found|newly_listed|new_listings_split|known_branch_ids)[^/]*$",
     "bronze", "02_listings", "archive"),
    (r"^August_menu/data/menu_items\.jsonl$", "bronze", "03_menus_raw", "archive"),
    (r"^menu_refresh/data/refresh_\d{6}/scrape/", "bronze", "03_menus_raw", "archive"),
    (r"^menu_refresh/data/refresh_\d{6}/newitems/menu_items\.jsonl$", "bronze", "03_menus_raw", "archive"),
    (r"^september/data/menus/menu_items\.jsonl$", "bronze", "03_menus_raw", "archive"),
    (r"^(menu|August_menu)/data/[^/]*raw[^/]*$", "bronze", "03_menus_raw", "archive"),
    (r"serper[^/]*cache[^/]*\.json$", "bronze", "04_enrichment_raw", "archive_keep"),
    (r"translat[^/]*cache[^/]*\.json$", "bronze", "04_enrichment_raw", "archive_keep"),
    (r"(llm|openai|gpt|tavily)[^/]*cache[^/]*\.json$", "bronze", "04_enrichment_raw", "archive_keep"),
    (r"^(apify|Google_Place|tavily)/", "bronze", "04_enrichment_raw", "archive"),
    (r"^August_classification/data[^/]*/(google|maps|places)[^/]*$", "bronze", "04_enrichment_raw", "archive"),
    (r"^september/data/september_google_details[^/]*$", "bronze", "04_enrichment_raw", "archive"),
    (r"^images/data/[^/]*images_confirmed\.jsonl$", "bronze", "05_images_raw", "archive"),
    (r"^images/data/[^/]*(logo_targets|images_failed)[^/]*$", "bronze", "05_images_raw", "archive"),

    # ---------------------------------------------------------- ops
    # before the catch-alls, or a checkpoint lands in 99_other
    (r"\.log$", "ops", "logs", "archive"),
    (r"checkpoint[^/]*\.json$", "ops", "checkpoints", "archive"),
    (r"^Postgre/", "ops", "postgres", "archive"),

    # ---------------------------------------------------------- June cycle
    # talabat/menu/ is the FIRST cycle (June 2026): the menu tracker's own
    # snapshots, deltas, classification and exports. Superseded by August_menu
    # and menu_refresh, but it is the only copy of that month.
    (r"^menu/data/snapshots/", "bronze", "03_menus_raw", "archive"),
    (r"^menu/data/baseline/", "silver", "09_delta", "archive"),
    (r"^menu/data/reports/", "silver", "09_delta", "archive"),
    (r"^menu/data/entity_resolution/", "silver", "05_classification", "archive"),
    (r"^menu/data/exports/", "gold", "exports", "archive"),
    (r"^menu/classification/", "silver", "03_std_terms", "archive"),
    (r"^menu/[^/]*\.(xlsx|xls|csv)$", "silver", "03_std_terms/review", "archive"),

    # ---------------------------------------------------------- our own output
    (r"^s3_archive/", None, None, "skip"),
    (r"^docs/", None, None, "skip"),
    (r"\.py\.[^/]*bak$", None, None, "skip"),

    # ---------------------------------------------------------- reviewed labels
    (r"^August_menu/data/menu_items_FINAL_reviewed\.jsonl$", "silver", "03_std_terms", "archive"),
    (r"^August_menu/[^/]*FINALIZED[^/]*\.(ndjson|jsonl|json)$", "silver", "04_ingredients", "archive"),
    (r"^August_menu/data/sanitize_mapping\.csv$", "silver", "02_sanitize", "archive"),
    (r"^August_menu/data/restaurant_status\.jsonl$", "bronze", "03_menus_raw", "archive"),
    (r"^unified/data/label_conflict[^/]*$", "silver", "03_std_terms", "archive"),
    (r"^menu/master_mapping\.parquet$", "silver", "03_std_terms", "archive"),
    (r"^August_menu/data/(translate_mapping|sanitize_exceptions|arabic_location_translations|"
     r"area_city_cache|removed_|sanitize_report|mapping_report|final_review_report)", "silver", "02_sanitize", "archive"),
    (r"^August_menu/data/(august_verified_locations|location_duplicate_report|august_brand_location_summary)",
     "silver", "05_classification", "archive"),
    (r"^images/data/[^/]*(logo_download_failed|image_match_summary)[^/]*$", "bronze", "05_images_raw", "archive"),
    (r"^unified/data/july_city_fixes\.json$", "silver", "06_areas", "archive"),
    (r"^data/brand_locations/", "bronze", "04_enrichment_raw", "archive"),
    (r"^september/serve_cuisines\.csv$", "silver", "05_classification", "archive"),

    # root-level working files (Data_menus.xlsx, CITY-URLS.csv, ...)
    (r"^[^/]+\.(xlsx|xls|xlsm|csv|json|jsonl)(\.[^/]*bak)?$", "silver", "00_working_files", "archive"),

    # ---------------------------------------------------------- silver
    (r"^listing_comparison/", "silver", "01_listing_compare", "archive"),
    (r"^september/data/september_new_listings[^/]*$", "silver", "01_listing_compare", "archive"),
    (r"^(sanitization|TAB_MENU_VALIDATION)/", "silver", "02_sanitize", "archive"),
    (r"menu_items_(clean|final|shipped)\.jsonl$", "silver", "02_sanitize", "archive"),
    (r"^menu_refresh/data/refresh_\d{6}/(newitems|sanitize)/", "silver", "02_sanitize", "archive"),
    (r"^september/data/menus/", "silver", "02_sanitize", "archive"),
    (r"std_term", "silver", "03_std_terms", "archive"),
    (r"^menu_refresh/data/refresh_\d{6}/stdterm/", "silver", "03_std_terms", "archive"),
    (r"(review|finalis|finaliz|manual)[^/]*\.(xlsx|csv)$", "silver", "03_std_terms/review", "archive"),
    (r"^(ingredients|August_menu/data/ingredient)", "silver", "04_ingredients", "archive"),
    (r"ingredient[^/]*\.(json|jsonl|csv)$", "silver", "04_ingredients", "archive"),
    (r"^August_classification/", "silver", "05_classification", "archive"),
    (r"^september/data/(sept_chain_ids|sept_restaurants|brand_collisions|collisions)[^/]*$",
     "silver", "05_classification", "archive"),
    (r"^area_classification/", "silver", "06_areas", "archive"),
    (r"^september/data/september_area[^/]*$", "silver", "06_areas", "archive"),
    (r"^unified/data/unified_\d{6}_area[^/]*$", "silver", "06_areas", "archive"),
    (r"^address_audit/", "silver", "07_addresses", "archive"),
    (r"^unified/(data/unified_\d{6}_|review/)", "silver", "08_validation", "archive"),
    (r"^menu_refresh/data/refresh_\d{6}/(delta|report)", "silver", "09_delta", "archive"),
    (r"^menu_refresh/data/", "silver", "09_delta", "archive"),
    (r"^september/data/", "silver", "05_classification", "archive"),
    (r"^(menu|August_menu|unified|images|supabase|data)/", "silver", "99_other", "review"),
]
COMPILED = [(re.compile(p, re.I), lay, st, act) for p, lay, st, act in RULES]


def month_of(rel_path):
    """The month the DATA is about, read from its own path. Never mtime."""
    for rx, fixed in MONTH_PATTERNS:
        m = rx.search(rel_path)
        if m:
            return fixed or f"20{m.group(1)}-{m.group(2)}"
    top = rel_path.split("/")[0]
    return FOLDER_MONTH.get(top, CROSS)


def is_junk(rel_path):
    parts = PurePosixPath(rel_path).parts
    return any(p in SKIP_DIRS for p in parts)


def classify(rel_path, size=0):
    """-> (layer, stage, month, action, reason). rel_path is POSIX, relative to talabat/."""
    p = rel_path.replace("\\", "/")
    name = PurePosixPath(p).name
    ext = PurePosixPath(p).suffix.lower()
    if is_junk(p):
        return None, None, None, "skip", "build junk / virtualenv"
    if ext in CODE_EXT and ext not in (".json", ".csv"):
        return None, None, None, "skip", "code or documentation"
    if ext not in DATA_EXT:
        return None, None, None, "skip", f"not a data file ({ext or 'no extension'})"
    for rx, layer, stage, action in COMPILED:
        if rx.search(p):
            if action == "skip":
                return None, None, None, "skip", f"rule {rx.pattern}"
            return layer, stage, month_of(p), action, f"rule {rx.pattern}"
    return None, None, month_of(p), "review", "no rule matched"


def s3_key(rel_path, layer, stage, month):
    """talabat/<layer>/<month>/<stage>/<path under its source folder>."""
    p = rel_path.replace("\\", "/")
    if layer == "reference":
        return f"{ROOT_PREFIX}/reference/{stage}/{PurePosixPath(p).name}"
    return f"{ROOT_PREFIX}/{layer}/{month}/{stage}/{p}"
