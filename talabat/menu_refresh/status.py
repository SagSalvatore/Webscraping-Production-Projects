"""Live progress for the menu-refresh scrape.

Reads the checkpoint and output files directly - it does not attach to the
running process, so it is safe to run as often as you like and works after a
restart. `--watch` refreshes in place until the run finishes.

    python status.py
    python status.py --watch
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def _argv(flag, default=None):
    """Read the flag before the paths below are bound - argparse runs in
    main(), by which point these constants are already resolved."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    for a in sys.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


# --run <dir> points at a refresh directory. Bare invocation still watches the
# original data/scrape, so nothing that used to work stops working.
RUN = _argv("--run")
SUB = _argv("--scrape", "scrape")
if RUN:
    RD = DATA / RUN
    SCRAPE = RD / SUB
    TARGETS = RD / f"scrape_targets_{RUN.split('_')[-1]}.jsonl"
else:
    SCRAPE = DATA / "scrape"
    TARGETS = DATA / "scrape_targets.jsonl"

CHECKPOINT = SCRAPE / "checkpoint.json"
ITEMS = SCRAPE / "menu_items.jsonl"
STATUS = SCRAPE / "restaurant_status.jsonl"
FAILED = SCRAPE / "failed.jsonl"

sys.stdout.reconfigure(encoding="utf-8")


def count_lines(p):
    if not p.exists():
        return 0
    with open(p, "rb") as f:
        return sum(1 for _ in f)


def bar(pct, w=34):
    n = int(pct / 100 * w)
    return "#" * n + "." * (w - n)


def snapshot():
    total = count_lines(TARGETS)
    done = 0
    started = None
    if CHECKPOINT.exists():
        try:
            d = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            done = len(d.get("done_branch_ids", []))
            started = d.get("started_at")
        except (json.JSONDecodeError, OSError):
            done = 0
    # the checkpoint only flushes every 100, so on its own it reads 0 for the
    # first minutes. restaurant_status.jsonl gets a line per restaurant, so it
    # is the finer signal; take whichever is further along.
    done = max(done, count_lines(STATUS))
    return total, done, started, count_lines(ITEMS), count_lines(FAILED)


def statuses():
    c = Counter()
    if STATUS.exists():
        with open(STATUS, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    try:
                        c[json.loads(line).get("status", "?")] += 1
                    except json.JSONDecodeError:
                        pass
    return c


def render(t0, first_done):
    total, done, _, items, failed = snapshot()
    pct = done / total * 100 if total else 0
    el = time.time() - t0
    # rate measured over THIS status session, so a resumed run is not credited
    # with work done before the watch started
    rate = (done - first_done) / el if el > 5 and done > first_done else 0
    eta = (total - done) / rate / 60 if rate > 0 else None

    out = []
    out.append("=" * 60)
    out.append(f"  TALABAT MENU REFRESH  -  {total:,} restaurant re-scrape"
               + (f"  [{RUN}]" if RUN else ""))
    out.append("=" * 60)
    out.append(f"  [{bar(pct)}] {pct:5.1f}%")
    out.append(f"  restaurants : {done:,} / {total:,}")
    out.append(f"  menu items  : {items:,}")
    if failed:
        out.append(f"  failed      : {failed:,}")
    st = statuses()
    if st:
        out.append("  outcomes    : " + "  ".join(
            f"{k}={v:,}" for k, v in st.most_common()))
    if rate:
        out.append(f"  rate        : {rate*60:.0f} restaurants/min")
    if eta is not None:
        out.append(f"  ETA         : {eta:.0f} min"
                   f"   (finishes ~{time.strftime('%H:%M', time.localtime(time.time()+eta*60))})")
    if done >= total and total:
        out.append("\n  RUN COMPLETE -> next: python compute_delta.py --dry-run")
    return "\n".join(out), done >= total and total > 0


def main(args):
    t0 = time.time()
    _, first_done, _, _, _ = snapshot()
    if not args.watch:
        print(render(t0, first_done)[0])
        return 0
    try:
        while True:
            text, done = render(t0, first_done)
            print("\033[2J\033[H" + text, flush=True)
            if done:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        print("\n  (watch stopped - the scrape keeps running)")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true")
    p.add_argument("--run", help="refresh directory under data/, "
                                 "e.g. refresh_202609")
    p.add_argument("--scrape", default="scrape",
                   help="scrape subdirectory ('smoke' for the trial slice)")
    p.add_argument("--every", type=int, default=20)
    sys.exit(main(p.parse_args()))
