import requests
import gzip
import io
import csv
import xml.etree.ElementTree as ET
from urllib.parse import urlparse



# Root sitemap indexes from robots.txt
ROOT_SITEMAPS = [
    "https://www.talabat.com/_sitemap/sitemap.xml.gz",
    "https://www.talabat.com/sitemap/sitemap.xml.gz",
]

# Output CSV file
OUTPUT_CSV = "talabat_sitemap_urls.csv"

# How deep to recurse into sitemap indexes
MAX_SITEMAP_DEPTH = 3

# Optional safety limits (set None to disable)
MAX_SITEMAPS = None          # e.g. 200
MAX_URLS = None              # e.g. 50000


# =======================
# HELPER FUNCTIONS
# =======================

def fetch_xml(url: str) -> bytes:
    """Fetch a .xml or .xml.gz sitemap and return raw XML bytes."""
    print(f"  Fetching: {url}")

    headers = {
        # Generic modern browser UA (do NOT pretend to be Googlebot)
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
    }

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    content = resp.content

    # Some .gz URLs actually return plain XML (start with '<?xml')
    # True gzip files start with magic bytes 0x1f 0x8b.
    if content[:2] == b"\x1f\x8b":
        # Real gzip -> decompress
        import gzip, io
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
            content = f.read()

    # Now content should be plain XML bytes
    return content


def parse_sitemap(xml_bytes: bytes):
    """
    Parse a sitemap or sitemap index.
    Returns:
      - url_locs: list of <url><loc> URLs
      - sitemap_locs: list of <sitemap><loc> child sitemap URLs
    """
    root = ET.fromstring(xml_bytes)

    # Default sitemap namespace
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    url_locs = []
    for url_node in root.findall("sm:url", ns):
        loc = url_node.find("sm:loc", ns)
        if loc is not None and loc.text:
            url_locs.append(loc.text.strip())

    sitemap_locs = []
    for sm_node in root.findall("sm:sitemap", ns):
        loc = sm_node.find("sm:loc", ns)
        if loc is not None and loc.text:
            sitemap_locs.append(loc.text.strip())

    return url_locs, sitemap_locs


def is_uae_url(url: str) -> bool:
    """Heuristic: URL path contains /uae/."""
    parsed = urlparse(url)
    return parsed.netloc.endswith("talabat.com") and "/uae/" in parsed.path


def is_dubai_url(url: str) -> bool:
    """Heuristic: UAE URL that also contains 'dubai' in path."""
    if not is_uae_url(url):
        return False
    return "dubai" in url.lower()


def is_restaurant_like(url: str) -> bool:
    """
    Heuristic: path contains '/restaurant' or '/restaurants/'.
    Adjust once you inspect real URLs from the CSV.
    """
    path = urlparse(url).path.lower()
    return "/restaurant" in path or "/restaurants/" in path


# =======================
# MAIN CRAWLER
# =======================

def crawl_sitemap(
    sitemap_url: str,
    seen_sitemaps: set,
    collected_urls: list,
    depth: int = 0,
):
    """
    Recursively crawl sitemap indexes up to MAX_SITEMAP_DEPTH.
    collected_urls is filled with dicts:
      {source_sitemap, loc, is_uae, is_dubai, is_restaurant_like}
    """
    if sitemap_url in seen_sitemaps:
        return
    if depth > MAX_SITEMAP_DEPTH:
        print(f"{'  '*depth}Max depth reached at {sitemap_url}")
        return

    # Optional hard limit on number of sitemaps
    if MAX_SITEMAPS is not None and len(seen_sitemaps) >= MAX_SITEMAPS:
        print("Reached MAX_SITEMAPS limit, stopping further sitemap recursion.")
        return

    seen_sitemaps.add(sitemap_url)
    print(f"{'  '*depth}Crawling sitemap: {sitemap_url}")

    try:
        xml_bytes = fetch_xml(sitemap_url)
        url_locs, sitemap_locs = parse_sitemap(xml_bytes)
    except Exception as e:
        print(f"{'  '*depth}Error fetching/parsing {sitemap_url}: {e}")
        return

    # Record URLs
    for loc in url_locs:
        # Optional hard limit on URLs
        if MAX_URLS is not None and len(collected_urls) >= MAX_URLS:
            print("Reached MAX_URLS limit, stopping URL collection.")
            break

        record = {
            "source_sitemap": sitemap_url,
            "loc": loc,
            "is_uae": is_uae_url(loc),
            "is_dubai": is_dubai_url(loc),
            "is_restaurant_like": is_restaurant_like(loc),
        }
        collected_urls.append(record)

    # Recurse into child sitemap indexes
    for child in sitemap_locs:
        crawl_sitemap(child, seen_sitemaps, collected_urls, depth=depth + 1)


def main():
    seen_sitemaps = set()
    collected_urls = []

    for root_sm in ROOT_SITEMAPS:
        crawl_sitemap(root_sm, seen_sitemaps, collected_urls, depth=0)

    # De‑duplicate by URL
    unique = {}
    for rec in collected_urls:
        loc = rec["loc"]
        if loc not in unique:
            unique[loc] = rec

    records = list(unique.values())
    print(f"\nTotal unique URLs collected: {len(records)}")
    print(f"Total sitemaps visited: {len(seen_sitemaps)}")

    # Write to CSV
    fieldnames = ["source_sitemap", "loc", "is_uae", "is_dubai", "is_restaurant_like"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} rows to {OUTPUT_CSV}")
    print("Next step: open CSV in Excel and filter is_uae / is_dubai / is_restaurant_like.")


if __name__ == "__main__":
    main()