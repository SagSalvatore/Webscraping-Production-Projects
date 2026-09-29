"""Final data-quality audit on ri-db.restaurants_id.final.json.

Read-only. Reports every issue class with counts and offending values so each
can be judged before any fix is applied.

Run: python audit_quality.py
"""
import re
import sys
from collections import Counter, defaultdict

import orjson
from loguru import logger

from config import INPUT_JSON, OUTPUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
logger.remove()
logger.add(sys.stderr, level="INFO", format="{message}")

FINAL = OUTPUT_DIR / "ri-db.restaurants_id.final.json"

ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
CITIES = {"dubai", "abu dhabi", "sharjah", "ajman", "uae", "fujairah",
          "umm al quwain", "ras al khaimah", "al ain", "united arab emirates"}
PLUS_CODE = re.compile(r"[A-Z0-9]{4}\+[A-Z0-9]{2,4}", re.I)
NON_AREA = re.compile(
    r"\b(floor|level|lvl|mezzanine|shop|unit|store|kiosk|counter|suite|villa|"
    r"building|bldg|tower|plaza|mall|centre|center|hotel|complex|souk|"
    r"street|st|road|rd|avenue|ave|blvd|boulevard|highway|"
    r"petrol|filling|station|enoc|adnoc|eppco|emarat|"
    r"near|inside|opposite|behind|beside|next to|"
    r"food court|parking|entrance|gate|college|university|hospital|"
    r"terminal|airport|unnamed)\b", re.I)
ZONE = re.compile(r"^(zone|sector|plot|block|phase)\s*\d+[a-z]?$|^[a-z]{1,3}\s?\d{1,3}[a-z]?$",
                  re.I)


def main():
    records = orjson.loads(FINAL.read_bytes())
    areas = Counter((r["location"] or {}).get("area") or "" for r in records)
    blank = areas.pop("", 0)
    total_rows = len(records)

    print("=" * 74)
    print(f"AUDIT  {total_rows:,} records | {len(areas):,} distinct areas | "
          f"{blank} blank")
    print("=" * 74)

    issues = {}

    def report(label, hits, show=8):
        """hits: {area: rowcount}"""
        rows = sum(hits.values())
        issues[label] = (len(hits), rows)
        flag = "OK " if not hits else "!! "
        print(f"\n{flag}{label:38} {len(hits):>4} values / {rows:>5} rows")
        for a, n in sorted(hits.items(), key=lambda t: -t[1])[:show]:
            print(f"        {n:>5}x  {a!r}")
        if len(hits) > show:
            print(f"        ... {len(hits)-show} more")

    report("Arabic characters", {a: n for a, n in areas.items() if ARABIC.search(a)})
    report("any non-ASCII", {a: n for a, n in areas.items()
                             if any(ord(c) > 127 for c in a)})
    report("contains hyphen", {a: n for a, n in areas.items() if "-" in a})
    report("contains comma", {a: n for a, n in areas.items() if "," in a})
    report("contains parenthesis", {a: n for a, n in areas.items() if "(" in a or ")" in a})
    report("non-area token", {a: n for a, n in areas.items() if NON_AREA.search(a)})
    report("google plus-code", {a: n for a, n in areas.items() if PLUS_CODE.search(a)})
    report("bare city / emirate", {a: n for a, n in areas.items()
                                   if a.strip().lower() in CITIES})
    report("bare zone code", {a: n for a, n in areas.items() if ZONE.match(a.strip())})
    report("digits only", {a: n for a, n in areas.items() if a.strip().isdigit()})
    report("length < 3", {a: n for a, n in areas.items() if len(a.strip()) < 3})
    report("length > 40", {a: n for a, n in areas.items() if len(a.strip()) > 40})
    report("leading/trailing space", {a: n for a, n in areas.items() if a != a.strip()})
    report("double space", {a: n for a, n in areas.items() if "  " in a})
    report("ALL CAPS", {a: n for a, n in areas.items()
                        if a.isupper() and len(a) > 4 and " " in a})
    report("all lowercase", {a: n for a, n in areas.items() if a.islower()})
    report("ordinal word", {a: n for a, n in areas.items()
                            if re.search(r"\b(first|second|third|fourth|fifth|sixth)\b", a, re.I)})
    report("ordinal suffix", {a: n for a, n in areas.items()
                              if re.search(r"\b\d+(st|nd|rd|th)\b", a, re.I)})
    report("abbreviation dot", {a: n for a, n in areas.items() if re.search(r"\b\w+\.", a)})

    # ---- twin groups ----
    def twins(label, keyfn):
        g = defaultdict(list)
        for a, n in areas.items():
            g[keyfn(a)].append((a, n))
        dupes = {k: v for k, v in g.items() if len(v) > 1}
        issues[label] = (len(dupes), sum(sum(n for _, n in v) for v in dupes.values()))
        flag = "OK " if not dupes else "!! "
        print(f"\n{flag}{label:38} {len(dupes):>4} groups")
        for k, v in sorted(dupes.items(), key=lambda t: -sum(n for _, n in t[1]))[:6]:
            print("        " + " | ".join(f"{a} ({n})" for a, n in
                                          sorted(v, key=lambda t: -t[1])))

    ORD = {"first": "1", "second": "2", "third": "3", "fourth": "4",
           "fifth": "5", "sixth": "6"}
    twins("twins: case/punctuation",
          lambda s: re.sub(r"[^a-z0-9]", "", s.lower()))
    twins("twins: ordinal vs number",
          lambda s: re.sub(r"[^a-z0-9]", "",
                           re.sub(r"\b(first|second|third|fourth|fifth|sixth)\b",
                                  lambda m: ORD[m.group(1).lower()], s.lower())))
    twins("twins: singular/plural",
          lambda s: "".join(re.sub(r"(?<=[a-z]{3})s$", "", w)
                            for w in re.findall(r"[a-z0-9]+", s.lower())))
    twins("twins: bare vs 'Al ' prefix",
          lambda s: re.sub(r"^al\s+", "", s.lower()).strip())
    twins("twins: Centre vs Center",
          lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("centre", "center")))

    print("\n" + "=" * 74)
    bad = {k: v for k, v in issues.items() if v[0]}
    if bad:
        print(f"ISSUES FOUND in {len(bad)} of {len(issues)} checks:")
        for k, (nv, nr) in sorted(bad.items(), key=lambda t: -t[1][1]):
            print(f"   {k:38} {nv:>4} values / {nr:>5} rows")
    else:
        print("ALL CHECKS CLEAN")
    print("=" * 74)


if __name__ == "__main__":
    main()
