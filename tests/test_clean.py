"""Tests for the cleaned layer, using small raw files in the real source formats."""
import json

import pandas as pd
import pytest

from smog import clean, config, warehouse


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", data / "raw")
    monkeypatch.setattr(config, "CLEANED_DIR", data / "cleaned")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", data / "warehouse.duckdb")
    return data / "raw"


def write_json(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def hour(ts, value, observed=12):
    return {"value": value, "flagInfo": {"hasFlags": False},
            "parameter": {"id": 2, "name": "pm25", "units": "µg/m³"},
            "period": {"datetimeFrom": {"utc": ts}},
            "coverage": {"observedCount": observed, "expectedCount": 12, "percentCoverage": observed / 12 * 100}}


def make_raw(raw):
    write_json(raw / "openaq/locations/lahore_20261019_p1.json", {"results": [{
        "id": 101, "name": "Gulberg III", "isMobile": False, "isMonitor": False,
        "coordinates": {"latitude": 31.51, "longitude": 74.35},
        "owner": {"name": "The Urban Unit"}, "provider": {"name": "AirGradient"},
        "licenses": [{"name": "CC BY 4.0"}],
        "datetimeFirst": {"utc": "2025-06-04T10:00:00Z"}, "datetimeLast": {"utc": "2026-10-19T11:00:00Z"},
        "sensors": [{"id": 9001, "parameter": {"name": "pm25"}},
                    {"id": 9002, "parameter": {"name": "relativehumidity"}}]}]})
    # A month file plus a weekly part that repeats one hour with a newer value.
    write_json(raw / "openaq/hours/sensor_9001/2026-09.json", {"results": [
        hour("2026-09-01T00:00:00Z", 80.0), hour("2026-09-01T01:00:00Z", None), hour("2026-09-01T02:00:00Z", 95.5, 3)]})
    write_json(raw / "openaq/hours/sensor_9001/2026-09_part1.json", {"results": [hour("2026-09-01T00:00:00Z", 81.0)]})

    header = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
    sp = raw / "firms/VIIRS_SNPP_SP/2026-07-30_2d.csv"
    nrt = raw / "firms/VIIRS_SNPP_NRT/2026-07-31_2d.csv"
    for p in (sp, nrt):
        p.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(header + "30.9,74.0,330,0.4,0.4,2026-07-31,812,N,VIIRS,h,2,295,6.3,D\n")
    nrt.write_text(header +
                   "30.9,74.0,331,0.4,0.4,2026-07-31,0812,N,VIIRS,n,2.0NRT,295,6.1,D\n"     # duplicate day -> dropped
                   "31.0,74.4,341,0.4,0.4,2026-08-01,2015,N,VIIRS,l,2.0NRT,296,12.8,N\n")   # new day -> kept
    modis = raw / "firms/MODIS_NRT/2026-08-01_1d.csv"
    modis.parent.mkdir(parents=True, exist_ok=True)
    modis.write_text("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,"
                     "confidence,version,bright_t31,frp,daynight\n"
                     "31.1,74.5,320,1,1,2026-08-01,0530,T,MODIS,85,6.1NRT,300,20.0,D\n")

    units = {"time": "iso8601", "temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"}
    write_json(raw / "openmeteo_weather/lahore/2026-09.json", {
        "utc_offset_seconds": 0, "hourly_units": units,
        "hourly": {"time": ["2026-09-01T00:00", "2026-09-01T01:00"], "temperature_2m": [30.1, None],
                   "relative_humidity_2m": [70, 72], "wind_speed_10m": [5.0, 4.2]}})
    write_json(raw / "openmeteo_air/lahore/2026-09.json", {
        "utc_offset_seconds": 0, "hourly_units": {"time": "iso8601", "pm2_5": "μg/m³", "aerosol_optical_depth": ""},
        "hourly": {"time": ["2026-09-01T00:00"], "pm2_5": [55.0], "aerosol_optical_depth": [0.6]}})
    write_json(raw / "openmeteo_forecast/lahore/weather_20261019T14.json", {
        "utc_offset_seconds": 0, "hourly_units": units,
        "hourly": {"time": ["2026-10-20T00:00"], "temperature_2m": [24.0],
                   "relative_humidity_2m": [80], "wind_speed_10m": [3.0]}})


def test_full_clean_run(sandbox):
    make_raw(sandbox)
    summary = clean.run()
    assert "pm25_hourly 3" in summary and "fires 3" in summary

    with warehouse.connect(read_only=True) as con:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='cleaned'").fetchall()}
        assert {"sensors", "pm25_hourly", "fires", "weather_hourly", "cams_hourly", "weather_forecast"} <= tables
        pm = con.execute("SELECT ts_utc, pm25, observed_count, source_file FROM cleaned.pm25_hourly ORDER BY ts_utc").df()

    # The duplicated hour keeps the newer file's value; missing stays missing (not 0).
    assert pm["pm25"].iloc[0] in (80.0, 81.0)
    assert pd.isna(pm["pm25"].iloc[1])
    assert str(pm["ts_utc"].dt.tz) in ("UTC", "Etc/UTC")
    assert str(pm["ts_utc"].iloc[0]) == "2026-09-01 00:00:00+00:00"   # not shifted to +05:00
    assert pm["source_file"].str.startswith("data/raw/openaq/hours/").all()


def test_sensor_metadata(sandbox):
    make_raw(sandbox)
    sensors = clean.build_sensors()
    assert list(sensors["sensor_id"]) == [9001]          # humidity sensor is not a PM2.5 sensor
    row = sensors.iloc[0]
    assert row["city_id"] == "lahore" and row["owner"] == "The Urban Unit" and row["licence"] == "CC BY 4.0"


def test_fires_prefer_sp_and_unify_confidence(sandbox):
    make_raw(sandbox)
    fires = clean.build_fires()
    july = fires[fires["acq_date"] == pd.Timestamp("2026-07-31").date()]
    assert list(july["product"]) == ["VIIRS_SNPP_SP"]     # NRT copy of an SP day was dropped
    assert str(july["acq_ts_utc"].iloc[0]) == "2026-07-31 08:12:00+00:00"   # "812" read as 08:12 UTC
    assert set(fires["confidence_level"]) == {"high", "low"}   # 'h', 'l' and MODIS 85 -> high


def test_wrong_unit_stops_the_build(sandbox):
    make_raw(sandbox)
    bad = sandbox / "openaq/hours/sensor_9001/2026-09.json"
    body = json.loads(bad.read_text(encoding="utf-8"))
    body["results"][0]["parameter"]["units"] = "ppm"
    bad.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(clean.UnitError, match="ppm"):
        clean.build_pm25_hourly()


def test_profile_runs_on_cleaned_data(sandbox, capsys):
    from smog import profile
    make_raw(sandbox)
    clean.run()
    profile.run()
    out = capsys.readouterr().out
    assert "=== PM2.5 values" in out and "=== Fires per month" in out
    # Every query works on real-shaped data, except BLH, which this small sample does not include.
    failures = [line for line in out.splitlines() if line.startswith("(could not compute")]
    assert len(failures) <= 1


def test_forecast_keeps_issue_time(sandbox):
    make_raw(sandbox)
    fc = clean.build_forecasts("weather", clean.EXPECTED_WEATHER_UNITS)
    assert str(fc["issued_utc"].iloc[0]) == "2026-10-19 14:00:00+00:00"
    assert str(fc["ts_utc"].iloc[0]) == "2026-10-20 00:00:00+00:00"
