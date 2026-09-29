"""Shared loaders and normalisation for listing comparison.

Every comparison script imports from here so the definition of "the existing
universe" and "a normalised name" is stated once, not re-invented per script.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # talabat/
RI = ROOT / "Restaurant Identifier" / "data"
URLS = ROOT / "data" / "urls"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)

# ---- sources -------------------------------------------------------------
EXISTING_CONFIRMED = RI / "restaurants_confirmed.jsonl"      # run-1, 15,773
NEW_CONFIRMED = RI / "restaurants_confirmed_run2.jsonl"      # run-2
NEW_RAW = URLS / "talabat_restaurant_urls_run2.jsonl"        # run-2 pre-classification
EXISTING_RAW = URLS / "talabat_restaurant_urls.jsonl"        # run-1 pre-classification
EXPORT = ROOT / "export" / "talabat_export.json"             # production export


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---- name normalisation --------------------------------------------------
# Talabat appends the branch location to the brand ("Curry Chatti, Muwaileh
# Commercial"), so a raw string compare would call every branch a new brand.
# Stripping the trailing location is what makes brand-level matching work.
_LEGAL = re.compile(
    r"\b(l\.?l\.?c|llc|fz[ce]?|fzco|trading|restaurant|restaurants|cafeteria|"
    r"cafe|café|kitchen|foodstuff|est|establishment|co|company|group)\b", re.I)
_PUNCT = re.compile(r"[^\w\s]", re.U)
_WS = re.compile(r"\s+")


def norm_name(name: str | None, *, strip_branch: bool = True) -> str:
    """Lowercased, punctuation-free brand key."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    if strip_branch:
        # "Brand, Branch Location" and "Brand - Branch Location"
        s = re.split(r"\s*[,|]\s*| - ", s)[0]
    s = _PUNCT.sub(" ", s.lower())
    s = _LEGAL.sub(" ", s)
    return _WS.sub(" ", s).strip()


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Metres between two points."""
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return float("inf")
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geo_key(lat, lon, precision: int = 3) -> tuple | None:
    """Coarse grid cell for blocking. 3dp ~ 110m, enough to bucket candidates
    without comparing every pair (15,773 x 10,597 = 167M comparisons)."""
    try:
        return (round(float(lat), precision), round(float(lon), precision))
    except (TypeError, ValueError):
        return None


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
    import csv
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = fields or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_universe(existing_paths=None, new_path=None):
    """Returns (existing_confirmed, new_confirmed) with normalised name keys.

    THE EXISTING SIDE MUST GROW EVERY CYCLE. The defaults are run-1 only, so a
    September run left unchanged would report all 7,129 August restaurants as
    brand-new. `existing_paths` takes a LIST so each cycle folds the previous
    one in; duplicates across files are collapsed on branch_id.
    """
    paths = existing_paths or [EXISTING_CONFIRMED]
    old, seen = [], set()
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise SystemExit(f"existing-universe file not found: {p}")
        for r in read_jsonl(p):
            b = r.get("branch_id")
            if b in seen:
                continue
            seen.add(b)
            old.append(r)
    new = read_jsonl(Path(new_path) if new_path else NEW_CONFIRMED)
    for r in old + new:
        r["_name_key"] = norm_name(r.get("name"))
    return old, new
