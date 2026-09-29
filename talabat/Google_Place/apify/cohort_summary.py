"""Cohort summary: outlets per brand per emirate, plus raw volume and cost.

Reads each brand's finished CSV and its newest run log, so the numbers are the
ones actually shipped rather than a re-derivation.

    python cohort_summary.py
    python cohort_summary.py --combined      # also write the combined CSV/JSON
"""
import argparse
import csv
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

APIFY_DIR = Path(__file__).resolve().parent
COHORT_DIR = APIFY_DIR / "sept_chicken"
sys.stdout.reconfigure(encoding="utf-8")

EMIRATES = ["Dubai", "Abu Dhabi", "Al Ain", "Sharjah", "Ajman",
            "Ras Al Khaimah", "Fujairah", "Umm Al Quwain",
            "Khor Fakkan", "Kalba", "Unknown"]


def brand_csv(brand):
    cfg = json.loads((APIFY_DIR / "brands" / brand / "input.json")
                     .read_text(encoding="utf-8"))
    return (APIFY_DIR / "brands" / brand / "output"
            / f"{cfg['output_filename']}.csv"), cfg


def log_stats(brand):
    logs = sorted(glob.glob(str(APIFY_DIR / "brands" / brand
                                / "output" / "logs" / "*.log")))
    raw = cost = 0
    for lg in logs:                       # newest log that has a total wins
        t = Path(lg).read_text(encoding="utf-8", errors="replace")
        m = re.findall(r"Total raw items\s*:\s*([\d,]+)", t)
        c = re.findall(r"Total cost\s*:\s*~?\$([\d.]+)", t)
        if m:
            raw = int(m[-1].replace(",", ""))
        if c:
            cost = float(c[-1])
    return raw, cost


def main(args):
    brands = list(json.loads((COHORT_DIR / "key_assignment.json")
                             .read_text(encoding="utf-8")))
    show = EMIRATES[:9]
    w = f"{'BRAND':22}{'OUTLETS':>8}" + "".join(f"{e[:6]:>7}" for e in show) \
        + f"{'raw':>8}{'cost':>9}"
    print("=" * len(w))
    print("  SEPTEMBER CHICKEN-QSR COHORT")
    print("=" * len(w))
    print(w)
    print("-" * len(w))

    tot, raw_t, cost_t, all_rows = Counter(), 0, 0, []
    for b in brands:
        path, cfg = brand_csv(b)
        if not path.exists():
            print(f"{b:22}{'-- not processed --':>8}")
            continue
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        for r in rows:
            all_rows.append({"Brand": cfg["brand_display"],
                             "Brand_Folder": b, **r})
        c = Counter(r["Emirate"] for r in rows)
        tot += c
        raw, cost = log_stats(b)
        raw_t += raw
        cost_t += cost
        print(f"{b:22}{len(rows):>8}"
              + "".join(f"{c.get(e, 0) or '':>7}" for e in show)
              + f"{raw:>8}{'$%.2f' % cost:>9}")

    print("-" * len(w))
    print(f"{'TOTAL':22}{sum(tot.values()):>8}"
          + "".join(f"{tot.get(e, 0) or '':>7}" for e in show)
          + f"{raw_t:>8}{'$%.2f' % cost_t:>9}")

    unk = tot.get("Unknown", 0)
    print(f"\n  unresolved emirate : {unk}"
          + ("   <-- add spelling to _EMIRATE_ALIASES" if unk else "  (none)"))
    print(f"  distinct outlets   : {sum(tot.values())} across "
          f"{len([b for b in brands if brand_csv(b)[0].exists()])} brands")

    if args.combined and all_rows:
        COHORT_DIR.mkdir(parents=True, exist_ok=True)
        out = COHORT_DIR / "SEPT_CHICKEN_UAE_Combined"
        with open(f"{out}.csv", "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            wr.writeheader()
            wr.writerows(all_rows)
        Path(f"{out}.json").write_text(
            json.dumps(all_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  -> {out.name}.csv / .json  ({len(all_rows):,} rows)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--combined", action="store_true")
    sys.exit(main(p.parse_args()))
