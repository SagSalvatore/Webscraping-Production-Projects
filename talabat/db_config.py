"""One place for the local PostgreSQL credentials, read from talabat/.env.

Thirty-one scripts across menu/, menu_refresh/, export/, ingredients/,
map_version2/ and Postgre/ used to carry the password as a literal. That put a
real credential into every diff and would have written it into GitHub history
permanently, so it now lives in talabat/.env (git-ignored) and is read here.

This module loads that .env ITSELF rather than relying on the caller, because
most of those scripts never called load_dotenv() - they had no reason to when
the password was inlined. Importing this module is therefore enough; nothing
else in a script has to change.

    from db_config import PG_PARAMS, PG_DSN, PG_PASSWORD

    conn = psycopg2.connect(**PG_PARAMS)          # psycopg2 / psycopg
    conn = await asyncpg.connect(PG_DSN)          # asyncpg

PG_PARAMS is handed out as a fresh dict each time it is read, so a caller that
mutates it (adding options=..., or a different database) cannot corrupt another
caller's copy.

Missing PG_PASSWORD raises immediately with the fix spelled out. That is
deliberate: a loud failure at import beats connecting to the wrong place, or a
psycopg2 "authentication failed" ten frames deep.
"""
import os
from pathlib import Path
from urllib.parse import quote

ENV_FILE = Path(__file__).resolve().parent / ".env"

DEFAULTS = {
    "PG_HOST": "localhost",
    "PG_PORT": "5432",
    "PG_DATABASE": "RestaurantIntelligence",
    "PG_USER": "postgres",
}


def _load_env(path=ENV_FILE):
    """Put path's KEY=VALUE pairs into os.environ without overwriting anything
    already set, so a real environment variable still wins over the file."""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(path, override=False)
        return
    # python-dotenv absent: a deliberately small parser. It handles KEY=VALUE,
    # comments, blank lines and surrounding quotes - not export/multiline.
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

PG_HOST = os.environ.get("PG_HOST") or DEFAULTS["PG_HOST"]
PG_PORT = int(os.environ.get("PG_PORT") or DEFAULTS["PG_PORT"])
PG_DATABASE = os.environ.get("PG_DATABASE") or DEFAULTS["PG_DATABASE"]
PG_USER = os.environ.get("PG_USER") or DEFAULTS["PG_USER"]
PG_PASSWORD = os.environ.get("PG_PASSWORD")

if not PG_PASSWORD:
    raise RuntimeError(
        "PG_PASSWORD is not set.\n"
        f"  Add it to {ENV_FILE}   ->   PG_PASSWORD=your_postgres_password\n"
        "  (that file is git-ignored; see talabat/.env.example for the full list)"
    )


class _Params(dict):
    """dict that also works as a callable, so PG_PARAMS and PG_PARAMS() both
    give a connect-ready mapping. Kept because some call sites splat it
    directly (**PG_PARAMS) and others store and edit it."""

    def __call__(self, **overrides):
        out = dict(self)
        out.update(overrides)
        return out


PG_PARAMS = _Params(
    host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
    user=PG_USER, password=PG_PASSWORD,
)

# quote() so a password containing @ : / ? # cannot break the URI. The literals
# this replaced were written pre-encoded by hand, which is exactly the bug this
# avoids: an encoded password in the file and a raw one in .env drift apart.
PG_DSN = (
    f"postgresql://{quote(PG_USER, safe='')}:{quote(PG_PASSWORD, safe='')}"
    f"@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
)

__all__ = ["PG_PARAMS", "PG_DSN", "PG_PASSWORD", "PG_HOST", "PG_PORT",
           "PG_DATABASE", "PG_USER", "ENV_FILE"]
