"""
supabase_loader.py — Async Supabase restaurant loader for classification pipeline.

Pulls all rows from talabat_restaurants using 1,000-row pagination loop
to bypass PostgREST's default cap.

Usage:
    df = await load_restaurants(env_path=Path("../talabat/.env"))
    df = await load_restaurants(env_path=..., limit=20)   # test mode
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

_COLS = ",".join([
    "branch_id",
    "restaurant_name",
    "serves_cuisine",
    "key_cuisines",
    "restaurant_type",
    "outlet_type",
    "chained_outlet_type",
])


async def load_restaurants(
    env_path: Path | str,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Pull restaurant records from talabat_restaurants table.

    Parameters
    ----------
    env_path : Path to talabat/.env (contains SUPABASE_URL + SUPABASE_ANON_KEY)
    limit    : If set, stop after this many rows (for --test / --limit modes)

    Returns
    -------
    pd.DataFrame with columns:
        branch_id, restaurant_name, serves_cuisine, key_cuisines,
        restaurant_type, outlet_type, chained_outlet_type
    """
    try:
        from supabase._async.client import AsyncClient, create_client
    except ImportError:
        from supabase import acreate_client as create_client, AsyncClient  # type: ignore

    load_dotenv(Path(env_path))
    url = os.getenv("SUPABASE_URL", "")
    key = (
        os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    )
    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in the .env file"
        )

    client: AsyncClient = await create_client(url, key)

    rows: list[dict] = []
    page_size = 1000
    offset    = 0

    print("Loading restaurants from Supabase ...")
    while True:
        resp = (
            await client
            .from_("talabat_restaurants")
            .select(_COLS)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        print(f"  {len(rows):,} rows fetched ...", flush=True)

        if len(batch) < page_size:
            break
        offset += page_size

        if limit is not None and len(rows) >= limit:
            rows = rows[:limit]
            break

    try:
        await client.aclose()
    except AttributeError:
        pass

    df = pd.DataFrame(rows)
    print(f"Total: {len(df):,} restaurants loaded.\n")
    return df
