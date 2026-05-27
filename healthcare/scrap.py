from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import os
import time


def scrape_product_detail(url):
    with sync_playwright() as p:
        headless = os.getenv("HEALTHCARE_HEADLESS", "true").lower() != "false"
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        page.goto("https://portal.dimdi.de/amguifree/am/search.xhtml")
        time.sleep(3)

        try:
            page.wait_for_selector('a:has-text("Accept")', timeout=10000)
            page.click('a:has-text("Accept")')
            print("Clicked accept")
            time.sleep(3)
        except Exception as e:
            print(f"No accept button: {e}")

        print(f"Loading: {url}")
        page.goto(url)
        time.sleep(5)

        print(f"Current URL: {page.url}")

        soup = BeautifulSoup(page.content(), "html.parser")

        with open("product_debug.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        data = {"url": url}

        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                data[dt.get_text(strip=True)] = dd.get_text(strip=True)

        tables = []
        for table in soup.find_all("table"):
            rows = [
                [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                for tr in table.find_all("tr")
            ]
            if rows:
                tables.append([row for row in rows if row])

        data["tables"] = tables

        browser.close()
        return data


if __name__ == "__main__":
    url = "https://portal.dimdi.de/amguifree/am/docoutput/jpadocdisplay.xhtml?globalDocId=6089026B16B44E75B53BACC418D1D1ED&directdisplay=true&docid=1"
    product = scrape_product_detail(url)

    with open("product_complete.json", "w", encoding="utf-8") as f:
        json.dump(product, f, ensure_ascii=False, indent=2)

    print(f"Fields: {len(product) - 2}")
    print(f"Tables: {len(product.get('tables', []))}")
