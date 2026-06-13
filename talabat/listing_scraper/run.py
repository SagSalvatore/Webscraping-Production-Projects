"""
Run the Talabat UAE listing spider.

USAGE
-----
cd talabat/listing_scraper/

  Full run (all 648 areas):
      python run.py

  Full run WITH resume support (recommended — safe to Ctrl+C and resume):
      python run.py --resume

  Smoke test — first 3 areas only:
      python run.py --limit 3

  Debug a single area verbosely:
      python run.py --limit 1 --loglevel DEBUG

OUTPUT (all in talabat/data/urls/)
------
  talabat_restaurant_urls.jsonl      — all unique branches (append mode, safe to resume)
  area_progress.json                 — per-area branch counts, autosaved every 500 items
  talabat_restaurant_urls_summary.json — final summary at completion
  spider.log                         — full spider log

RESUME AFTER CRASH
------------------
  python run.py --resume
  (Scrapy replays pending requests from the job queue)
  NOTE: The JSONL file is in append mode — already-written entries stay.
        Dedup pipeline drops any branch_ids seen again.
"""

import argparse
import sys
from pathlib import Path

from scrapy.cmdline import execute

_JOBDIR = str(Path(__file__).resolve().parent.parent / ".scrapy" / "jobs" / "uae_full_run")


def main():
    parser = argparse.ArgumentParser(description="Run Talabat UAE listing spider")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only scrape first N areas (0 = all 648)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from previous run (uses JOBDIR)")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    cmd = ["scrapy", "crawl", "talabat_uae_listing"]
    cmd += ["-a", f"area_limit={args.limit}"]
    cmd += ["-s", f"LOG_LEVEL={args.loglevel}"]

    if args.resume:
        cmd += ["-s", f"JOBDIR={_JOBDIR}"]
        print(f"Resume mode: job directory = {_JOBDIR}")
    else:
        # Fresh run — enable JOBDIR anyway so first run is resumable
        cmd += ["-s", f"JOBDIR={_JOBDIR}"]
        print(f"Fresh run (resume-enabled): job directory = {_JOBDIR}")

    print(f"Areas: {'all 648' if args.limit == 0 else args.limit}")
    print(f"Output: ../data/urls/talabat_restaurant_urls.jsonl\n")

    sys.argv = cmd
    execute()


if __name__ == "__main__":
    main()
