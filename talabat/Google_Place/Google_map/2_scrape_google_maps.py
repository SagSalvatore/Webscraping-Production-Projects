"""
2_scrape_google_maps.py
--------------------------
Production DIY Google Maps scraper: async Playwright + Oxylabs UAE
residential proxy, concurrent execution, scroll-until-end-of-list,
and the SAME strict entity-matching logic used in the Apify pipeline
(ordered-prefix core-token match) so results are held to one
consistent standard across both scraping methods.

Phase 1: search "{Name} UAE Restaurant" -> scroll results feed until
         the "You've reached the end of the list." marker appears (or
         a safety cap is hit), collecting every {name, url} result.
Phase 2: visit each result's place page -> extract address, phone
         (normalized to +971 international format), website, maps_url.

Matching: every result is scored against the Talabat name using the
same core-token ordered-prefix logic as 9_merge_retest_strict.py.
Exact matches go to the main directory; everything else goes to a
separate "other UAE entities" file (bonus data, not merged).

Output: JSON + CSV for Matched, Not Found, and Other Entities, each
carrying branch_id / area_id for easy mapping back to the Talabat file.
"""
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)

import os
OXY_USER = os.getenv("OXYLABS_USERNAME")
OXY_PASS = os.getenv("OXYLABS_PASSWORD")
OXY_HOST = os.getenv("OXYLABS_PROXY_HOST")
OXY_PORT = os.getenv("OXYLABS_PROXY_PORT")
OXY_COUNTRY = os.getenv("OXYLABS_COUNTRY", "ae")

PROXY = {
    "server": f"http://{OXY_HOST}:{OXY_PORT}",
    "username": f"customer-{OXY_USER}-country-{OXY_COUNTRY.upper()}",
    "password": OXY_PASS,
}

GOOGLE_MAP_DIR = Path(__file__).parent
BATCH_INPUT = GOOGLE_MAP_DIR / "output" / "batch_input" / "diy_batch_01_input.json"
OUT_DIR = GOOGLE_MAP_DIR / "output" / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONCURRENCY = 6
MAX_SCROLLS = 8
BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

# Mutable single-element list used as a simple thread/task-safe-enough
# shared counter across concurrent contexts (real-time bandwidth tracking,
# so we can compare directly against the 907MB/100-name baseline).
BANDWIDTH_BYTES = [0]


async def track_bandwidth(response):
    try:
        body = await response.body()
        BANDWIDTH_BYTES[0] += len(body)
    except Exception:
        pass

STOPWORDS = {
    "restaurant", "restaurants", "cafe", "cafeteria", "kitchen", "grill",
    "house", "eatery", "bistro", "diner", "cuisine", "cuisines", "food",
    "foods", "corner", "shop", "llc", "branch", "and", "the", "of", "by",
    "bar", "co", "company", "sweets", "bakery", "snack", "snacks",
}


def core_token_list(name: str) -> list:
    if not name:
        return []
    name = name.split("|")[0]
    name = re.sub(r"[^\x00-\x7f]", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return [w for w in name.split() if w and w not in STOPWORDS]


def is_exact_entity_match(talabat_name: str, google_title: str) -> bool:
    t = core_token_list(talabat_name)
    g = core_token_list(google_title)
    if not t or not g or len(g) < len(t):
        return False
    return g[:len(t)] == t


def normalize_phone(phone: str) -> str:
    """UAE local format ('02 645 8000', '050 405 7320') -> international
    ('+971 2 645 8000', '+971 50 405 7320'). Already-international numbers
    pass through unchanged."""
    if not phone:
        return None
    phone = phone.strip()
    if phone.startswith("+"):
        return phone
    if phone.startswith("00"):
        return "+" + phone[2:].strip()
    if phone.startswith("0"):
        return f"+971 {phone[1:].strip()}"
    return f"+971 {phone}"


def force_english(url: str) -> str:
    if "hl=" in url:
        return re.sub(r"hl=[^&]*", "hl=en", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}hl=en"


async def block_heavy_resources(route):
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def search_places(page, query: str) -> list:
    """Phase 1: search + scroll until end-of-list marker or safety cap."""
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}?hl=en"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return []
    await page.wait_for_timeout(2000)

    try:
        await page.wait_for_selector('div[role="feed"]', timeout=10000)
    except Exception:
        # Possible direct single-result redirect straight to a place page
        if "/maps/place/" in page.url:
            title_el = await page.query_selector("h1")
            name = await title_el.inner_text() if title_el else query
            return [{"name": name, "url": page.url}]
        return []

    seen_count = 0
    for _ in range(MAX_SCROLLS):
        links = await page.query_selector_all('div[role="feed"] a[aria-label]')
        current_count = len(links)

        # End-of-list marker (primary: text content; fallback: class name,
        # since Google's auto-generated classes can change between builds)
        end_marker = await page.query_selector('span:has-text("reached the end of the list")')
        if not end_marker:
            end_marker = await page.query_selector("span.HlvSq")

        if end_marker or current_count == seen_count:
            break  # either genuinely done, or scrolling isn't loading anything new

        seen_count = current_count
        feed = await page.query_selector('div[role="feed"]')
        if feed:
            await feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
        await page.wait_for_timeout(1500)

    links = await page.query_selector_all('div[role="feed"] a[aria-label]')
    results = []
    for link in links:
        name = await link.get_attribute("aria-label")
        href = await link.get_attribute("href")
        if name and href:
            results.append({"name": name, "url": href})
    return results


async def scrape_place_detail(page, place_url: str) -> dict:
    """Phase 2: visit a place URL, extract address/phone/website/maps_url."""
    place_url = force_english(place_url)
    try:
        await page.goto(place_url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return {"address": None, "phone": None, "website": None, "maps_url": place_url}
    await page.wait_for_timeout(2000)

    result = {"address": None, "phone": None, "website": None, "maps_url": page.url}

    addr_el = await page.query_selector('button[data-item-id="address"]')
    if addr_el:
        text = await addr_el.get_attribute("aria-label")
        result["address"] = re.sub(r"^Address:\s*", "", text).strip() if text else None

    phone_el = await page.query_selector('button[data-item-id^="phone:tel:"]')
    if phone_el:
        text = await phone_el.get_attribute("aria-label")
        raw_phone = re.sub(r"^Phone:\s*", "", text).strip() if text else None
        result["phone"] = normalize_phone(raw_phone)

    site_el = await page.query_selector('a[data-item-id="authority"]')
    if site_el:
        result["website"] = await site_el.get_attribute("href")

    return result


async def process_one_entry(browser, entry: dict, semaphore: asyncio.Semaphore) -> dict:
    """
    Bandwidth fix: only visit detail pages for results that EXACT-MATCH the
    Talabat name (each full page load costs ~4.5MB, dominated by Google
    Maps' own JS bundle -- confirmed via diag_bandwidth.py). Non-matching
    "other entity" results are kept as name+url ONLY, from the search
    results feed we already loaded -- zero extra detail-page bandwidth.

    Also fixes a prior bug: a real local chain with multiple exact-match
    branches (e.g. 3 "Ami Bakehouse" locations) now gets ALL of them
    scraped, not just the first one found.
    """
    async with semaphore:
        context = await browser.new_context(locale="en-US")
        await context.route("**/*", block_heavy_resources)
        page = await context.new_page()
        page.on("response", lambda r: asyncio.ensure_future(track_bandwidth(r)))

        try:
            search_results = await search_places(page, entry["query_string"])

            matched_list = []
            others = []
            for r in search_results:
                if is_exact_entity_match(entry["restaurant_name_clean"], r["name"]):
                    matched_list.append(r)
                else:
                    others.append(r)

            matched_details = []
            for m in matched_list:
                detail = await scrape_place_detail(page, m["url"])
                matched_details.append({**m, **detail})

            # No detail-page visits for non-matches -- name+url only, from
            # the already-loaded search results feed.
            other_details = others

        finally:
            await context.close()

        return {
            "entry": entry,
            "matched_list": matched_details,
            "other_entities": other_details,
            "total_results_found": len(search_results),
        }


async def main():
    entries = json.loads(BATCH_INPUT.read_text(encoding="utf-8"))
    print(f"Processing {len(entries)} Talabat entries with concurrency={CONCURRENCY}...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=PROXY)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        tasks = [process_one_entry(browser, e, semaphore) for e in entries]
        results = []
        done = 0
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results.append(r)
            done += 1
            status = f"MATCHED x{len(r['matched_list'])}" if r["matched_list"] else "NOT FOUND"
            mb = BANDWIDTH_BYTES[0] / 1024 / 1024
            print(f"  [{done}/{len(entries)}] {r['entry']['restaurant_name_clean']!r:<40} -> {status} ({r['total_results_found']} results)  [{mb:.1f} MB so far]")

        await browser.close()

    # -- Build output rows -----------------------------------------------------
    matched_rows, not_found_rows, other_rows = [], [], []
    for r in results:
        e = r["entry"]
        base = {
            "Talabat_Restaurant_Name": e["restaurant_name_clean"],
            "Branch_ID": e["branch_id"],
            "Restaurant_ID": e["restaurant_id"],
            "Area_ID": e.get("area_id"),
            "Area_Name": e.get("area_name"),
            "Talabat_Branch_Count": e["talabat_branch_count"],
        }
        if r["matched_list"]:
            for m in r["matched_list"]:
                matched_rows.append({
                    **base,
                    "Google_Business_Name": m["name"],
                    "Address": m.get("address"),
                    "Phone": m.get("phone"),
                    "Website": m.get("website"),
                    "Google_Maps_URL": m.get("maps_url"),
                })
        else:
            not_found_rows.append(base)

        # No detail data for others anymore -- name/url only, zero extra bandwidth
        for o in r["other_entities"]:
            other_rows.append({
                "Discovered_Via_Search": e["query_string"],
                "Google_Business_Name": o["name"],
                "Google_Maps_URL": o["url"],
            })

    batch_num = json.loads((GOOGLE_MAP_DIR / "output" / "checkpoints" / "diy_scraper_checkpoints.json").read_text(encoding="utf-8"))["batches"][-1]["batch_number"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name, rows in [("Matched", matched_rows), ("NotFound", not_found_rows), ("OtherEntities", other_rows)]:
        json_path = OUT_DIR / f"DIY_Batch{batch_num:02d}_{name}_{ts}.json"
        csv_path = OUT_DIR / f"DIY_Batch{batch_num:02d}_{name}_{ts}.csv"
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        if rows:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        print(f"Saved {name}: {len(rows)} rows -> {json_path.name} / {csv_path.name}")

    total_mb = BANDWIDTH_BYTES[0] / 1024 / 1024

    # Update checkpoint
    cp_path = GOOGLE_MAP_DIR / "output" / "checkpoints" / "diy_scraper_checkpoints.json"
    cp = json.loads(cp_path.read_text(encoding="utf-8"))
    cp["batches"][-1]["status"] = "completed"
    cp["batches"][-1]["completed_at"] = datetime.now(timezone.utc).isoformat()
    cp["batches"][-1]["matched_count"] = len(matched_rows)
    cp["batches"][-1]["not_found_count"] = len(not_found_rows)
    cp["batches"][-1]["bandwidth_mb"] = round(total_mb, 1)
    cp_path.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Total entries       : {len(entries)}")
    print(f"Matched (rows)      : {len(matched_rows)}")
    print(f"Not Found           : {len(not_found_rows)}")
    print(f"Other entities      : {len(other_rows)}")
    print(f"Total bandwidth     : {total_mb:.1f} MB  ({total_mb/len(entries):.2f} MB/name)")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
