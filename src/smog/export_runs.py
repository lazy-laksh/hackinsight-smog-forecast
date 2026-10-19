"""Export Prefect's run history to prefect_runs.csv, exactly as Prefect recorded it.

    python -m smog export-runs --start "2026-10-20T16:00+05:00" --end "2026-10-21T16:00+05:00"

The submission needs the history for Tue 20 Oct 4 PM to Wed 21 Oct 4 PM (PKT),
unedited. This script only reads from the Prefect server; it never changes or
filters out runs (failed runs are included, as they must be).
"""
from __future__ import annotations

import csv
from datetime import datetime

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (FlowRunFilter, FlowRunFilterExpectedStartTime)
from prefect.client.schemas.sorting import FlowRunSort

from smog import config

COLUMNS = ["flow_run_id", "flow_run_name", "deployment_id", "state_type", "state_name",
           "expected_start_time", "start_time", "end_time", "total_run_time_seconds", "run_count", "tags"]


def export(start: datetime, end: datetime, out_name: str = "prefect_runs.csv") -> str:
    rows, offset = [], 0
    run_filter = FlowRunFilter(expected_start_time=FlowRunFilterExpectedStartTime(after_=start, before_=end))
    with get_client(sync_client=True) as client:
        while True:
            batch = client.read_flow_runs(flow_run_filter=run_filter, sort=FlowRunSort.EXPECTED_START_TIME_ASC,
                                          limit=200, offset=offset)
            if not batch:
                break
            for r in batch:
                rows.append({
                    "flow_run_id": str(r.id), "flow_run_name": r.name,
                    "deployment_id": str(r.deployment_id) if r.deployment_id else "",
                    "state_type": r.state_type.value if r.state_type else "",
                    "state_name": r.state_name or "",
                    "expected_start_time": r.expected_start_time.isoformat() if r.expected_start_time else "",
                    "start_time": r.start_time.isoformat() if r.start_time else "",
                    "end_time": r.end_time.isoformat() if r.end_time else "",
                    "total_run_time_seconds": round(r.total_run_time.total_seconds(), 1) if r.total_run_time else "",
                    "run_count": r.run_count,
                    "tags": ";".join(r.tags or []),
                })
            offset += len(batch)

    path = config.ROOT / out_name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    ok = sum(1 for r in rows if r["state_type"] == "COMPLETED")
    return f"wrote {len(rows)} runs ({ok} completed) to {out_name}"
