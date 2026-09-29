"""
check_team_keys.py
-------------------
Validates each API key in apify/keys.csv and reports its real
remaining budget before we build a distribution plan on top of them.
Tokens are masked in all output.
"""
import csv
import sys
import requests
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

APIFY_ROOT = Path(__file__).resolve().parent.parent
KEYS_CSV = APIFY_ROOT / "keys.csv"


def mask(token: str) -> str:
    return f"{token[:14]}...{token[-4:]}"


def check_key(name: str, token: str) -> dict:
    try:
        r1 = requests.get("https://api.apify.com/v2/users/me", params={"token": token}, timeout=15)
        r2 = requests.get("https://api.apify.com/v2/users/me/limits", params={"token": token}, timeout=15)
        if r1.status_code != 200 or r2.status_code != 200:
            return {"name": name, "token": mask(token), "ok": False, "error": f"HTTP {r1.status_code}/{r2.status_code}"}

        me = r1.json()["data"]
        limits = r2.json()["data"]

        plan_id = me.get("plan", {}).get("id", "?")
        max_usd = limits["limits"]["maxMonthlyUsageUsd"]
        used_usd = limits["current"]["monthlyUsageUsd"]
        remaining = round(max_usd - used_usd, 4) if max_usd else None
        max_concurrent = limits["limits"].get("maxConcurrentActorJobs", "?")

        return {
            "name": name,
            "username": me.get("username"),
            "token": mask(token),
            "ok": True,
            "plan": plan_id,
            "max_usd": max_usd,
            "used_usd": round(used_usd, 4),
            "remaining_usd": remaining,
            "max_concurrent_jobs": max_concurrent,
        }
    except Exception as e:
        return {"name": name, "token": mask(token), "ok": False, "error": str(e)}


def main():
    with open(KEYS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"Checking {len(rows)} keys...\n")
    results = []
    for row in rows:
        name = row["Name"].strip()
        token = row["Keys"].strip()
        res = check_key(name, token)
        results.append(res)
        if res["ok"]:
            print(f"  {name:<25} {res['token']:<22} plan={res['plan']:<10} "
                  f"remaining=${res['remaining_usd']:<8} (used ${res['used_usd']}/{res['max_usd']}) "
                  f"concurrent_jobs={res['max_concurrent_jobs']}")
        else:
            print(f"  {name:<25} {res['token']:<22} FAILED: {res['error']}")

    ok_results = [r for r in results if r["ok"]]
    total_remaining = sum(r["remaining_usd"] for r in ok_results if r["remaining_usd"] is not None)
    total_max = sum(r["max_usd"] for r in ok_results if r["max_usd"] is not None)

    print(f"\n{'='*70}")
    print(f"Valid keys       : {len(ok_results)}/{len(rows)}")
    print(f"Total plan cap   : ${total_max}")
    print(f"Total remaining  : ${round(total_remaining, 2)}")
    print(f"{'='*70}")

    import json
    out = APIFY_ROOT / "output" / "team_keys_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nFull status saved -> {out}")


if __name__ == "__main__":
    main()
