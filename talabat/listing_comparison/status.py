"""Live status for the areaName fetch. Read-only - safe to run any time.

    python status.py           one-shot
    python status.py --watch   refresh every 20s
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "output" / "areaname_cache.json"
FAILED = HERE / "output" / "areaname_failed.jsonl"
LOG = HERE / "logs" / "fetch_area.log"
FINAL = HERE / "output" / "genuinely_new_area_corrected.csv"
TOTAL = 5709


def count_lines(p):
    if not p.exists():
        return 0
    with open(p, encoding="utf-8", errors="replace") as f:
        return sum(1 for l in f if l.strip())


def show():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print(f"  AREA-NAME FIX          {datetime.now():%H:%M:%S}")
    print("=" * 60)

    done = 0
    changed = 0
    if CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            done = len(c)
            changed = sum(1 for v in c.values() if v.get("area_name_real"))
        except Exception:
            pass                       # mid-write; next tick will read cleanly

    failed = count_lines(FAILED)
    pct = done / TOTAL * 100
    bar = "#" * int(pct / 2.5) + "." * (40 - int(pct / 2.5))

    print(f"  [{bar}] {pct:.1f}%")
    print(f"  fetched : {done:,} / {TOTAL:,}")
    print(f"  with an areaName : {changed:,}")
    print(f"  failed (retryable) : {failed:,}")

    rate = None
    if LOG.exists():
        txt = LOG.read_text(encoding="utf-8", errors="replace")
        hits = re.findall(r"([\d.]+)/s \| ETA (\d+) min", txt)
        if hits:
            rate, eta = hits[-1]
            print(f"\n  rate : {rate}/s      ETA : {eta} min")
    if rate is None and done:
        print("\n  (no rate line yet - first checkpoint lands at 100 rows)")

    if FINAL.exists():
        print(f"\n  DONE - output written: {FINAL.name}")
    else:
        print("\n  still running (CSV is written at the end)")
    if failed:
        print(f"  after it finishes, retry the {failed:,} failures with:")
        print("     python fetch_area_names.py --concurrency 6")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true")
    a = p.parse_args()
    if a.watch:
        while True:
            print("\033[2J\033[H", end="")
            show()
            time.sleep(20)
    else:
        show()
