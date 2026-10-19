"""Tests for the hourly Prefect flow, with the real `smog` commands replaced by a fake.

Checks the two promises in flow.py:
  - a failed download does not stop the run (data just shows as stale),
  - a failed quality gate stops the run, and the last successful run is remembered.
"""
import json

import pytest
from prefect.testing.utilities import prefect_test_harness

from smog import config
from smog import flow as hourly_flow


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    with prefect_test_harness():
        yield


@pytest.fixture
def fake_smog(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    calls, failing = [], set()

    def fake(*args):
        calls.append(args[0])
        if args[0] in failing:
            raise RuntimeError(f"`smog {args[0]}` failed with exit code 1")
        return f"{args[0]} ok"

    monkeypatch.setattr(hourly_flow, "smog", fake)
    # No 2-minute waits between retries inside tests.
    for name in ("fetch_openaq", "fetch_firms", "fetch_openmeteo"):
        monkeypatch.setattr(hourly_flow, name, getattr(hourly_flow, name).with_options(retry_delay_seconds=0))
    return calls, failing


def status(tmp_path):
    return json.loads((tmp_path / hourly_flow.STATUS_FILE).read_text())


def test_all_steps_run_in_order(fake_smog, tmp_path):
    calls, _ = fake_smog
    steps = hourly_flow.hourly()
    assert calls == ["fetch-openaq", "fetch-firms", "fetch-openmeteo", "verify",
                     "build-cleaned", "quality", "build-gold", "predict"]
    assert steps["gold"] == "build-gold ok"
    s = status(tmp_path)
    assert s["last_run"]["status"] == "success" and s["last_success"] == s["last_run"]


def test_failed_download_is_retried_then_run_continues(fake_smog, tmp_path):
    calls, failing = fake_smog
    failing.add("fetch-firms")
    steps = hourly_flow.hourly()
    assert calls.count("fetch-firms") == 3                  # first try + 2 retries
    assert steps["firms"].startswith("FAILED")
    assert "build-gold" in calls                             # the rest still ran
    assert status(tmp_path)["last_run"]["status"] == "success"


def test_export_runs_includes_failed_runs(fake_smog, tmp_path):
    import csv
    from datetime import datetime, timedelta, timezone
    from smog import export_runs

    calls, failing = fake_smog
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    hourly_flow.hourly()
    failing.add("quality")
    with pytest.raises(Exception):
        hourly_flow.hourly()
    summary = export_runs.export(start, datetime.now(timezone.utc) + timedelta(minutes=1))

    rows = list(csv.DictReader((tmp_path / "prefect_runs.csv").open(encoding="utf-8")))
    states = [r["state_type"] for r in rows if r["flow_run_name"]]
    assert "COMPLETED" in states and "FAILED" in states     # failures are exported too, never hidden
    assert summary.startswith(f"wrote {len(rows)} runs")
    assert all(r["start_time"] for r in rows)


def test_failed_quality_gate_stops_run_and_keeps_last_success(fake_smog, tmp_path):
    calls, failing = fake_smog
    hourly_flow.hourly()                                     # a good run first
    good = status(tmp_path)["last_success"]

    failing.add("quality")
    calls.clear()
    with pytest.raises(Exception):
        hourly_flow.hourly()
    assert "build-gold" not in calls                         # gold is not rebuilt
    s = status(tmp_path)
    assert s["last_run"]["status"] == "failed" and "quality" in s["last_run"]["steps"]["error"]
    assert s["last_success"] == good                         # dashboard still knows the last good run
