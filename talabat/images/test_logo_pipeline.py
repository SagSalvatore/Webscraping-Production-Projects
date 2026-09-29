"""Tests for the August logo pipeline. Run before the production scrape.

  SMOKE       inputs exist, modules import, flags wired
  SANITY      extraction and URL-stripping behave on hand-built cases
  REGRESSION  the hazards already paid for stay handled
  PROXY       one real Oxylabs fetch of a live Talabat page (--proxy)

The proxy test is separate because it costs a real request. Everything else is
free and offline.

    python test_logo_pipeline.py
    python test_logo_pipeline.py --proxy      # + one live Oxylabs fetch
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

import scrape_menu_images as S

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))


TARGETS = HERE / "data" / "august_logo_targets.jsonl"


def smoke():
    print("\n=== SMOKE ===")
    check("target list exists", TARGETS.exists(), str(TARGETS))
    rows = []
    if TARGETS.exists():
        rows = [json.loads(l) for l in open(TARGETS, encoding="utf-8") if l.strip()]
        check("7,133 targets", len(rows) == 7133, f"{len(rows)}")
        check("every row has source_id + map_url",
              all(r.get("source_id") and r.get("map_url") for r in rows))
        check("every URL keeps ?aid=",
              all("?aid=" in r["map_url"] for r in rows),
              f"{sum(1 for r in rows if '?aid=' not in r['map_url'])} missing")
        check("source_id unique",
              len({r["source_id"] for r in rows}) == len(rows))
    check("LOGO_ONLY flag exists", hasattr(S, "LOGO_ONLY"))
    check("oxylabs creds loaded", bool(S.USERNAME and S.PASSWORD),
          "missing from talabat/.env")
    import download_logos as D
    check("download_logos imports", hasattr(D, "process_restaurant_logo"))
    check("downloader capped at 100 workers", D.CONCURRENT_WORKERS <= 100,
          f"{D.CONCURRENT_WORKERS} - 150 caused WinError 10048")
    return rows


def sanity():
    print("\n=== SANITY ===")
    page = json.dumps({"props": {"pageProps": {"initialMenuState": {
        "restaurant": {"logo": "https://img.example/logo_123.jpg?width=180&height=180"},
        "menuData": {"items": [
            {"id": 1, "name": "Burger",
             "image": "https://img.example/a.jpg?width=172&height=172",
             "originalImage": "https://img.example/a.jpg"}]}}}}})
    html = f'<script id="__NEXT_DATA__" type="application/json">{page}</script>'
    got = S.extract_images(html)
    check("extract_images finds the block", got is not None)
    if got:
        logo, items = got
        check("logo query string stripped (full size, not thumbnail)",
              logo == "https://img.example/logo_123.jpg", f"{logo}")
        check("menu items still parsed", len(items) == 1)

    check("no __NEXT_DATA__ -> None (retried, not treated as 404)",
          S.extract_images("<html>bot challenge</html>") is None)
    check("strip_query_string leaves a clean URL alone",
          S.strip_query_string("https://x/y.jpg") == "https://x/y.jpg")
    check("strip_query_string handles None",
          S.strip_query_string(None) in (None, ""))


def regression():
    print("\n=== REGRESSION ===")
    src = (HERE / "scrape_menu_images.py").read_text(encoding="utf-8")
    dl = (HERE / "download_logos.py").read_text(encoding="utf-8")

    # --logo-only must change only what is WRITTEN, never the extraction
    check("logo_only gates the payload, not extract_images",
          "if not LOGO_ONLY:" in src and "images = {\"logo\": logo}" in src)
    # a cohort resuming on another cohort's checkpoint would skip real work
    check("out-prefix isolates the checkpoint",
          "_checkpoint.json" in src and "args.out_prefix" in src)
    # July's 150-worker run: ~1,472 failures, Windows ephemeral port exhaustion
    check("100-worker ceiling is documented in download_logos",
          "10048" in dl or "port exhaustion" in dl)
    # the ?aid= lesson, again
    check("input builder asserts ?aid=",
          "?aid=" in (HERE / "build_august_logo_input.py").read_text(encoding="utf-8"))
    # logos land per source_id so a rerun does not re-download
    check("downloader skips files already on disk",
          "already exists" in dl or "skips a file that already exists" in dl)

    # COHORT ISOLATION - the thing that makes this re-runnable each month.
    # Without it September resumes on August's checkpoint and skips everything.
    import argparse as _a
    import download_logos as D
    aug = D.resolve_paths(_a.Namespace(cohort="august", test=False, input=None))
    sep = D.resolve_paths(_a.Namespace(cohort="september", test=False, input=None))
    jul = D.resolve_paths(_a.Namespace(cohort=None, test=False, input=None))
    check("cohorts get separate checkpoints",
          aug["checkpoint"] != sep["checkpoint"] != jul["checkpoint"])
    check("cohorts get separate manifests",
          len({aug["final_manifest"], sep["final_manifest"],
               jul["final_manifest"]}) == 3)
    check("cohorts get separate failed lists",
          aug["final_failed"] != jul["final_failed"])
    check("July's unprefixed files are untouched by a cohort run",
          jul["checkpoint"].name == "logo_download_checkpoint.json"
          and aug["checkpoint"].name.startswith("august_"))
    check("image library is SHARED across cohorts (source_id never collides)",
          aug["final_downloads"] == sep["final_downloads"] == jul["final_downloads"])
    check("--test still isolates from a real cohort run",
          D.resolve_paths(_a.Namespace(cohort="august", test=True, input=None))
          ["checkpoint"] != aug["checkpoint"])
    check("retry uses the same cohort paths",
          "P = resolve_paths(args)" in dl and dl.count("resolve_paths(args)") >= 2)
    check("loguru writes a per-cohort log",
          aug["log"].name.startswith("august_") and "rotation" in dl)


def proxy_test(rows):
    print("\n=== PROXY (one live Oxylabs fetch) ===")
    if not rows:
        check("targets available for a live test", False)
        return
    row = rows[0]
    sess = S.make_session() if hasattr(S, "make_session") else None
    if sess is None:
        import random
        from curl_cffi import requests as cffi
        px = (f"http://customer-{S.USERNAME}-cc-{S.COUNTRY}"
              f"-sessid-{random.randint(10000,99999)}:{S.PASSWORD}"
              f"@pr.oxylabs.io:7777")
        sess = cffi.Session(impersonate="chrome124",
                            proxies={"http": px, "https": px})
    try:
        r = sess.get(row["map_url"], timeout=30)
        check("HTTP 200 through the proxy", r.status_code == 200,
              f"got {r.status_code}")
        got = S.extract_images(r.text)
        check("__NEXT_DATA__ present on a live page", got is not None)
        if got:
            logo, items = got
            check("logo URL extracted", bool(logo), f"{logo}")
            check("logo URL has no ?width= (full size)",
                  bool(logo) and "width=" not in logo, f"{logo}")
            print(f"      {row['restaurant_name'][:34]}")
            print(f"      logo: {logo}")
            print(f"      menu items on page: {len(items)}")
    except Exception as exc:
        check("live fetch completed", False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", action="store_true")
    a = p.parse_args()
    rows = smoke()
    sanity()
    regression()
    if a.proxy:
        proxy_test(rows)
    print(f"\n{'='*54}\n  PASSED {len(PASS)}   FAILED {len(FAIL)}")
    if FAIL:
        print("  failures: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
