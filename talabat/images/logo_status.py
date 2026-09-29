"""Live progress for the logo pipeline - scrape stage and download stage.

Reads the output files directly rather than attaching to the running process, so
it is safe to run as often as you like, works from any terminal, and survives a
restart. `--watch` refreshes in place until the stage finishes.

    python logo_status.py                    # one snapshot
    python logo_status.py --watch            # refresh until done
    python logo_status.py --cohort september # a later month
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STAGING = Path(r"C:\talabat_images_staging\data")

sys.stdout.reconfigure(encoding="utf-8")


def count_lines(p):
    if not p.exists():
        return 0
    with open(p, "rb") as f:
        return sum(1 for _ in f)


def bar(pct, w=34):
    n = int(pct / 100 * w)
    return "#" * n + "." * (w - n)


def snap(cohort):
    pre = f"{cohort}_" if cohort else ""
    targets = count_lines(DATA / f"{pre}logo_targets.jsonl")
    confirmed = count_lines(DATA / f"{pre}images_confirmed.jsonl")
    failed = count_lines(DATA / f"{pre}images_failed.jsonl")
    # download stage: manifest lives in staging until finalize() moves it
    man = STAGING / f"{pre}logos_manifest.jsonl"
    if not man.exists():
        man = DATA / f"{pre}logos_manifest.jsonl"
    downloaded = count_lines(man)
    dl_failed = count_lines(STAGING / f"{pre}logo_download_failed.jsonl") \
        or count_lines(DATA / f"{pre}logo_download_failed.jsonl")
    return targets, confirmed, failed, downloaded, dl_failed


def render(cohort, t0, first):
    targets, confirmed, failed, downloaded, dl_failed = snap(cohort)
    done = confirmed + failed
    pct = done / targets * 100 if targets else 0
    el = time.time() - t0
    rate = (done - first) / el if el > 5 and done > first else 0
    eta = (targets - done) / rate / 60 if rate > 0 else None

    L = ["=" * 62,
         f"  TALABAT LOGOS  -  cohort={cohort or 'july'}",
         "=" * 62,
         "  STAGE 1  scrape logo URLs",
         f"    [{bar(pct)}] {pct:5.1f}%",
         f"    done      : {done:,} / {targets:,}   (ok {confirmed:,}, failed {failed:,})"]
    if rate:
        L.append(f"    rate      : {rate*60:.0f} pages/min")
    if eta is not None and done < targets:
        fin = time.strftime('%H:%M', time.localtime(time.time() + eta * 60))
        L.append(f"    ETA       : {eta:.0f} min   (finishes ~{fin})")
    if targets and done >= targets:
        L.append("    COMPLETE")

    if downloaded or (targets and done >= targets):
        dpct = downloaded / confirmed * 100 if confirmed else 0
        L += ["", "  STAGE 2  download logo files",
              f"    [{bar(dpct)}] {dpct:5.1f}%",
              f"    saved     : {downloaded:,} / {confirmed:,}"
              + (f"   failed {dl_failed:,}" if dl_failed else "")]
        d = DATA / "downloaded"
        if d.exists():
            try:
                n = sum(1 for _ in d.iterdir())
                L.append(f"    library   : {n:,} restaurant folders "
                         "(July + August share it)")
            except OSError:
                pass
    if targets and done >= targets and not downloaded:
        L += ["", "  next: python download_logos.py --cohort "
              f"{cohort or 'august'}"]
    return "\n".join(L), bool(targets and done >= targets and downloaded >= confirmed)


def main(a):
    t0 = time.time()
    first = sum(snap(a.cohort)[1:3])
    if not a.watch:
        print(render(a.cohort, t0, first)[0])
        return 0
    try:
        while True:
            text, done = render(a.cohort, t0, first)
            print("\033[2J\033[H" + text, flush=True)
            if done:
                return 0
            time.sleep(a.every)
    except KeyboardInterrupt:
        print("\n  (watch stopped - the run keeps going)")
        return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", default="august")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--every", type=int, default=20)
    sys.exit(main(p.parse_args()))
