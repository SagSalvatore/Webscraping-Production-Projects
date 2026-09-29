"""
build_output.py - stage 3: the deliverable, in chain_uk.csv's row order.

    output/UK_chain_details.xlsx   sheet "UK Chains" - the 7 requested columns
                                   sheet "Evidence"  - sources and checks behind every value
                                   sheet "Method"    - how each column was derived
    output/UK_chain_details.json   the 7 requested columns per brand

Descriptive columns come from research.py (Tavily), Number of Outlets from
apify_outlets.py (Google Maps). A value that failed verification is left
blank, never guessed.

    python build_output.py
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config

R = config.R
COUNT_COL = ("Number of Outlets*" if config.REGION == "uk"
             else f"Number of Outlets ({R.get('scope_label') or R['label']})*")
# GCC: the client's Outlet Type is a format, kept as such (decided 2026-09-28)
FORMAT_MODE = R.get("outlet_type_mode") == "format"
TYPE_COL = "Outlet Type (Format)" if FORMAT_MODE else "Outlet Type (Regional/International)"
REGION_IS_GCC = config.REGION == "gcc"
COLUMNS = ["Restaurant Name", "Parent Company", "Website", "Headquarters", "Cuisine/Concept", TYPE_COL, COUNT_COL]


def outlet_format(cuisine: str | None) -> str | None:
    """The client's GCC vocabulary, from the verified cuisine label."""
    if not cuisine:
        return None
    if cuisine in ("Cafe", "Bakery", "Doughnuts", "Cookies", "Pretzels"):
        return "Café/Bakery"
    if cuisine in ("Desserts", "Ice cream"):
        return "Dessert/QSR"
    if cuisine in ("Casual dining", "Steakhouse", "Grill", "Pub", "Pub & carvery", "Carvery", "Bar"):
        return "Casual Dining"
    return "QSR"
XLSX = config.OUTPUT_DIR / f"{R['output_stem']}.xlsx"
JSON_OUT = config.OUTPUT_DIR / f"{R['output_stem']}.json"
SOURCE_LABEL = {"report": "Store-locator location report",
                "published": "Company / press published figure",
                "web": "Web / company figure (number verified in the cited source)",
                "region_sum": f"Sum of per-country figures ({R.get('scope_label', '')} countries)",
                "aggregate": f"Stated {R.get('scope_label', '')} total (company / press)",
                "official": "Brand's official website (counted by the project team)"}


def parse_estimate(v) -> tuple[int, int] | None:
    """'~13,706' -> (13706, 13706); '~1,000–1,150' -> (1000, 1150)."""
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", str(v or ""))]
    return (min(nums), max(nums)) if nums else None


def compare_to_estimate(ours: int | None, est: tuple[int, int] | None) -> tuple[float | None, str | None]:
    """Signed difference from the client's estimate (0 inside a range), and its band."""
    if ours is None or not est:
        return None, None
    lo, hi = est
    diff = 0.0 if lo <= ours <= hi else ((ours - lo) / lo if ours < lo else (ours - hi) / hi)
    band = "Match (within 10%)" if abs(diff) <= 0.10 else "Close (10-20%)" if abs(diff) <= 0.20 else "Check (over 20%)"
    return round(diff * 100, 1), band


def _domain(v) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/")[0]
    return s[4:] if s.startswith("www.") else s


def _city(v) -> str:
    return str(v or "").split(",")[0].strip().lower()


def _jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


load_rows = config.load_rows


_OWNER_SUFFIX = {"plc", "ltd", "limited", "inc", "llc", "co", "company", "corporation", "corp", "the",
                 "group", "holdings", "holding", "sa", "sarl", "s", "a", "r", "l"}


def brand_site(site: str | None) -> str | None:
    """The brand's own site, not its investor pages: corporate.dominos.co.uk -> dominos.co.uk."""
    return re.sub(r"^(corporate|investors?|about|careers|group|ir)\.", "", site) if site else site


def cuisine_label(c: str | None) -> str | None:
    """Spelled as in the client's example ('Café'); the extraction vocabulary is ASCII."""
    return "Café" if c == "Cafe" else c


def owner_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    s = s.replace("&", " and ")
    return " ".join(w for w in re.findall(r"[a-z0-9!']+", s) if w not in _OWNER_SUFFIX)


def harmonise_parents(research: dict[int, dict]) -> dict[int, str]:
    """One ultimate owner, spelled one way, across the whole file.

    Rows are researched independently, so a group shows up at different
    levels: Slug & Lettuce -> 'Stonegate Pub Company' while Popworld ->
    'TDR Capital' via 'Stonegate Pub Company Ltd'. Every row's verified
    'owned via' link is collected and followed to the top, then each owner is
    written in its most frequent spelling ('Mitchells & Butlers plc' vs
    'Mitchells & Butlers'). Nothing here comes from outside the evidence.
    """
    up: dict[str, Counter] = defaultdict(Counter)
    for r in research.values():
        if r.get("parent_company") and r.get("owner_via"):
            if owner_key(r["owner_via"]) != owner_key(r["parent_company"]):
                up[owner_key(r["owner_via"])][r["parent_company"]] += 1
    parent_of = {k: c.most_common(1)[0][0] for k, c in up.items()}

    def top(name: str) -> str:
        seen = set()
        while owner_key(name) in parent_of and owner_key(name) not in seen:
            seen.add(owner_key(name))
            name = parent_of[owner_key(name)]
        return name

    final = {i: top(r["parent_company"]) for i, r in research.items() if r.get("parent_company")}
    spellings = Counter(final.values())
    by_key: dict[str, list[str]] = defaultdict(list)
    for n in spellings:
        by_key[owner_key(n)].append(n)
    display = {k: max(v, key=lambda n: (spellings[n], -len(n))) for k, v in by_key.items()}
    return {i: display[owner_key(n)] for i, n in final.items()}


def main() -> None:
    rows = load_rows()
    current = dict(rows)
    research = {r["row_index"]: r for r in _jsonl(config.DATA_DIR / "research.jsonl")
                if current.get(r["row_index"]) == r["input_name"]}
    outlets = {r["row_index"]: r for r in _jsonl(config.DATA_DIR / "outlets.jsonl")
               if current.get(r["row_index"]) == r["input_name"]}
    count_plan = json.loads((config.DATA_DIR / "count_plan.json").read_text(encoding="utf-8"))
    parents = harmonise_parents(research)
    overrides = json.loads(config.OVERRIDES.read_text(encoding="utf-8")) if config.OVERRIDES.exists() else {}

    main_rows, evidence_rows, json_rows, checks = [], [], [], []
    client = config.load_input()
    for idx, name in rows:
        r = research.get(idx, {})
        o = outlets.get(idx)
        cp = count_plan.get(name, {})
        # one source per brand, decided by count_plan.py
        if cp.get("source") in SOURCE_LABEL:
            n_out = cp.get("count")
            src_label = SOURCE_LABEL[cp["source"]]
            src_label += f", as of {cp['as_of']}" if cp.get("as_of") else ""
            src_url, src_text = cp.get("url"), cp.get("evidence")
        else:
            # a run that returned nothing was not a count (Bill's: the filtered search
            # matched no titles); only a run that saw the brand can say 0 (Bar + Block)
            n_out = o["uk_outlets"] if o and o.get("raw") and (o["uk_outlets"] or o.get("perm_closed")) else None
            src_label = f"Google Maps count via Apify, {date.today():%Y-%m-%d}" if o else "Apify count pending"
            src_url = src_text = None
        values = {
            "Restaurant Name": name,
            "Parent Company": overrides.get(name, {}).get("parent") or parents.get(idx),
            "Website": brand_site(overrides.get(name, {}).get("website") or r.get("website")),
            "Headquarters": overrides.get(name, {}).get("headquarters") or r.get("headquarters"),
            "Cuisine/Concept": cuisine_label(overrides.get(name, {}).get("cuisine") or r.get("cuisine_concept")),
            TYPE_COL: ((overrides.get(name, {}).get("format") or outlet_format(r.get("cuisine_concept"))) if FORMAT_MODE
                       else r.get("outlet_type") if r else None),
            COUNT_COL: (f"~{n_out}" if n_out else ("0" if n_out == 0 else None)),
        }
        if R.get("estimate_col"):
            given = client.iloc[idx]
            est = parse_estimate(given.get(R["estimate_col"]))
            diff, band = compare_to_estimate(n_out, est)
            values.update({"Client estimate": given.get(R["estimate_col"]),
                           "Difference vs estimate (%)": diff, "Agreement": band})
            checks.append({
                "row": idx + 1, "Restaurant Name": name,
                "Website (client)": given.get("Website"), "Website (ours)": values["Website"],
                "Website check": ("same" if _domain(given.get("Website")) == _domain(values["Website"])
                                  else "differs" if values["Website"] else "not verified"),
                "HQ (client)": given.get("Headquarters"), "HQ (ours)": values["Headquarters"],
                "HQ check": ("same" if _city(given.get("Headquarters")) == _city(values["Headquarters"])
                             else "differs" if values["Headquarters"] else "not verified"),
                "Outlet Type (client)": given.get("Outlet Type"), "Outlet Type (ours)": values[TYPE_COL],
                "Outlet Type basis (ours)": r.get("outlet_type_basis"),
                "Outlet Type check": ("same" if str(given.get("Outlet Type") or "").strip().lower()
                                      == str(values[TYPE_COL] or "").lower() else "differs"),
                "Cuisine / Concept (client)": given.get("Cuisine / Concept"), "Cuisine/Concept (ours)": values["Cuisine/Concept"],
                "Count (client)": given.get(R["estimate_col"]), "Count (ours)": n_out,
                "Count difference (%)": diff, "Count agreement": band, "Count source (ours)": src_label,
            })
        main_rows.append(values)
        json_rows.append({**values, COUNT_COL: n_out})
        evidence_rows.append({
            "row": idx + 1,
            "Restaurant Name": name,
            "Brand (as the brand writes it)": r.get("canonical_name"),
            "Parent Company": parents.get(idx),
            "Parent as found for this brand": r.get("parent_company"),
            "Owned via": r.get("owner_via"),
            "Parent Company sources": " | ".join(r.get("parent_company_urls", [])),
            "Website": r.get("website"),
            "Headquarters": r.get("headquarters"),
            "Headquarters sources": " | ".join(r.get("headquarters_urls", [])),
            "Cuisine/Concept": r.get("cuisine_concept"),
            "Outlet Type": r.get("outlet_type"),
            "Outlet Type basis": r.get("outlet_type_basis"),
            "Countries with outlets (confirmed)": ", ".join(c["country"] for c in r.get("countries_outside_uk", [])),
            "Country source": " | ".join(c["url"] for c in r.get("countries_outside_uk", [])[:3]),
            "Country passage (first)": (r.get("countries_outside_uk") or [{}])[0].get("passage"),
            "Crown dependencies (not counted)": ", ".join(c["country"] for c in r.get("uk_adjacent_territories", [])),
            "Planned/other (not counted)": ", ".join(r.get("countries_not_counted", [])),
            "Trading status": r.get("trading_status"),
            f"Number of Outlets ({R['label']})": n_out,
            "Outlet count source": src_label,
            "Outlet count source URL": src_url,
            "Outlet count evidence": src_text,
            "Outlet count note": cp.get("note"),
            "Google Maps count via Apify (open)": o.get("uk_outlets") if o else None,
            "Temporarily closed (excluded)": o.get("temp_closed") if o else None,
            "Matched by name": o.get("by_title") if o else None,
            "Matched by website only": o.get("by_domain_only") if o else None,
            "Name match, other website": o.get("title_other_domain") if o else None,
            "Raw places scraped": o.get("raw") if o else None,
            "Apify runs": o.get("runs") if o else None,
            "Capped runs": ", ".join(o.get("capped_runs", [])) if o else None,
            "Web size hint (not used)": r.get("uk_outlets_hint"),
            "Model confidence": r.get("confidence"),
            "Verification flags": " || ".join(r.get("flags", [])),
            # the model's free-text notes are unverified; the multi-country builds ship
            # only sourced fields (Autogrill's note named the wrong acquirer)
            **({} if R.get("countries") else {"Notes": r.get("notes")}),
        })

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    method = pd.DataFrame({"Column": COLUMNS + ["General"], "How it was filled": [
        f"Exactly as listed in {config.INPUT_FILE.name} (same order).",
        "Company, group, fund or family that ultimately owns the brand, from Tavily web search; kept only if a cited source names it.",
        "Official brand domain, found in the search evidence (delivery apps, Wikipedia, social media and directories rejected).",
        "Brand's global head office as City, Country (a foreign brand shows its home HQ, not the UK franchisee). Kept only if a cited source names the city.",
        "Short concept label derived from the cited sources.",
        "International = a cited source names at least one country outside the UK with an OPEN outlet (Ireland counts). Planned openings, exports and the Crown dependencies (Isle of Man, Jersey, Guernsey, Gibraltar) do not count. Otherwise Regional.",
        ("* UK outlets, concessions included, from one of three sources (named per row in Evidence): "
         "(1) a store-locator location report dated 2025-26 (ScrapeHero), used when it agrees with the independent web figure; "
         "(2) the brand's official website (Cake Box, Pizza Hut Delivery) or the company's published figure, for pub groups whose pubs trade under their own names and for Costa (stores only, "
         "excluding Costa Express self-serve machines); "
         f"(3) otherwise open outlets counted on Google Maps via Apify on {date.today():%d %b %Y} - a place counts when its name "
         "starts with the brand name or its Google website is the brand's official domain; closed places, offices and "
         "warehouses excluded; duplicates removed by place id and address. Calibration: Apify 80 vs report 81 (Itsu), "
         "47 vs 51 (Franco Manca)."),
        "Blank = the value could not be verified from a source. Nothing is filled from model memory.",
    ]})
    if config.REGION != "uk":
        method.loc[method["Column"] == "Outlet Type (Regional/International)", "How it was filled"] = (
            f"International = a cited source names at least one country outside the {R['label']} with an OPEN outlet, "
            f"or the brand's head office is outside the {R['label']}. Planned openings, exports and "
            f"{', '.join(sorted(R['adjacent']))} do not count. Otherwise Regional.")
        method.loc[method["Column"] == COUNT_COL, "How it was filled"] = (
            f"* {R['label']} outlets from one source per brand (named per row in Evidence): a store-locator location "
            "report dated 2025-26 (ScrapeHero / xmap) where it agrees with the independent web figure; otherwise the "
            "company / web figure whose number appears in the cited source. Compared with the client's estimate: "
            "Match = within 10%, Close = 10-20%, Check = over 20%.")
        method.loc[method["Column"] == "Headquarters", "How it was filled"] = (
            "Brand's global head office; US head offices as City, ST (the client's style), others as City, Country. "
            "Kept only if a cited source names the city.")
    if R.get("countries"):
        scope = R["scope_label"]
        if FORMAT_MODE:
            method.loc[method["Column"] == TYPE_COL, "How it was filled"] = (
                "Outlet format in the client's vocabulary, from the verified cuisine / concept: QSR; Café/Bakery "
                "(cafés, bakeries, doughnuts); Dessert/QSR (desserts, ice cream); Casual Dining (grills, steakhouses).")
        else:
            method.loc[method["Column"] == TYPE_COL, "How it was filled"] = (
                "International = a cited source names at least one country outside the brand's home country (the "
                "country of its head office) with an OPEN outlet. Planned openings and exports do not count. "
                "Otherwise Regional.")
        arabic = " (English or Arabic name)" if REGION_IS_GCC else ""
        method.loc[method["Column"] == COUNT_COL, "How it was filled"] = (
            f"* Outlets across the {scope} ({', '.join(R['countries'])}), one source per brand, named per row in "
            "Evidence: (1) the sum of per-country figures - a store-locator location report dated 2025-26 "
            "(ScrapeHero / xmap) where one exists, otherwise the company / press figure quoted in the cited source; "
            f"(2) a stated total for the whole {scope}, only where the source's scope is exactly the {scope}; "
            "(3) a company-published total where the per-country figures were stale or incomplete; "
            f"(4) open outlets counted on Google Maps via Apify on {date.today():%d %b %Y}, one run per country, "
            f"where web figures were missing, stale, partial or of the wrong scope - a place counts when its name "
            f"starts with the brand name{arabic}; closed places and offices excluded; duplicates removed by place id. "
            "Compared with the client's estimate: Match = within 10%, Close = 10-20%, Check = over 20%; the "
            "Evidence note explains each Check.")
        fixes = [f"{b}: {f}" for b, o in overrides.items() if isinstance(o, dict)
                 for f in ("parent", "website", "headquarters", "format") if o.get(f)]
        method.loc[method["Column"] == "General", "How it was filled"] = (
            "Blank = the value could not be verified from a source. Nothing is filled from model memory."
            + (f" Corrected by hand after checking the brand's own site or a cited source: {'; '.join(fixes)}."
               if fixes else ""))
    cols = COLUMNS + (["Client estimate", "Difference vs estimate (%)", "Agreement"] if R.get("estimate_col") else [])
    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        pd.DataFrame(main_rows, columns=cols).to_excel(xw, sheet_name=f"{R['label']} Chains", index=False)
        if checks:
            pd.DataFrame(checks).to_excel(xw, sheet_name="Client values vs ours", index=False)
        pd.DataFrame(evidence_rows).to_excel(xw, sheet_name="Evidence", index=False)
        method.to_excel(xw, sheet_name="Method", index=False)

    wb = load_workbook(XLSX)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E78")
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, col in enumerate(ws.columns, 1):
            width = max(len(str(c.value or "")) for c in col[:200])
            ws.column_dimensions[get_column_letter(i)].width = min(max(12, width + 2), 60)
    wb["Method"].column_dimensions["B"].width = 120
    for row in wb["Method"].iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(XLSX)

    JSON_OUT.write_text(json.dumps(json_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    filled = {c: sum(1 for r in main_rows if r[c]) for c in COLUMNS}
    print(f"{len(main_rows)} rows -> {XLSX.name}, {JSON_OUT.name}")
    for c, n in filled.items():
        print(f"  {c:<40} {n:>4}/{len(main_rows)}")
    if checks:
        bands = Counter(c["Count agreement"] or "no count" for c in checks)
        print("  count vs client estimate:", dict(bands))
        for k in ("Website check", "HQ check", "Outlet Type check"):
            print(f"  {k:<20}", dict(Counter(c[k] for c in checks)))


if __name__ == "__main__":
    main()
