"""Tests for the forecast model, on synthetic gold tables shaped like the real ones.

The synthetic PM2.5 depends on weather, fires and the season, so a model that
uses those features should beat persistence. The most important tests are:
  - no leakage: features of day D never change when the future changes,
  - the test set is exactly the declared period (from 1 Oct 2026),
  - results are reproducible, and the 80% range is ordered.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from smog import config, model, warehouse

NOW = datetime(2026, 10, 19, 15, 30, tzinfo=timezone.utc)   # 20:30 PKT on 19 Oct
CITY_LEVEL = {"lahore": 70, "peshawar": 55, "islamabad_rawalpindi": 45, "karachi": 35}


def make_gold(seed=0):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-06-01", "2026-10-20", freq="D")
    hourly, weather, fires, cams = [], [], [], []
    for city, level in CITY_LEVEL.items():
        n = len(days)
        blh = 900 - 500 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) * -1 + rng.normal(0, 120, n)
        blh = np.clip(blh, 150, None)
        wind = np.clip(rng.normal(8, 3, n), 0.5, None)
        fire = rng.poisson(np.where(days.month.isin([10, 11, 4, 5]), 60, 5))
        season = 1 + 0.9 * np.cos(2 * np.pi * (days.dayofyear - 330) / 365.25)
        daily = level * season * (600 / blh) ** 0.6 * (8 / wind) ** 0.3 + 0.15 * np.roll(fire, 1)
        daily = daily * rng.lognormal(0, 0.12, n)
        for d, v in zip(days, daily):
            ts = pd.date_range(d - pd.Timedelta(hours=5), periods=24, freq="h", tz="UTC")   # PKT day in UTC
            shape = 1 + 0.35 * np.cos(2 * np.pi * (np.arange(24) - 2) / 24)               # night-time peak
            hourly.append(pd.DataFrame({"city_id": city, "ts_utc": ts, "pm25_median": (v * shape).round(1)}))
        weather.append(pd.DataFrame({"city_id": city, "day_pkt": days, "temp_mean": 25.0, "temp_min": 20.0,
                                     "rh_mean": 60.0, "wind_mean_kmh": wind, "wind_dir_mean": 300.0,
                                     "precip_sum_mm": 0.0, "blh_mean_m": blh, "blh_min_m": blh * 0.4,
                                     "pressure_mean": 990.0, "hours_covered": 24,
                                     "row_id": "weatherday|" + city + "|" + days.strftime("%Y%m%d")}))
        fires.append(pd.DataFrame({"city_id": city, "day_pkt": days, "fires_100km": fire // 3, "fires_300km": fire,
                                   "frp_300km": fire * 8.0, "row_id": "x"}))
        cams.append(pd.DataFrame({"city_id": city, "day_pkt": days,
                                  "cams_pm25_mean": daily * 0.7 * rng.lognormal(0, 0.35, n),   # a noisy model, like real CAMS
                                  "cams_dust_mean": 1.0, "cams_aod_mean": 0.5, "row_id": "x"}))
    return pd.concat(hourly), pd.concat(weather), pd.concat(fires), pd.concat(cams)


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("model")
    mp = pytest.MonkeyPatch()
    data = tmp / "data"
    for name, value in {"ROOT": tmp, "DATA_DIR": data, "WAREHOUSE_PATH": data / "warehouse.duckdb",
                        "GOLD_DIR": data / "gold"}.items():
        mp.setattr(config, name, value)
    hourly, weather, fires, cams = make_gold()
    hourly = hourly[hourly["ts_utc"] <= pd.Timestamp(NOW)]                # nothing after "now"
    with warehouse.connect() as con:
        con.execute("CREATE SCHEMA gold")
        con.register("h", hourly)
        con.execute("CREATE TABLE gold.city_hourly AS SELECT * FROM h")
        con.execute("""CREATE TABLE gold.city_daily AS
            SELECT city_id, CAST(ts_utc + INTERVAL 5 HOUR AS DATE) AS day_pkt, avg(pm25_median) AS pm25_mean,
                   max(pm25_median) AS pm25_max_hour, count(*) >= 18 AS complete,
                   'cityday|' || city_id AS row_id
            FROM gold.city_hourly GROUP BY 1, 2""")
        for name, df in {"weather_daily": weather, "fires_daily": fires, "cams_daily": cams}.items():
            con.register("df", df)
            con.execute(f"CREATE TABLE gold.{name} AS SELECT * FROM df")
            con.unregister("df")
        # A forecast snapshot issued before "now", for the live prediction.
        fc_hours = pd.date_range("2026-10-19 19:00", periods=48, freq="h", tz="UTC")
        con.execute("CREATE SCHEMA cleaned")
        wf = pd.DataFrame([{"city_id": c, "issued_utc": pd.Timestamp("2026-10-19 14:00", tz="UTC"), "ts_utc": t,
                            "temperature_2m": 24.0, "relative_humidity_2m": 65.0, "wind_speed_10m": 4.0,
                            "wind_direction_10m": 310.0, "precipitation": 0.0, "boundary_layer_height": 300.0}
                           for c in CITY_LEVEL for t in fc_hours])
        cf = wf[["city_id", "issued_utc", "ts_utc"]].assign(pm2_5=90.0)
        for name, df in {"weather_forecast": wf, "cams_forecast": cf}.items():
            con.register("df", df)
            con.execute(f"CREATE TABLE cleaned.{name} AS SELECT * FROM df")
            con.unregister("df")
    summary = model.train()
    yield tmp, summary
    mp.undo()


def test_model_beats_persistence_and_writes_reports(trained):
    tmp, summary = trained
    import json
    m = json.loads((tmp / "reports/model_metrics.json").read_text())
    assert m["test"]["model"]["mae"] < m["test"]["baseline_same_info"]["mae"]
    assert "model_without_tomorrow_inputs" in m["test"]                # strict check is always reported
    assert m["test_target_days"][0] >= "2026-10-01"                 # test = declared period only
    assert m["train_target_days"][1] < "2026-10-01"
    assert set(pd.read_csv(tmp / "reports/test_errors_by_city.csv")["city"]) == set(CITY_LEVEL)
    card = (tmp / "reports/MODEL_CARD.md").read_text()
    assert str(m["test"]["model"]["mae"]) in card and "Limitations" in card
    assert "better" in summary


def test_intervals_are_ordered_and_reasonably_calibrated(trained):
    tmp, _ = trained
    preds = pd.read_csv(tmp / "reports/test_predictions.csv")
    assert (preds["p10"] <= preds["prediction"]).all() and (preds["prediction"] <= preds["p90"]).all()
    inside = ((preds["target"] >= preds["p10"]) & (preds["target"] <= preds["p90"])).mean()
    assert 0.4 <= inside <= 1.0


def test_ranges_are_calibrated_on_validation_months_only(trained):
    tmp, _ = trained
    import json
    import joblib
    m = json.loads((tmp / "reports/model_metrics.json").read_text())
    cal, t = m["interval_calibration"], m["test"]
    assert cal["validation_days"] > 0 and cal["widening_ugm3"] >= 0
    assert cal["validation_coverage_after"] >= 0.80               # calibrated where it was chosen
    assert cal["validation_coverage_after"] >= cal["validation_coverage_before"]
    assert t["interval_80_coverage"] >= t["interval_80_coverage_before_calibration"]   # widening never hurts coverage
    assert joblib.load(tmp / "data/models/model.joblib")["widen"] == pytest.approx(cal["widening_ugm3"], abs=0.01)
    assert "validation months" in (tmp / "reports/MODEL_CARD.md").read_text()


def test_conformal_widening_math():
    # A zero-width range at 5; real values 0..9. Widening by q must bring >= 80% of them inside.
    oof = pd.DataFrame({"target": np.arange(10.0), "p10": 5.0, "p90": 5.0})
    q = model.conformal_widening(oof, coverage=0.8)
    inside = ((oof["target"] >= 5 - q) & (oof["target"] <= 5 + q)).mean()
    assert inside >= 0.8


def test_no_leakage_features_of_day_d_ignore_the_future(trained):
    hourly, weather, fires, cams = make_gold()
    tables = {"hourly": hourly, "weather": weather, "fires": fires, "cams": cams,
              "daily": hourly.assign(day_pkt=(hourly["ts_utc"] + pd.Timedelta(hours=5)).dt.date)
                             .groupby(["city_id", "day_pkt"])["pm25_median"]
                             .agg(pm25_mean="mean", pm25_max_hour="max").reset_index().assign(complete=True)}
    before = model.build_features(tables)

    # Change the future: tomorrow's PM2.5 everywhere and today's hours after 20:00 PKT.
    d = pd.Timestamp("2026-09-15")
    future = tables["hourly"].copy()
    pkt = future["ts_utc"].dt.tz_convert("Asia/Karachi").dt.tz_localize(None)
    late_today = (pkt.dt.normalize() == d) & (pkt.dt.hour >= 20)
    tomorrow = pkt.dt.normalize() == d + pd.Timedelta(days=1)
    future.loc[late_today | tomorrow, "pm25_median"] *= 10
    tables2 = dict(tables, hourly=future)
    tables2["daily"] = tables["daily"].copy()
    tables2["daily"].loc[pd.to_datetime(tables2["daily"]["day_pkt"]) == d + pd.Timedelta(days=1), "pm25_mean"] *= 10
    after = model.build_features(tables2)

    cols = model.feature_columns(before)
    row = lambda f: f[(f["day"] == d)].set_index("city_id")[cols].sort_index()
    pd.testing.assert_frame_equal(row(before), row(after))          # features unchanged
    assert not row(before).empty
    changed = after[(after["day"] == d)]["target"].values
    assert (changed != before[(before["day"] == d)]["target"].values).all()   # but the target did change


def test_training_is_reproducible(trained):
    tmp, first = trained
    assert model.train() == first


def test_live_prediction_for_tomorrow(trained):
    out = model.predict(now=NOW)
    assert "for 2026-10-20" in out
    with warehouse.connect(read_only=True) as con:
        p = con.execute("SELECT * FROM gold.predictions").df()
    assert set(p["city_id"]) == set(CITY_LEVEL) and len(p) == 4
    assert ((p["pm25_p10"] <= p["pm25_pred"]) & (p["pm25_pred"] <= p["pm25_p90"])).all()
    assert p["row_id"].str.startswith("pred|").all() and p["inputs_row_ids"].str.contains("weatherday").all()
    assert set(p["category"]) <= {"unhealthy very likely", "unhealthy likely", "unhealthy possible", "unhealthy unlikely"}


def test_decision_files_and_card(trained):
    tmp, _ = trained
    import json
    d = json.loads((tmp / "reports/decision_metrics.json").read_text())
    rule = d["test"][str(d["default_cost_ratio"])]
    for name in ("our_rule", "warn_if_today_unhealthy", "warn_if_forecast_number_unhealthy"):
        s = rule[name]
        assert s["hits"] + s["misses"] + s["false_alarms"] + s["correct_quiet"] == s["days"]
    assert "issue a smog advisory" in (tmp / "reports/MODEL_CARD.md").read_text()
    val = pd.read_csv(tmp / "reports/validation_predictions.csv")
    assert val["p_unhealthy"].between(0.01, 0.99).all() and val["target_day"].max() < "2026-10-01"


def test_live_prediction_has_probability_advice_and_drivers(trained):
    import json
    model.predict(now=NOW)
    with warehouse.connect(read_only=True) as con:
        p = con.execute("SELECT * FROM gold.predictions").df()
    assert p["p_unhealthy"].between(0.01, 0.99).all()
    assert p["advisory_default"].dtype == bool
    drivers = json.loads(p["drivers_json"].iloc[0])
    assert drivers and {"driver", "effect_ugm3"} <= set(drivers[0])


def test_old_predictions_table_gets_new_columns(trained):
    # A table written by the previous version (no probability columns) must keep its rows.
    with warehouse.connect() as con:
        con.execute("DROP TABLE IF EXISTS gold.predictions")
        con.execute("CREATE TABLE gold.predictions (city_id VARCHAR, row_id VARCHAR, pm25_pred DOUBLE)")
        con.execute("INSERT INTO gold.predictions VALUES ('lahore', 'pred|old', 80.0)")
    model.predict(now=NOW)
    with warehouse.connect(read_only=True) as con:
        p = con.execute("SELECT * FROM gold.predictions").df()
    assert "pred|old" in set(p["row_id"]) and "p_unhealthy" in p.columns and len(p) == 5


def test_publish_export_has_dashboard_files_and_no_secrets(trained, monkeypatch):
    from smog import publish
    tmp, _ = trained
    (tmp / ".env").write_text("FIRMS_MAP_KEY=secret-should-never-be-published\n")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    model.predict(now=NOW)
    summary = publish.run()
    assert "upload SKIPPED" in summary
    out = tmp / "data/publish"
    for name in ("predictions.csv", "city_daily.csv", "freshness.csv", "scorecard.csv", "published.json",
                 "reports/MODEL_CARD.md", "reports/decision_metrics.json", "README.md"):
        assert (out / name).exists(), name
    everything = "".join(p.read_text(errors="ignore") for p in out.rglob("*") if p.is_file())
    assert "secret-should-never-be-published" not in everything
    (tmp / ".env").unlink()
    import os
    os.environ.pop("FIRMS_MAP_KEY", None)


def test_scorecard_uses_the_evening_forecast():
    from smog import publish
    pred = pd.DataFrame({
        "city_id": ["lahore"] * 3, "target_day_pkt": ["2026-10-21"] * 3,
        # 10:05, 20:05 and 22:05 PKT on 20 Oct
        "issued_utc": ["2026-10-20T05:05:00Z", "2026-10-20T15:05:00Z", "2026-10-20T17:05:00Z"],
        "pm25_pred": [100.0, 120.0, 130.0], "pm25_p10": [80.0] * 3, "pm25_p90": [150.0] * 3,
        "p_unhealthy": [0.9] * 3, "advisory_default": [True] * 3, "baseline_persistence": [90.0] * 3,
        "row_id": ["a", "b", "c"]})
    daily = pd.DataFrame({"city_id": ["lahore"], "day_pkt": ["2026-10-21"], "pm25_mean": [110.0],
                          "hours_covered": [24], "complete": [True]})
    s = publish.scorecard(pred, daily)
    assert len(s) == 1 and s["row_id"].iloc[0] == "b"          # the 20:05 issue, not the later one
    assert s["status"].iloc[0] == "final" and s["error"].iloc[0] == 10.0
