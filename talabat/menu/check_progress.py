"""
check_progress.py — Show live progress of a running (or completed) scrape run.

Usage:
  cd talabat/menu
  python check_progress.py            # snapshot stats + last 30 log lines
  python check_progress.py --tail     # stream the log live (Ctrl+C to stop)
  python check_progress.py --log-tail 50  # show last 50 log lines

Output example:
  Checkpoint : 1,450 done  |  3,825 remaining  |  5,275 total
  Last saved : 2026-06-18 14:32:07 UTC
  Log file   : data/logs/run_20260618_141200.log
  ─────────────────────────────────────────
  [latest 30 lines from log...]
"""

import argparse
import json
import sys
import time
from pathlib import Path

DATA_DIR    = Path(__file__).resolve().parent / "data"
LOG_DIR     = DATA_DIR / "logs"
CHECKPOINT  = DATA_DIR / "checkpoint.json"
TOTAL_RESTAURANTS = 5275   # from Data_menus.xlsx


def read_checkpoint() -> dict:
    if not CHECKPOINT.exists():
        return {"done": [], "count": 0, "saved_at": "—"}
    with open(CHECKPOINT, "r", encoding="utf-8") as f:
        return json.load(f)


def latest_log() -> Path | None:
    # Prefer the symlink; fall back to most-recently-modified file
    sym = LOG_DIR / "run_LATEST.log"
    if sym.exists():
        target = LOG_DIR / sym.read_text().strip() if sym.is_symlink() else None
        if target and target.exists():
            return target
    logs = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Exclude the symlink itself
    logs = [l for l in logs if not l.name == "run_LATEST.log"]
    return logs[0] if logs else None


def tail_lines(path: Path, n: int) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return lines[-n:]


def print_summary(n_tail: int = 30):
    ck = read_checkpoint()
    done  = ck.get("count", len(ck.get("done", [])))
    saved = ck.get("saved_at", "—")
    remaining = max(0, TOTAL_RESTAURANTS - done)
    pct   = done / TOTAL_RESTAURANTS * 100

    print("=" * 58)
    print(f"  Checkpoint  : {done:,} done  |  {remaining:,} remaining  |  {TOTAL_RESTAURANTS:,} total")
    print(f"  Progress    : {pct:.1f}%")
    print(f"  Last saved  : {saved}")

    log = latest_log()
    if log:
        print(f"  Log file    : {log.relative_to(Path(__file__).parent)}")
    print("=" * 58)

    if log and log.exists():
        lines = tail_lines(log, n_tail)
        print(f"\n--- Last {len(lines)} log lines ---")
        for line in lines:
            print(line, end="")
        print()
    else:
        print("  No log file found yet.")


def stream_log(log: Path):
    print(f"Tailing {log.name}  (Ctrl+C to stop)\n{'─'*58}")
    with open(log, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)   # jump to end
        while True:
            line = f.readline()
            if line:
                print(line, end="", flush=True)
            else:
                time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Check talabat scraper progress")
    parser.add_argument("--tail",     action="store_true", help="Stream the live log (Ctrl+C to stop)")
    parser.add_argument("--log-tail", type=int, default=30, metavar="N", help="Lines to show (default 30)")
    args = parser.parse_args()

    if args.tail:
        log = latest_log()
        if not log:
            print("No log file found — is the scraper running?")
            sys.exit(1)
        stream_log(log)
    else:
        print_summary(n_tail=args.log_tail)


if __name__ == "__main__":
    main()
