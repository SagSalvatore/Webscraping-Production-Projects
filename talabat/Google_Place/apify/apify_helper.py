"""
apify_helper.py
---------------
Lightweight local replacement for the Apify MCP connector (which fails to
install via the Claude Desktop extension marketplace — ENOTEMPTY errors on
node_modules during extraction).

Wraps the Apify REST API v2 directly using APIFY_API_TOKEN from the root
.env. Covers everything the MCP connector would have given us:
  - search actors in the Store
  - fetch actor details / README / input schema
  - run an actor (sync or async) and wait for completion
  - pull dataset items from a run
  - check account plan / usage / cost info

Usage (CLI):
  python apify_helper.py whoami
  python apify_helper.py info compass/crawler-google-places
  python apify_helper.py search "google places"
  python apify_helper.py run compass/crawler-google-places input.json
  python apify_helper.py dataset <dataset_id>
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(r"C:\Users\SagarSingh\Downloads\Google_Place\.env"), override=True)

TOKEN = os.getenv("APIFY_API_TOKEN", "")
BASE  = "https://api.apify.com/v2"

if not TOKEN:
    raise RuntimeError("APIFY_API_TOKEN not set in .env")


def _get(path, **params):
    params["token"] = TOKEN
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path, json_body=None, **params):
    params["token"] = TOKEN
    r = requests.post(f"{BASE}{path}", params=params, json=json_body, timeout=60)
    r.raise_for_status()
    return r.json()


# ── Account / plan info ──────────────────────────────────────────────────────
def whoami():
    return _get("/users/me")["data"]


def plan_pricing():
    d = whoami()
    return d["plan"]["planPricing"]["chargeableServiceUnitPricesUsd"]


# ── Actor discovery ───────────────────────────────────────────────────────────
def search_actors(query: str, limit: int = 10):
    return _get("/store", search=query, limit=limit)["data"]["items"]


def actor_info(actor_id: str):
    """actor_id like 'compass/crawler-google-places' or 'username~actor-name'"""
    slug = actor_id.replace("/", "~")
    return _get(f"/acts/{slug}")["data"]


def actor_input_schema(actor_id: str):
    info = actor_info(actor_id)
    return info.get("taggedBuilds", {}).get("latest", {}) or info.get("exampleRunInput", {})


# ── Run an actor ──────────────────────────────────────────────────────────────
def run_actor(actor_id: str, run_input: dict, wait_secs: int = 300, poll_interval: int = 5):
    """
    Starts an actor run and polls until it finishes (or wait_secs elapses).
    Returns the run object (includes 'defaultDatasetId' to fetch results from).
    """
    slug = actor_id.replace("/", "~")
    run = _post(f"/acts/{slug}/runs", json_body=run_input)["data"]
    run_id = run["id"]
    print(f"  Run started: {run_id}  (status={run['status']})", flush=True)

    elapsed = 0
    while elapsed < wait_secs:
        time.sleep(poll_interval)
        elapsed += poll_interval
        status = _get(f"/actor-runs/{run_id}")["data"]
        print(f"  [{elapsed}s] status={status['status']}", flush=True)
        if status["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return status

    print("  Wait time exceeded — run may still be going. Check dashboard.")
    return _get(f"/actor-runs/{run_id}")["data"]


def get_dataset_items(dataset_id: str, limit: int = None, offset: int = 0):
    params = {"format": "json", "clean": "true", "offset": offset}
    if limit:
        params["limit"] = limit
    r = requests.get(f"{BASE}/datasets/{dataset_id}/items", params={**params, "token": TOKEN}, timeout=60)
    r.raise_for_status()
    return r.json()


def estimate_actor_cost(compute_units: float):
    """Rough cost estimate using account's ACTOR_COMPUTE_UNITS unit price."""
    price = plan_pricing().get("ACTOR_COMPUTE_UNITS", 0.2)
    return round(compute_units * price, 4)


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "whoami":
        me = whoami()
        print(f"Username : {me['username']}")
        print(f"Plan     : {me['plan']['id']} (${me['plan']['monthlyBasePriceUsd']}/mo, "
              f"${me['plan']['monthlyUsageCreditsUsd']} usage credits)")
        print(f"Compute unit price   : ${me['plan']['planPricing']['chargeableServiceUnitPricesUsd']['ACTOR_COMPUTE_UNITS']}")
        print(f"Max concurrent runs  : {me['plan']['maxConcurrentActorRuns']}")
        print(f"Max actor memory GB  : {me['plan']['maxActorMemoryGbytes']}")

    elif cmd == "info":
        actor_id = sys.argv[2]
        info = actor_info(actor_id)
        print(json.dumps({
            "name": info.get("name"),
            "title": info.get("title"),
            "description": (info.get("description") or "")[:300],
            "stats": info.get("stats", {}),
            "defaultRunOptions": info.get("defaultRunOptions"),
            "pricingInfo": info.get("pricingInfo"),
        }, indent=2))

    elif cmd == "search":
        query = sys.argv[2]
        results = search_actors(query)
        for a in results[:10]:
            print(f"  {a['name']:<40} runs={a.get('stats',{}).get('totalRuns','?'):<10} {a.get('title','')}")

    elif cmd == "run":
        actor_id = sys.argv[2]
        input_path = sys.argv[3]
        run_input = json.loads(Path(input_path).read_text(encoding="utf-8"))
        result = run_actor(actor_id, run_input)
        print(json.dumps(result, indent=2)[:1000])

    elif cmd == "dataset":
        dataset_id = sys.argv[2]
        items = get_dataset_items(dataset_id, limit=5)
        print(json.dumps(items, indent=2)[:2000])

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
