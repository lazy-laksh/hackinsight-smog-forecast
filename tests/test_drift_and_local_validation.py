"""Drift monitor and Punjab EPA local validation (pure functions, no network, no warehouse)."""
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from smog import drift, local_validation


# --- drift -------------------------------------------------------------------------------------------

def test_psi_is_small_for_the_same_distribution_and_large_for_a_shift():
    rng = np.random.default_rng(0)
    ref = rng.normal(100, 20, 400)
    assert drift.psi(ref, rng.normal(100, 20, 400)) < 0.10
    assert drift.psi(ref, rng.normal(160, 20, 60)) > 0.25
    assert drift.psi_status(0.05) == "ok" and drift.psi_status(0.2) == "watch" and drift.psi_status(0.4) == "drift"


def test_psi_survives_a_constant_reference():
    assert drift.psi([0] * 50, [0] * 10) < 0.10
    assert drift.psi([0] * 50, [5] * 10) > 0.25


def test_small_samples_of_the_same_distribution_are_not_called_drift():
    rng = np.random.default_rng(1)
    ref = pd.DataFrame({"day_pkt": pd.date_range("2025-09-29", "2025-11-10", freq="D").repeat(4), "wind_mean_kmh": 0.0})
    ref["wind_mean_kmh"] = rng.normal(8, 2, len(ref))
    cur = pd.DataFrame({"day_pkt": pd.date_range("2026-10-06", "2026-10-19", freq="D").repeat(4)})
    cur["wind_mean_kmh"] = rng.normal(8, 2, len(cur))
    statuses = [drift.input_drift({"weather_daily": pd.concat([ref, cur])}, date(2026, 10, 20))[0]["status"]]
    assert statuses == ["ok"]
    assert 0.08 < drift.noise_floor(172, 56) < 0.1


def test_reference_is_the_same_weeks_last_year():
    w = drift.windows(date(2026, 10, 21))
    assert w["recent"] == (date(2026, 10, 7), date(2026, 10, 20))
    assert w["reference"] == (date(2025, 9, 30), date(2025, 10, 27))     # same weeks, +-7 days; no November


def _daily(start, days, value, complete=True):
    d = pd.date_range(start, periods=days, freq="D")
    return pd.DataFrame([{"city_id": c, "day_pkt": x.date(), "pm25_mean": value + i % 7, "complete": complete}
                         for c in ("lahore", "karachi") for i, x in enumerate(d)])


def test_input_drift_flags_a_shift_and_reports_not_enough_data():
    hist = pd.concat([_daily("2025-09-25", 50, 60), _daily("2026-10-01", 19, 200)])
    rows = {r["input"]: r for r in drift.input_drift({"city_daily": hist}, date(2026, 10, 20))}
    assert rows["PM2.5 daily mean"]["status"] == "drift"
    short = _daily("2026-10-01", 19, 60)                       # no history one year ago
    rows = {r["input"]: r for r in drift.input_drift({"city_daily": short}, date(2026, 10, 20))}
    assert rows["PM2.5 daily mean"]["status"] == "not enough data"


def test_performance_drift_compares_live_error_with_the_test_error():
    metrics = {"test": {"model": {"mae": 10.0}}}
    sc = pd.DataFrame({"status": ["final"] * 10, "error": [-11, 12, 9, -10, 11, 10, -9, 12, 10, 11],
                       "persistence_error": [15] * 10})
    assert drift.performance_drift(sc, metrics)["status"] == "ok"
    sc["error"] = sc["error"] * 2
    assert drift.performance_drift(sc, metrics)["status"] == "drift"
    assert drift.performance_drift(sc.head(3), metrics)["status"] == "watch"     # too few days to call drift
    assert drift.performance_drift(pd.DataFrame(), metrics)["status"] == "not enough data"


def test_report_overall_is_the_worst_known_status(tmp_path, monkeypatch):
    monkeypatch.setattr(drift.config, "ROOT", tmp_path)
    hist = pd.concat([_daily("2025-09-25", 50, 60), _daily("2026-10-01", 19, 200)])
    r = drift.report({"city_daily": hist}, pd.DataFrame(), {"test": {"model": {"mae": 10}}},
                     datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    assert r["overall"] == "drift" and r["performance"]["status"] == "not enough data"
    drift.log(r)
    drift.log(r)
    log = pd.read_csv(tmp_path / "data/logs/drift_log.csv")
    assert len(log) == 2 and "psi_pm25_mean" in log.columns


# --- local validation ------------------------------------------------------------------------------

def test_reference_file_is_valid_and_cites_its_sources():
    ref = local_validation.load_reference()
    assert {"time_pkt", "city_id", "station", "pm25_ugm3", "source_url"} <= set(ref.columns)
    assert ref["source_url"].str.startswith("http").all()
    assert ref["pm25_ugm3"].between(0, 1000).all()


def test_epa_hour_is_matched_to_our_hour_starting_one_hour_earlier():
    ref = pd.DataFrame({"time_pkt": pd.to_datetime(["2026-10-19 03:00"] * 2), "city_id": ["lahore"] * 2,
                        "station": ["A", "B"], "pm25_ugm3": [100.0, 200.0], "source_url": ["http://x"] * 2})
    snap = local_validation.epa_snapshots(ref)
    assert snap["epa_mean"].iloc[0] == 150 and snap["epa_stations"].iloc[0] == 2
    assert str(snap["hour_start_utc"].iloc[0]) == "2026-10-18 21:00:00+00:00"      # 03:00 PKT end -> 21:00 UTC start
    hourly = pd.DataFrame({"city_id": "lahore", "ts_utc": pd.date_range("2026-10-17 22:00", "2026-10-18 21:00", freq="h", tz="UTC"),
                           "pm25_median": 120.0, "n_sensors": 30})
    hourly.loc[hourly.index[-1], "pm25_median"] = 180.0
    out = local_validation.compare(snap, hourly)
    row = out.iloc[0]
    assert row["ours_same_hour"] == 180.0 and row["difference"] == 30.0 and row["ratio"] == 1.2
    assert bool(row["inside_epa_station_range"]) and row["our_sensors"] == 30
    assert 120 < row["ours_24h_mean"] < 125
    s = local_validation.summarise(out)
    assert s["hours_compared"] == 1 and s["mean_difference"] == 30.0
    md = local_validation.write_markdown(out, s)
    assert "30.0 µg/m³ higher" in md


def test_missing_hours_are_reported_not_invented():
    ref = pd.DataFrame({"time_pkt": pd.to_datetime(["2026-10-19 03:00"]), "city_id": ["lahore"],
                        "station": ["A"], "pm25_ugm3": [100.0], "source_url": ["http://x"]})
    out = local_validation.compare(local_validation.epa_snapshots(ref),
                                   pd.DataFrame(columns=["city_id", "ts_utc", "pm25_median", "n_sensors"]))
    assert out["ours_same_hour"].isna().all() and local_validation.summarise(out) == {"hours_compared": 0}
