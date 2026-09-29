"""Run a cohort of brands in parallel, one free-tier Apify key per brand.

Each brand already runs its 10 city queries as 2 simultaneous actor runs. Giving
every brand its OWN key means the brands also run simultaneously instead of
queueing behind one account's concurrency limit, so wall-clock is the slowest
brand rather than the sum of all of them.

Free-tier keys bill at $0.004/place against the team's $100/month of free credit,
vs $0.003 on the main BRONZE account's real balance - so --cost-per-place is
passed through for an honest cost line in each brand's log.

Reads sept_chicken/key_assignment.json  ({brand: key_name}).

    python run_cohort.py --dry-run          # show the plan, spend nothing
    python run_cohort.py --skip hardees     # everything except the pilot
    python run_cohort.py --only wingstop,nandos
"""
import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

APIFY_DIR = Path(__file__).resolve().parent
ASSIGN = APIFY_DIR / "sept_chicken" / "key_assignment.json"
RUNNER = APIFY_DIR / "scripts" / "run_brand_scraper.py"
FREE_TIER_COST = 0.004
sys.stdout.reconfigure(encoding="utf-8")


def run_one(brand, key_name, cost):
    started = time.time()
    cmd = [sys.executable, "-u", str(RUNNER), "--brand", brand,
           "--key-name", key_name, "--cost-per-place", str(cost)]
    p = subprocess.run(cmd, cwd=str(APIFY_DIR), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    tail = [l for l in (p.stdout or "").splitlines()
            if "Total raw items" in l or "Total cost" in l or "ERROR" in l]
    return brand, p.returncode, time.time() - started, tail


def main(args):
    assign = json.loads(ASSIGN.read_text(encoding="utf-8"))
    if args.only:
        want = {b.strip() for b in args.only.split(",")}
        assign = {k: v for k, v in assign.items() if k in want}
    for s in (args.skip or "").split(","):
        assign.pop(s.strip(), None)

    print("=" * 74)
    print(f"  RUN COHORT  |  {len(assign)} brands in parallel")
    print("=" * 74)
    for b, k in assign.items():
        print(f"    {b:22}{k}")
    print(f"\n  cost/place ${args.cost_per_place}  (free-tier team keys)")
    if args.dry_run:
        print("\n  --dry-run: nothing launched")
        return 0

    t0 = time.time()
    print(f"\n  launched {datetime.now():%H:%M:%S} - each brand logs to "
          f"brands/<brand>/output/logs/\n")
    results = []
    with ThreadPoolExecutor(max_workers=len(assign)) as ex:
        futs = {ex.submit(run_one, b, k, args.cost_per_place): b
                for b, k in assign.items()}
        for f in as_completed(futs):
            brand, rc, secs, tail = f.result()
            status = "OK" if rc == 0 else f"FAILED rc={rc}"
            print(f"  [{datetime.now():%H:%M:%S}] {brand:22}{status:14}"
                  f"{secs/60:5.1f} min")
            for t in tail:
                print(f"        {t.split('INFO')[-1].strip()}")
            results.append((brand, rc))

    ok = sum(1 for _, rc in results if rc == 0)
    print(f"\n  {ok}/{len(results)} succeeded in {(time.time()-t0)/60:.1f} min "
          f"(parallel wall-clock)")
    bad = [b for b, rc in results if rc != 0]
    if bad:
        print(f"  FAILED: {bad}")
    print("\n  next: python scripts/process_brand.py --brand <brand>")
    return 1 if bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip", default="")
    p.add_argument("--only", default="")
    p.add_argument("--cost-per-place", type=float, default=FREE_TIER_COST)
    sys.exit(main(p.parse_args()))
