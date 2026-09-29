"""
test_proxy.py — Verify Oxylabs UAE residential proxy + Talabat access.

Checks:
  1. IP / geo (via httpbin.org)
  2. Talabat homepage (status 200, no Cloudflare block)
  3. One real restaurant menu page (__NEXT_DATA__ present)

Usage:
  cd talabat/menu
  python test_proxy.py
"""

import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from curl_cffi import requests as cffi_requests

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

USERNAME = os.getenv("OXYLABS_USERNAME", "")
PASSWORD = os.getenv("OXYLABS_PASSWORD", "")
COUNTRY  = os.getenv("OXYLABS_COUNTRY", "ae")

BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Known-working Talabat UAE restaurant URL for menu test
TEST_MENU_URL = "https://www.talabat.com/uae/restaurant/606/thai-wok-restaurant-al-barsha?aid=1280"


def make_session(sid: str = "test001"):
    proxy_url = (
        f"http://customer-{USERNAME}-cc-{COUNTRY}-sessid-{sid}"
        f":{PASSWORD}@pr.oxylabs.io:7777"
    )
    session = cffi_requests.Session(impersonate="chrome124")
    session.proxies = {"http": proxy_url, "https": proxy_url}
    session.headers.update({
        "User-Agent":      random.choice(BROWSER_UAS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control":   "no-cache",
    })
    return session, proxy_url


def check(label: str, ok: bool, detail: str = ""):
    icon = "PASS" if ok else "FAIL"
    line = f"  [{icon}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    return ok


def main():
    if not USERNAME or not PASSWORD:
        print("ERROR: OXYLABS_USERNAME / OXYLABS_PASSWORD not set in talabat/.env")
        sys.exit(1)

    print("=" * 60)
    print("PROXY + TALABAT ACCESS TEST")
    print(f"Proxy user : customer-{USERNAME}-cc-{COUNTRY}-sessid-...")
    print("=" * 60)

    session, proxy_url = make_session()
    all_ok = True

    # ── Test 1: IP / geo via httpbin ──────────────────────────────────────────
    print("\n[1/3] Checking proxy IP / geo...")
    ip_ok = False
    for ip_url in ["https://ipinfo.io/json", "https://api.ipify.org?format=json", "https://httpbin.org/ip"]:
        try:
            r = session.get(ip_url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                ip = data.get("ip") or data.get("origin", "?")
                country = data.get("country", "?")
                all_ok &= check("Proxy connected", True, f"exit IP = {ip}  country = {country}")
                ip_ok = True
                break
        except Exception:
            continue
    if not ip_ok:
        all_ok &= check("Proxy connected", False, "all IP-check services failed")

    # ── Test 2: Talabat homepage ──────────────────────────────────────────────
    print("\n[2/3] Checking Talabat UAE homepage...")
    try:
        r = session.get("https://www.talabat.com/uae", timeout=20)
        is_200 = r.status_code == 200
        not_blocked = "Just a moment" not in r.text and "cf-browser-verification" not in r.text
        all_ok &= check("Homepage status 200", is_200, f"HTTP {r.status_code}")
        all_ok &= check("No Cloudflare block", not_blocked,
                        "page appears normal" if not_blocked else "Cloudflare challenge detected")
    except Exception as e:
        all_ok &= check("Homepage reachable", False, str(e)[:80])

    # ── Test 3: Restaurant menu page (__NEXT_DATA__) ──────────────────────────
    print(f"\n[3/3] Checking menu page: {TEST_MENU_URL}")
    try:
        r = session.get(TEST_MENU_URL, timeout=25)
        is_200 = r.status_code == 200
        has_next = "__NEXT_DATA__" in r.text
        all_ok &= check("Menu page status 200", is_200, f"HTTP {r.status_code}")
        all_ok &= check("__NEXT_DATA__ present", has_next,
                        "menu data found" if has_next else "SSR data missing — page may be blocked")

        if has_next and is_200:
            import re
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            tag  = soup.find("script", {"id": "__NEXT_DATA__"})
            if tag and tag.string:
                nd = json.loads(tag.string)
                items = (nd.get("props", {})
                           .get("pageProps", {})
                           .get("initialMenuState", {})
                           .get("menuData", {})
                           .get("items", []))
                check("Menu items parsed", len(items) > 0, f"{len(items)} items found")

    except Exception as e:
        all_ok &= check("Menu page reachable", False, str(e)[:80])

    session.close()

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL TESTS PASSED — proxy and Talabat access are working.")
        print("\nNext step:")
        print("  python talabat_menu_tracker.py --limit 5 --rebuild")
    else:
        print("SOME TESTS FAILED — check proxy credentials or network.")
        print("Credentials file: talabat/.env")
    print("=" * 60)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
