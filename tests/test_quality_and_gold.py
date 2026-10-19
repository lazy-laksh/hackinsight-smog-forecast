"""Tests for the Data Quality Agent and the gold layer.

The test data copies the real problems found by `python -m smog profile`:
a 667 µg/m³ one-hour spike, a sensor stuck on one value, runs of 0.0,
short and long gaps, a sensor that went silent, weather rows that were
still in the future when fetched, and a satellite missing a whole month.
"""
import csv
from datetime import datetime, timezone

import pandas as pd
import pytest

from smog import config, gold, quality, warehouse

NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)
H = pd.Timedelta(hours=1)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", data / "raw")
    monkeypatch.setattr(config, "CLEANED_DIR", data / "cleaned")
    monkeypatch.setattr(config, "GOLD_DIR", data / "gold")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", data / "warehouse.duckdb")
    monkeypatch.setattr(config, "MANIFEST_PATH", data / "manifest.csv")
    build_cleaned_tables()
    return tmp_path


def hours(start, n):
    return [pd.Timestamp(start, tz="UTC") + k * H for k in range(n)]


def build_cleaned_tables():
    rows = []
    # Sensor 1 (Lahore): smooth values with a spike, a 1-hour gap, a 5-hour gap and a run of zeros.
    for i, ts in enumerate(hours("2026-10-18 00:00", 40)):
        if ts.hour == 10 and ts.day == 18:                 # 1 missing hour -> should be filled
            continue
        if ts.day == 18 and 12 <= ts.hour <= 16:           # 5 missing hours -> long gap, stays missing
            continue
        value = 50.0 + (i % 5)
        if ts == pd.Timestamp("2026-10-18 05:00", tz="UTC"):
            value = 667.0                                  # one-hour spike, like the real May case
        if ts.day == 18 and 20 <= ts.hour <= 23:
            value = 0.0                                    # zeros, like sensor 13817565
        rows.append((1, ts, value))
    # Sensor 2 (Lahore): normal, then stuck at exactly 123.0 for 8 hours (10:00-17:00), then normal.
    for i, ts in enumerate(hours("2026-10-18 00:00", 40)):
        rows.append((2, ts, 123.0 if 10 <= i < 18 else 60.0 + (i % 7)))
    # Sensor 3 (Karachi): stops reporting at 02:00 on the 19th -> stale at 16:00.
    for i, ts in enumerate(hours("2026-10-18 00:00", 27)):
        rows.append((3, ts, 30.0 + (i % 4)))

    pm = pd.DataFrame(rows, columns=["sensor_id", "ts_utc", "pm25"]).astype({"sensor_id": "int64"})
    pm["observed_count"] = 2
    pm["expected_count"] = 12
    pm["percent_coverage"] = 16.7
    pm["has_flags"] = False
    pm["source_file"] = "data/raw/openaq/hours/sensor_x/2026-10.json"
    sensors = pd.DataFrame({"sensor_id": [1, 2, 3], "city_id": ["lahore", "lahore", "karachi"],
                            "location_name": ["A", "B", "C"]}).astype({"sensor_id": "int64"})

    weather_file = "data/raw/openmeteo_weather/lahore/2026-10.json"
    w_hours = hours("2026-10-18 00:00", 48)                # until 23:00 on the 19th
    weather = pd.DataFrame({"city_id": "lahore", "ts_utc": w_hours, "temperature_2m": 25.0,
                            "relative_humidity_2m": 60.0, "wind_speed_10m": 5.0, "wind_direction_10m": 350.0,
                            "precipitation": 0.0, "boundary_layer_height": 400.0, "surface_pressure": 990.0,
                            "source_file": weather_file})
    config.MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.MANIFEST_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "source", "query", "fetched_at_utc", "sha256", "bytes", "licence", "publisher"])
        writer.writerow([weather_file, "openmeteo_weather", "q", "2026-10-19T15:30:00+00:00", "x", "1", "CC BY 4.0", "Open-Meteo"])

    fires = pd.DataFrame({
        "family": ["VIIRS_NOAA20"] * 250 + ["VIIRS_SNPP"] * 240 + ["VIIRS_NOAA20"] * 2,
        "product": ["VIIRS_NOAA20_NRT"] * 250 + ["VIIRS_SNPP_NRT"] * 240 + ["VIIRS_NOAA20_NRT"] * 2,
        # 250 NOAA-20 fires in Sept and Oct; SNPP only in Oct -> SNPP Sept is a product gap
        "acq_ts_utc": [pd.Timestamp("2026-09-10 08:00", tz="UTC")] * 125 + [pd.Timestamp("2026-10-18 08:00", tz="UTC")] * 125
                      + [pd.Timestamp("2026-10-18 08:00", tz="UTC")] * 240
                      + [pd.Timestamp("2026-10-18 21:00", tz="UTC")] * 2,
        "lat": [31.52 + 0.5] * 250 + [31.52] * 240 + [31.52 + 2.5] * 2,     # ~55 km and ~280 km from Lahore
        "lon": [74.36] * 492, "frp": 10.0,
    })
    cams = pd.DataFrame({"city_id": "lahore", "ts_utc": w_hours, "pm2_5": 80.0, "dust": 5.0,
                         "aerosol_optical_depth": 0.7, "source_file": "x"})
    forecast = pd.DataFrame({"city_id": ["lahore"], "issued_utc": [pd.Timestamp("2026-10-19 15:00", tz="UTC")],
                             "ts_utc": [pd.Timestamp("2026-10-20 00:00", tz="UTC")], "temperature_2m": [22.0]})

    with warehouse.connect() as con:
        con.execute("CREATE SCHEMA cleaned")
        for name, df in {"pm25_hourly": pm, "sensors": sensors, "weather_hourly": weather,
                         "fires": fires, "cams_hourly": cams, "weather_forecast": forecast}.items():
            con.register("df", df)
            con.execute(f"CREATE TABLE cleaned.{name} AS SELECT * FROM df")
            con.unregister("df")


def statuses():
    with warehouse.connect(read_only=True) as con:
        return con.execute("SELECT sensor_id, ts_utc, pm25, qc_status, usable FROM qc.pm25_hourly").df()


def decision(check):
    with warehouse.connect(read_only=True) as con:
        return con.execute("SELECT * FROM qc.decisions WHERE \"check\" = ?", [check]).df().iloc[-1]


def test_agent_flags_the_real_problem_types(sandbox):
    summary = quality.run(now=NOW)
    assert "gate 1 PASS, gate 2 PASS" in summary
    qc = statuses()
    at = lambda s, ts: qc[(qc.sensor_id == s) & (qc.ts_utc == pd.Timestamp(ts, tz="UTC"))].iloc[0]

    assert at(1, "2026-10-18 05:00")["qc_status"] == "flag_spike"
    assert at(1, "2026-10-18 04:00")["qc_status"] == "ok"           # its normal neighbour is untouched
    assert at(1, "2026-10-18 21:00")["qc_status"] == "flag_zero"
    assert at(2, "2026-10-18 13:00")["qc_status"] == "flag_stuck"
    assert at(2, "2026-10-18 09:00")["qc_status"] == "ok"
    assert at(2, "2026-10-18 18:00")["qc_status"] == "ok"
    assert not at(1, "2026-10-18 05:00")["usable"]                  # flagged rows are kept but not used


def test_short_gap_filled_and_marked_long_gap_left_missing(sandbox):
    quality.run(now=NOW)
    qc = statuses()
    s1 = qc[qc.sensor_id == 1].set_index("ts_utc")
    filled = s1.loc[pd.Timestamp("2026-10-18 10:00", tz="UTC")]
    assert filled["qc_status"] == "filled" and filled["usable"]
    assert 50 <= filled["pm25"] <= 55                               # between its two neighbours
    for h in range(12, 17):                                         # the 5-hour gap is NOT invented
        assert pd.Timestamp(f"2026-10-18 {h:02d}:00", tz="UTC") not in s1.index
    assert decision("long_gap")["rows_affected"] >= 1


def test_future_weather_removed_and_stale_sensor_reported(sandbox):
    quality.run(now=NOW)
    with warehouse.connect(read_only=True) as con:
        last = con.execute("SELECT max(ts_utc) AS m FROM qc.weather_observed").df()["m"].iloc[0]
        stale = con.execute("SELECT sensor_id FROM qc.stale_sensors").df()
    assert pd.Timestamp(last) <= pd.Timestamp("2026-10-19 15:30", tz="UTC")   # nothing after fetch time
    assert decision("weather_future_rows")["rows_affected"] == 8             # 16:00..23:00
    assert list(stale["sensor_id"]) == [3]


def test_fresh_sources_are_reported_fresh(sandbox):
    quality.run(now=NOW)
    assert decision("source_freshness")["reason"] == "All sources are fresh."
    with warehouse.connect(read_only=True) as con:
        fresh = con.execute("SELECT source, status, age_hours FROM qc.source_freshness").df()
    assert set(fresh["status"]) == {"fresh"}
    assert fresh.set_index("source").loc["openaq", "age_hours"] == 1.0     # newest hour 15:00, now 16:00


def test_fire_product_gap_detected(sandbox):
    quality.run(now=NOW)
    d = decision("fire_product_gap")
    assert "VIIRS_SNPP 2026-09" in d["reason"] and "VIIRS_NOAA20 only" in d["reason"]


def test_every_decision_is_logged_with_reason_and_timestamp(sandbox):
    quality.run(now=NOW)
    log = pd.read_csv(config.ROOT / quality.LOG_PATH_NAME)
    assert {"spike", "zero", "stuck", "short_gap", "long_gap", "weather_future_rows"} <= set(log["check"])
    assert set(log["action"]) <= {"fix", "fill", "flag", "info"}
    assert log["reason"].str.len().min() > 20
    assert (log["decided_at_utc"] == NOW.isoformat(timespec="seconds")).all()


def test_gate2_fails_when_data_is_stale_and_gold_keeps_last_version(sandbox):
    quality.run(now=NOW)
    gold.run()
    with warehouse.connect(read_only=True) as con:
        before = con.execute("SELECT count(*) FROM gold.city_hourly").fetchone()[0]

    two_days_later = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
    with pytest.raises(quality.QualityGateFailed, match="gate 2 FAIL"):
        quality.run(now=two_days_later)
    with warehouse.connect(read_only=True) as con:
        after = con.execute("SELECT count(*) FROM gold.city_hourly").fetchone()[0]
        gates = con.execute("SELECT gate2_passed FROM qc.gate_results ORDER BY decided_at_utc").df()
    assert after == before                                  # gold untouched: last good version stays
    assert list(gates["gate2_passed"]) == [True, False]


def test_gold_tables_and_row_ids(sandbox):
    quality.run(now=NOW)
    summary = gold.run()
    assert "city_daily" in summary
    with warehouse.connect(read_only=True) as con:
        hourly = con.execute("SELECT * FROM gold.city_hourly WHERE city_id='lahore'").df()
        daily = con.execute("SELECT * FROM gold.city_daily").df()
        fires = con.execute("SELECT * FROM gold.fires_daily WHERE city_id='lahore'").df()
        weather = con.execute("SELECT * FROM gold.weather_daily").df()

    # The spike hour uses only sensor 2's value (sensor 1's 667 was flagged).
    spike_hour = hourly[hourly.ts_utc == pd.Timestamp("2026-10-18 05:00", tz="UTC")].iloc[0]
    assert spike_hour["pm25_median"] < 200 and spike_hour["n_sensors"] == 1
    # 10:00: sensor 1's value is a fill and sensor 2 is stuck, so the fill is the only usable value.
    ten = hourly[hourly.ts_utc == pd.Timestamp("2026-10-18 10:00", tz="UTC")].iloc[0]
    assert ten["n_filled"] == 1 and ten["n_sensors"] == 1
    # Estimates never outvote measurements: no other hour uses a filled value.
    assert hourly["n_filled"].sum() == 1
    assert hourly["row_id"].is_unique and daily["row_id"].is_unique
    assert hourly["row_id"].iloc[0].startswith("cityhour|lahore|2026")

    # Days are Pakistan days: 2026-10-18 19:00 UTC is already 2026-10-19 in Lahore.
    assert set(daily["day_pkt"].astype(str)) >= {"2026-10-18", "2026-10-19"}
    # Fires: 125 at ~55 km on 18 Oct (13:00 PKT) and 2 at ~280 km late that night (19 Oct PKT).
    day18 = fires[fires.day_pkt.astype(str) == "2026-10-18"].iloc[0]
    assert day18["fires_100km"] == 125 and day18["fires_300km"] == 125
    day19 = fires[fires.day_pkt.astype(str) == "2026-10-19"].iloc[0]
    assert day19["fires_100km"] == 0 and day19["fires_300km"] == 2
    # Wind direction is averaged as a vector (350° stays near north, not 175°).
    assert weather["wind_dir_mean"].iloc[0] == 350
    assert (config.GOLD_DIR / "city_daily.parquet").exists()
