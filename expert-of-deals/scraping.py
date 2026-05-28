# ─────────────────────────────────────────────────────────────────────
#  ExpertsOfDeals.com — Lead Scraper
#  API endpoint : POST /api/latestlead
#  Response key : "lead" (confirmed from debug)
#  Output       : output/leads_TIMESTAMP.json + .csv
# ─────────────────────────────────────────────────────────────────────
#  pip install curl-cffi pandas
# ─────────────────────────────────────────────────────────────────────

from curl_cffi.requests import Session
import json, time, pandas as pd, os
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────
BASE_URL    = "https://expertsofdeals.com"
API_URL     = f"{BASE_URL}/api/latestlead"
OUTPUT_DIR  = "output"
PAGE_LIMIT  = 50        # records per page
MAX_PAGES   = 200       # safety ceiling
DELAY_SEC   = 0.8       # polite delay between pages
IMPERSONATE = "chrome"  # TLS fingerprint — mimics Chrome 120+

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── SESSION SETUP ─────────────────────────────────────────────────────
def make_session() -> Session:
    s = Session(impersonate=IMPERSONATE)
    s.headers.update({
        "accept":           "application/json, text/plain, */*",
        "accept-language":  "en-US,en;q=0.9",
        "content-type":     "application/json",
        "origin":           BASE_URL,
        "referer":          f"{BASE_URL}/",
        "sec-fetch-dest":   "empty",
        "sec-fetch-mode":   "cors",
        "sec-fetch-site":   "same-origin",
    })
    return s

# ── EXTRACT LEADS FROM RESPONSE ───────────────────────────────────────
def extract_leads(data) -> list:
    """
    Handles all known response shapes.
    Confirmed shape: { "status": true, "lead": [ ... ] }
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        # ✅ Confirmed key from debug output
        if "lead" in data and isinstance(data["lead"], list):
            return data["lead"]

        # Fallbacks — in case API changes key
        for key in ["leads", "data", "result", "items", "records"]:
            if key in data and isinstance(data[key], list):
                print(f"   ℹ️  Using fallback key: '{key}'")
                return data[key]

        # Last resort — find any list of dicts
        for key, val in data.items():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                print(f"   ℹ️  Auto-detected leads under key: '{key}'")
                return val

    return []

# ── CLEAN DIRTY FIELD VALUES ──────────────────────────────────────────
def clean_record(record: dict) -> dict:
    """
    Fixes known data quality issues in this API's response:
    - '\r\n' prefix on emails
    - 'mailto:' prefix on emails
    - '\u00a0' (non-breaking space) on phones/emails
    - Leading/trailing whitespace on all fields
    """
    cleaned = {}
    for k, v in record.items():
        if isinstance(v, str):
            v = (v
                 .replace("\r\n", "")
                 .replace("\u00a0", "")
                 .replace("mailto:", "")
                 .strip())
        cleaned[k] = v
    return cleaned

# ── PAGINATED FETCHER ─────────────────────────────────────────────────
def fetch_all_leads(session: Session) -> list:
    all_leads = []

    for page in range(1, MAX_PAGES + 1):
        print(f"📄 Page {page:>3} ...", end=" ", flush=True)

        try:
            resp = session.post(
                API_URL,
                json={"page": page, "limit": PAGE_LIMIT},
            )

            # ── Guard: bad HTTP status ──
            if resp.status_code != 200:
                print(f"❌ HTTP {resp.status_code} — stopping.")
                break

            # ── Guard: got HTML instead of JSON (auth wall / error page) ──
            ct = resp.headers.get("content-type", "")
            if "text/html" in ct:
                print("❌ Got HTML page — possible auth required or server error.")
                print(resp.text[:300])
                break

            # ── Parse JSON ──
            try:
                data = resp.json()
            except json.JSONDecodeError:
                print(f"❌ JSON decode failed. Raw response:\n{resp.text[:300]}")
                break

            # ── Guard: API-level failure ──
            if isinstance(data, dict) and data.get("status") is False:
                msg = data.get("message", "no message")
                print(f"❌ API returned status=false → '{msg}' — stopping.")
                break

            # ── Extract leads ──
            leads = extract_leads(data)

            if not leads:
                print("✅ Empty page — all leads collected.")
                break

            cleaned = [clean_record(r) for r in leads]
            all_leads.extend(cleaned)
            print(f"✔  {len(leads):>2} leads  |  Total: {len(all_leads)}")

            # ── Last page: fewer results than requested ──
            if len(leads) < PAGE_LIMIT:
                print("✅ Last page reached (partial batch).")
                break

            time.sleep(DELAY_SEC)

        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            break

    print(f"\n🏁 Fetch complete — {len(all_leads)} total leads collected.")
    return all_leads

# ── SAVE TO JSON + CSV ────────────────────────────────────────────────
def save_results(leads: list):
    if not leads:
        print("⚠️  No leads to save.")
        return None

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    df  = pd.DataFrame(leads)

    # Drop internal / low-value fields
    df.drop(columns=[c for c in ["__v", "supplier"] if c in df.columns], inplace=True)

    # Reorder columns — most useful fields first
    priority  = ["_id", "name", "companyName", "email", "phone",
                  "country", "product", "requirement",
                  "paymentterm", "shippingterm", "destinationport",
                  "productType", "quantity", "unit",
                  "status", "slug", "createdAt", "updatedAt"]
    ordered   = [c for c in priority if c in df.columns]
    remaining = [c for c in df.columns if c not in ordered]
    df = df[ordered + remaining]

    # ── Save JSON ──
    json_path = os.path.join(OUTPUT_DIR, f"leads_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON → {json_path}   ({len(leads)} records)")

    # ── Save CSV (utf-8-sig = opens correctly in Excel) ──
    csv_path = os.path.join(OUTPUT_DIR, f"leads_{ts}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ CSV  → {csv_path}   ({len(df)} rows × {len(df.columns)} cols)")

    # ── Terminal preview ──
    preview = ["name", "companyName", "email", "phone", "country", "product"]
    print(f"\n{'─'*80}")
    print(f"  PREVIEW — first 5 rows")
    print(f"{'─'*80}")
    print(df[[c for c in preview if c in df.columns]].head(5).to_string(index=False))
    print(f"{'─'*80}\n")

    return df

# ── MAIN ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  ExpertsOfDeals Lead Scraper")
    print(f"  Target : {API_URL}")
    print(f"  Output : ./{OUTPUT_DIR}/")
    print("=" * 60 + "\n")

    session = make_session()
    leads   = fetch_all_leads(session)
    save_results(leads)