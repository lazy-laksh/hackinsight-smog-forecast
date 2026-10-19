"""One place to open the DuckDB warehouse.

Always use `warehouse.connect()` instead of `duckdb.connect()`.

Why: DuckDB shows timestamps in the computer's own time zone. On a laptop in
Pakistan that is +05:00, so an hour stored as 00:00 UTC would come back as
05:00+05:00. That is the same moment, but code that later takes the "date" or
"hour" of it would silently be 5 hours off. Setting the session to UTC keeps
every time we read back in UTC, which is the rule in our Dataset Declaration.
"""
from __future__ import annotations

import duckdb

from smog import config


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    config.WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=read_only)
    con.execute("SET TimeZone = 'UTC'")
    return con
