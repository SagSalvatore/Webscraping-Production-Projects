"""Live status for the run2 crawl. Safe to run any time - read-only.

Progress is derived from the CHECKPOINT, not from the log, because the log only
prints every 200 pages and a piped `tail` buffers. The checkpoint is also what
the collector itself resumes from, so it is the honest source of truth.

    python status.py                  one-shot, top-20 priority run
    python status.py --watch          refresh every 20s
    python status.py --top 50         scope to a different priority tier
    python status.py --batch 2026-08-11   count only rows crawled that day
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
URLS = HERE.parent / "data" / "urls"
RES = HERE / "smoke_results"
OUT = URLS / "talabat_restaurant_urls_run2.jsonl"
CKPT = URLS / "run2_checkpoint.json"
FAILED = URLS / "run2_failed_pages.jsonl"
LOG = HERE / "logs" / "run2.log"

LIVELY = {"residential", "commercial", "tourist"}


def count_lines(p):
    if not p.exists():
        return 0
    with open(p, encoding="utf-8", errors="replace") as f:
        return sum(1 for l in f if l.strip())


def show(args):
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 64)
    print(f"  RUN2 - TOP {args.top} PRIORITY AREAS        {datetime.now():%H:%M:%S}")
    print("=" * 64)

    amap = {a["id"]: a for a in json.loads((RES / "area_map.json").read_text(encoding="utf-8"))}
    if args.probe:
        # measured ranking - matches `run2_collector.py --from-probe`
        pr = json.loads((RES / "area_probe_ranked.json").read_text(encoding="utf-8"))
        top = [a for a in pr if a.get("probe_new")][: args.top]
    else:
        pri = json.loads((RES / "area_priority.json").read_text(encoding="utf-8"))
        top = sorted([a for a in pri if a.get("character") in LIVELY],
                     key=lambda x: -x["efficiency"])[: args.top]

    done = set()
    if CKPT.exists():
        try:
            done = set(json.loads(CKPT.read_text(encoding="utf-8"))["done_pages"])
        except Exception:
            pass                       # mid-write; next tick reads cleanly

    top_ids = {a["id"] for a in top}
    total = pending = 0
    per_area = []
    for a in top:
        tp = amap.get(a["id"], {}).get("total_pages") or 0
        d = sum(1 for p in range(1, tp + 1) if f"{a['id']}::{p}" in done)
        total += tp
        pending += tp - d
        per_area.append((a["name"], d, tp))

    d_all = total - pending
    pct = d_all / total * 100 if total else 0
    bar = "#" * int(pct / 2.5) + "." * (40 - int(pct / 2.5))
    print(f"\n  [{bar}] {pct:.1f}%")
    print(f"  pages done : {d_all:,} / {total:,}      remaining {pending:,}")

    # rows crawled in this batch
    batch = args.batch
    rows = 0
    areas = Counter()
    if OUT.exists():
        with open(OUT, encoding="utf-8", errors="replace") as f:
            for line in f:
                if batch and f'"{batch}' not in line:
                    continue
                if line.strip():
                    rows += 1
                    try:
                        areas[json.loads(line)["area_name"]] += 1
                    except Exception:
                        pass
    label = f"since {batch}" if batch else "all run-2"
    print(f"  NEW branches ({label}) : {rows:,}")
    print(f"  failed pages pending retry : {count_lines(FAILED):,}")
    # No yield figure here on purpose: batch rows / all-time done-pages would
    # divide today's rows by pages crawled on earlier days too. The per-area
    # rows below are the honest signal.
    print("\n  NOTE: the checkpoint flushes every 200 pages, so 'pages done'"
          "\n  can lag the row counts by up to ~200 pages mid-run.")

    # rate + ETA straight from the collector's own checkpoint lines
    if LOG.exists():
        txt = LOG.read_text(encoding="utf-8", errors="replace")
        lines = [re.sub(r"\x1b\[[0-9;]*m", "", l).strip()
                 for l in txt.splitlines() if "pages | NEW" in l]
        if lines:
            m = re.search(r"(\d[\d,]*)/(\d[\d,]*) pages.*?([\d.]+) pg/s.*?"
                          r"ETA (\d+) min.*?failed (\d+)", lines[-1])
            if m:
                cur, tot_run, rate, eta, failed = m.groups()
                print(f"\n  current run : {cur}/{tot_run} pages  {rate} pg/s  "
                      f"failed {failed}")
                h, mn = divmod(int(eta), 60)
                print(f"  ETA : {h}h {mn}m")
            ts = re.match(r"([\d\-]+ [\d:]+)", lines[-1])
            if ts:
                print(f"  last checkpoint line : {ts.group(1)}")

    if args.areas:
        print("\n  per area (done/total):")
        for name, d, tp in per_area:
            got = areas.get(name, 0)
            print(f"    {name[:30]:32} {d:>4}/{tp:<5} new={got:>4}")

    if pending == 0:
        print("\n  ALL TOP-20 PAGES DONE")
    else:
        print("\n  (safe to stop anytime - progress is checkpointed)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--batch", default="2026-08-11", help="'' for all run-2 rows")
    p.add_argument("--areas", action="store_true", help="per-area breakdown")
    p.add_argument("--probe", action="store_true",
                   help="use the MEASURED probe ranking (for --from-probe runs)")
    a = p.parse_args()
    if a.watch:
        while True:
            print("\033[2J\033[H", end="")
            show(a)
            time.sleep(20)
    else:
        show(a)
