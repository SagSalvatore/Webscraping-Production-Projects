"""
check_keys.py - read-only balance check for every Tavily and Apify key in
TRACKER_TAVILY_KEYS_apify.xlsx. Spends nothing: Tavily /usage and Apify
/users/me/limits are both free endpoints. Keys are never printed.

    python check_keys.py            # both pools
    python check_keys.py --tavily   # one pool only
    python check_keys.py --apify
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config


def tavily_usage(name: str, key: str) -> dict:
    # /usage is rate-limited per IP: parallel calls get a 429 "excessive
    # requests" block for several minutes. A 429 here is NOT a dead key -
    # only 401/403 are permanent.
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {key}"},
                timeout=20,
            )
        except requests.RequestException as exc:
            return {"name": name, "status": "error", "detail": str(exc)[:80]}
        if r.status_code != 429:
            break
        time.sleep(20 * (attempt + 1))
    if r.status_code != 200:
        return {"name": name, "status": f"HTTP {r.status_code}", "detail": r.text[:80]}
    body = r.json()
    k, acct = body.get("key", {}) or {}, body.get("account", {}) or {}
    # The key-level limit is null on these accounts; the plan limit lives
    # under "account".
    used = k.get("usage") if k.get("usage") is not None else acct.get("plan_usage")
    limit = k.get("limit") if k.get("limit") is not None else acct.get("plan_limit")
    remaining = (limit - used) if isinstance(limit, (int, float)) and isinstance(used, (int, float)) else None
    return {"name": name, "status": "ok", "used": used, "limit": limit,
            "remaining": remaining, "plan": acct.get("current_plan")}


def apify_limits(name: str, token: str) -> dict:
    try:
        r = requests.get(
            "https://api.apify.com/v2/users/me/limits",
            params={"token": token},
            timeout=20,
        )
    except requests.RequestException as exc:
        return {"name": name, "status": "error", "detail": str(exc)[:80]}
    if r.status_code != 200:
        return {"name": name, "status": f"HTTP {r.status_code}", "detail": r.text[:80]}
    d = r.json().get("data", {})
    limit = d.get("limits", {}).get("maxMonthlyUsageUsd")
    used = d.get("current", {}).get("monthlyUsageUsd")
    return {
        "name": name, "status": "ok", "limit": limit, "used": round(used or 0, 4),
        "remaining": round((limit or 0) - (used or 0), 4),
        "cycle_end": (d.get("monthlyUsageCycle") or {}).get("endAt", "")[:10],
    }


def run(pool: str) -> list[dict]:
    keys = config.load_keys(pool)
    if pool == "tavily":
        rows = []
        for name, key in keys:
            rows.append(tavily_usage(name, key))
            time.sleep(3)
    else:
        with ThreadPoolExecutor(max_workers=8) as ex:
            rows = list(ex.map(lambda nk: apify_limits(*nk), keys))
    ok = [r for r in rows if r["status"] == "ok"]
    unit = "credits" if pool == "tavily" else "USD"
    print(f"\n== {pool.upper()}: {len(ok)}/{len(rows)} keys answer OK")
    for r in sorted(rows, key=lambda r: -(r.get("remaining") or -1)):
        if r["status"] == "ok":
            print(f"  {r['name']:<24} remaining {r['remaining']!s:>8} {unit}  "
                  f"(used {r['used']} / limit {r['limit']})"
                  + (f"  cycle ends {r['cycle_end']}" if r.get("cycle_end") else ""))
        else:
            print(f"  {r['name']:<24} {r['status']}  {r.get('detail', '')}")
    total = sum(r["remaining"] or 0 for r in ok)
    print(f"  TOTAL remaining: {total:,.2f} {unit}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tavily", action="store_true")
    ap.add_argument("--apify", action="store_true")
    args = ap.parse_args()
    pools = [p for p, on in (("tavily", args.tavily), ("apify", args.apify)) if on] or ["tavily", "apify"]
    report = {p: run(p) for p in pools}
    config.KEY_BALANCES.parent.mkdir(parents=True, exist_ok=True)
    config.KEY_BALANCES.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
