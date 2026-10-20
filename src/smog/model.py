"""The forecasting model: tomorrow's mean PM2.5 for each city.

THE QUESTION
  Each evening (20:00 Pakistan time) an environment officer asks: how bad will
  the air be tomorrow, on average, in my city? So for every city and every day D
  we predict the mean PM2.5 of day D+1, using ONLY what is known at 20:00 on D.

WHAT THE MODEL MAY USE (known at 20:00 on day D)
  - PM2.5 of today so far (00:00-19:59) and of the evening (14:00-19:59)
  - full-day PM2.5 of yesterday and earlier days
  - today's weather, and tomorrow's weather forecast
  - fires near the city today and in the last 3 days (VIIRS NOAA-20)
  - the CAMS model's PM2.5 for today and tomorrow
  - the season (day of year) and day of week of tomorrow, and the city

THE BASELINE (declared in DATASET_DECLARATION.md before any results)
  Persistence: tomorrow's mean = today's mean. We report it two ways:
  - "same information": today's mean so far at 20:00 (what the officer knows).
  - "full day": the whole of today, including hours after 20:00. This gives the
    baseline MORE information than our model has, so it is a harder test.

HONEST EVALUATION
  - Test set: every target day from 1 Oct 2026 on, never used for tuning.
  - Model choice: rolling-origin validation on earlier months (train on the
    past, predict the next month, move forward one month, repeat).
  - Same random seed every time, so results can be reproduced.

UNCERTAINTY
  Two extra models predict the 10th and 90th percentile, giving an 80% range.
  We check on the test set how often the real value fell inside that range.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from smog import config, decision, warehouse

ISSUE_HOUR_PKT = 20               # forecasts are issued at 20:00 Pakistan time
EVENING_START_PKT = 14
TEST_START = pd.Timestamp("2026-10-01")
UNHEALTHY = 55.5                  # µg/m³, US EPA "Unhealthy" 24-hour breakpoint (declared)
SEED = 42
CITIES = ["islamabad_rawalpindi", "karachi", "lahore", "peshawar"]
WEATHER_COLS = ["temp_mean", "rh_mean", "wind_mean_kmh", "blh_mean_m", "blh_min_m", "precip_sum_mm"]

# Two candidate settings; rolling-origin validation picks the better one.
CANDIDATES = {
    "small": dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20, l2_regularization=1.0),
    "medium": dict(max_iter=500, learning_rate=0.03, max_leaf_nodes=31, min_samples_leaf=30, l2_regularization=1.0),
}


def models_dir() -> Path:
    return config.DATA_DIR / "models"


def reports_dir() -> Path:
    return config.ROOT / "reports"


# --- Features ---------------------------------------------------------------------------

def _load(con) -> dict[str, pd.DataFrame]:
    q = lambda sql: con.execute(sql).df()
    return {
        "hourly": q("SELECT city_id, ts_utc, pm25_median FROM gold.city_hourly"),
        "daily": q("SELECT city_id, day_pkt, pm25_mean, pm25_max_hour, complete FROM gold.city_daily"),
        "weather": q("SELECT * FROM gold.weather_daily"),
        "fires": q("SELECT city_id, day_pkt, fires_100km, fires_300km, frp_300km FROM gold.fires_daily"),
        "cams": q("SELECT city_id, day_pkt, cams_pm25_mean FROM gold.cams_daily"),
    }


def _so_far(hourly: pd.DataFrame) -> pd.DataFrame:
    """Today's PM2.5 as known at 20:00 PKT: mean of 00:00-19:59 and of the evening."""
    h = hourly.copy()
    pkt = h["ts_utc"].dt.tz_convert("Asia/Karachi")
    h["day"] = pkt.dt.tz_localize(None).dt.normalize()
    h["hour"] = pkt.dt.hour
    h = h[h["hour"] < ISSUE_HOUR_PKT]
    g = h.groupby(["city_id", "day"])
    out = g["pm25_median"].agg(pm_sofar="mean", hours_sofar="count").reset_index()
    evening = h[h["hour"] >= EVENING_START_PKT].groupby(["city_id", "day"])["pm25_median"].mean()
    return out.merge(evening.rename("pm_evening").reset_index(), how="left")


def build_features(tables: dict[str, pd.DataFrame], extra_day: bool = False) -> pd.DataFrame:
    """One row per city and day D, with features known at 20:00 on D and the target D+1.

    extra_day=True adds tomorrow to the calendar, so a row exists for "today" even
    though its target is not known yet (used when making a live forecast).
    """
    daily = tables["daily"].assign(day=lambda d: pd.to_datetime(d["day_pkt"]))
    days = pd.date_range(daily["day"].min(), daily["day"].max() + pd.Timedelta(days=1 if extra_day else 0))
    grid = pd.MultiIndex.from_product([CITIES, days], names=["city_id", "day"]).to_frame(index=False)

    full = daily[["city_id", "day", "pm25_mean", "pm25_max_hour", "complete"]]
    g = grid.merge(full, how="left").merge(_so_far(tables["hourly"]), how="left")
    for name in ("weather", "fires", "cams"):
        t = tables[name].assign(day=lambda d: pd.to_datetime(d["day_pkt"])).drop(columns=["day_pkt", "row_id"], errors="ignore")
        g = g.merge(t, on=["city_id", "day"], how="left")
    g = g.sort_values(["city_id", "day"]).reset_index(drop=True)

    by_city = g.groupby("city_id")
    shift = lambda col, n: by_city[col].shift(n)
    f = pd.DataFrame({"city_id": g["city_id"], "day": g["day"]})
    f["target_day"] = f["day"] + pd.Timedelta(days=1)

    # PM2.5 history (all from days that are finished, or today's hours before 20:00)
    f["pm_sofar_today"] = g["pm_sofar"]
    f["pm_evening_today"] = g["pm_evening"]
    f["hours_sofar_today"] = g["hours_sofar"]
    f["pm_yesterday"] = shift("pm25_mean", 1)
    f["pm_2days_ago"] = shift("pm25_mean", 2)
    f["pm_max_hour_yesterday"] = shift("pm25_max_hour", 1)
    f["pm_mean_last7"] = by_city["pm25_mean"].transform(lambda s: s.shift(1).rolling(7, min_periods=3).mean())

    # Weather today and tomorrow (tomorrow = forecast when used live)
    for col in WEATHER_COLS:
        f[f"{col}_today"] = g[col]
        f[f"{col}_tomorrow"] = shift(col, -1)
    rad = np.radians(g["wind_dir_mean"])
    f["wind_dir_sin_tomorrow"] = np.sin(rad).groupby(g["city_id"]).shift(-1)
    f["wind_dir_cos_tomorrow"] = np.cos(rad).groupby(g["city_id"]).shift(-1)

    # Fires (counts are 0 on days with no detections, not missing)
    for col in ("fires_100km", "fires_300km", "frp_300km"):
        g[col] = g[col].fillna(0)
    f["fires_300km_today"] = g["fires_300km"]
    f["fires_100km_today"] = g["fires_100km"]
    f["frp_300km_today"] = g["frp_300km"]
    f["fires_300km_last3"] = by_city["fires_300km"].transform(lambda s: s.fillna(0).rolling(3, min_periods=1).sum())

    # CAMS model (a feature only, never the target)
    f["cams_pm25_today"] = g["cams_pm25_mean"]
    f["cams_pm25_tomorrow"] = shift("cams_pm25_mean", -1)

    # Calendar and city
    doy = f["target_day"].dt.dayofyear
    f["season_sin"] = np.sin(2 * np.pi * doy / 365.25)
    f["season_cos"] = np.cos(2 * np.pi * doy / 365.25)
    f["weekday_tomorrow"] = f["target_day"].dt.weekday
    for city in CITIES:
        f[f"city_{city}"] = (f["city_id"] == city).astype(int)

    # Target and baselines
    f["target"] = shift("pm25_mean", -1).where(shift("complete", -1).fillna(False).astype(bool))
    f["baseline_same_info"] = f["pm_sofar_today"].fillna(f["pm_yesterday"])
    f["baseline_full_day"] = g["pm25_mean"]
    f["target_row_id"] = "cityday|" + f["city_id"] + "|" + f["target_day"].dt.strftime("%Y%m%d")
    return f


FEATURE_COLUMNS: list[str] = []   # filled on first use, from build_features' output


def feature_columns(f: pd.DataFrame) -> list[str]:
    skip = {"city_id", "day", "target_day", "target", "baseline_same_info", "baseline_full_day", "target_row_id"}
    return [c for c in f.columns if c not in skip]


# --- Training -----------------------------------------------------------------------------

def _model(params: dict, loss: str = "absolute_error", quantile: float | None = None):
    kw = dict(params, random_state=SEED, loss=loss)
    if quantile is not None:
        kw["quantile"] = quantile
    return HistGradientBoostingRegressor(**kw)


def rolling_origin(train: pd.DataFrame, cols: list[str], params: dict, with_ranges: bool = False) -> pd.DataFrame:
    """Train on everything before a month, predict that month; repeat month by month.

    with_ranges=True also predicts the 10% / 90% values, used to calibrate the ranges.
    """
    months = sorted(train["target_day"].dt.to_period("M").unique())
    out = []
    for month in months[4:]:                     # need ~4 months of history before the first fold
        start = month.to_timestamp()
        past = train[train["target_day"] < start]
        this = train[train["target_day"].dt.to_period("M") == month]
        if len(past) < 100 or this.empty:
            continue
        m = _model(params).fit(past[cols], past["target"])
        piece = this.assign(prediction=m.predict(this[cols]), fold=str(month))
        if with_ranges:
            lo = _model(params, "quantile", 0.10).fit(past[cols], past["target"]).predict(this[cols])
            hi = _model(params, "quantile", 0.90).fit(past[cols], past["target"]).predict(this[cols])
            b = np.sort(np.column_stack([lo, piece["prediction"], hi]), axis=1)
            piece = piece.assign(p10=b[:, 0], p90=b[:, 2])
        out.append(piece)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def conformal_widening(oof: pd.DataFrame, coverage: float = 0.80) -> float:
    """How much to widen the 10%-90% range so it holds ~80% of real values.

    Conformalized quantile regression: for each validation day, measure how far the
    real value fell OUTSIDE the range (negative if it was inside). The amount we add
    to both ends is the 80th percentile of those distances (with the usual small-sample
    correction). Only validation months are used, never the test set.
    """
    outside_by = np.maximum(oof["p10"] - oof["target"], oof["target"] - oof["p90"]).to_numpy()
    n = len(outside_by)
    level = min(1.0, np.ceil((n + 1) * coverage) / n)
    return float(np.quantile(outside_by, level))


def _scores(y, pred) -> dict:
    err = pred - y
    return {"mae": round(float(np.mean(np.abs(err))), 2), "rmse": round(float(np.sqrt(np.mean(err ** 2))), 2),
            "bias": round(float(np.mean(err)), 2), "n": int(len(y))}


def _exceedance(y, pred) -> dict:
    actual, said = y >= UNHEALTHY, pred >= UNHEALTHY
    tp = int((actual & said).sum())
    return {"accuracy": round(float((actual == said).mean()), 3),
            "recall_unhealthy_days": round(tp / max(int(actual.sum()), 1), 3),
            "precision_unhealthy_days": round(tp / max(int(said.sum()), 1), 3),
            "unhealthy_days": int(actual.sum())}


def _season(day: pd.Series) -> pd.Series:
    return np.where(day.dt.month.isin([10, 11, 12, 1, 2]), "smog season (Oct-Feb)", "rest of year (Mar-Sep)")


def train() -> str:
    with warehouse.connect(read_only=True) as con:
        tables = _load(con)
    f = build_features(tables)
    cols = feature_columns(f)
    labelled = f[f["target"].notna() & f["baseline_same_info"].notna()]
    train_set = labelled[labelled["target_day"] < TEST_START]
    test_set = labelled[labelled["target_day"] >= TEST_START]
    if len(train_set) < 200 or test_set.empty:
        raise RuntimeError(f"not enough data: {len(train_set)} training rows, {len(test_set)} test rows")

    # 1. Choose settings by rolling-origin validation (test set untouched).
    cv = {}
    for name, params in CANDIDATES.items():
        oof = rolling_origin(train_set, cols, params)
        cv[name] = {"mae": _scores(oof["target"], oof["prediction"])["mae"], "oof": oof}
    best = min(cv, key=lambda k: cv[k]["mae"])
    oof = cv[best]["oof"]

    # 2. Fit the chosen model (median) and the 10% / 90% models on all training data.
    params = CANDIDATES[best]
    point = _model(params).fit(train_set[cols], train_set["target"])
    low = _model(params, "quantile", 0.10).fit(train_set[cols], train_set["target"])
    high = _model(params, "quantile", 0.90).fit(train_set[cols], train_set["target"])

    # 3. Calibrate the 80% range on the validation months (never on the test set).
    oof_ranges = rolling_origin(train_set, cols, params, with_ranges=True)
    widen = conformal_widening(oof_ranges)
    val_raw = float(((oof_ranges["target"] >= oof_ranges["p10"]) & (oof_ranges["target"] <= oof_ranges["p90"])).mean())
    val_cal = float(((oof_ranges["target"] >= oof_ranges["p10"] - widen)
                     & (oof_ranges["target"] <= oof_ranges["p90"] + widen)).mean())

    # 4. Score once on the untouched test set.
    t = test_set.copy()
    t["prediction"] = point.predict(t[cols])
    bounds = np.sort(np.column_stack([low.predict(t[cols]), t["prediction"], high.predict(t[cols])]), axis=1)
    raw_p10, raw_p90 = bounds[:, 0], bounds[:, 2]
    raw_inside = (t["target"] >= raw_p10) & (t["target"] <= raw_p90)
    t["p10"] = np.minimum(raw_p10 - widen, t["prediction"]).clip(lower=0)   # a range never excludes the forecast
    t["p90"] = np.maximum(raw_p90 + widen, t["prediction"])
    inside = (t["target"] >= t["p10"]) & (t["target"] <= t["p90"])

    # Stricter check: the same model WITHOUT any "tomorrow" inputs (weather and CAMS
    # forecasts). In training those come from archives that are closer to the truth
    # than a real forecast, so this shows the most pessimistic honest result.
    today_only = [c for c in cols if not c.endswith("_tomorrow") or c == "weekday_tomorrow"]
    strict = _model(params).fit(train_set[today_only], train_set["target"])
    strict_s = _scores(t["target"], strict.predict(t[today_only]))

    model_s = _scores(t["target"], t["prediction"])
    base_s = _scores(t["target"], t["baseline_same_info"])
    base_full_s = _scores(t["target"], t["baseline_full_day"].fillna(t["baseline_same_info"]))
    improvement = lambda a, b: round(100 * (b["mae"] - a["mae"]) / b["mae"], 1)

    by_city = []
    for city, part in t.groupby("city_id"):
        m, b = _scores(part["target"], part["prediction"]), _scores(part["target"], part["baseline_same_info"])
        by_city.append({"city": city, "days": m["n"], "model_mae": m["mae"], "baseline_mae": b["mae"],
                        "improvement_pct": improvement(m, b), "model_bias": m["bias"],
                        "interval_coverage": round(float(inside[part.index].mean()), 2)})
    by_city = pd.DataFrame(by_city)

    oof["season"] = _season(oof["target_day"])
    oof_groups = []
    for (city, season), part in oof.groupby(["city_id", "season"]):
        m, b = _scores(part["target"], part["prediction"]), _scores(part["target"], part["baseline_same_info"])
        oof_groups.append({"city": city, "season": season, "days": m["n"], "model_mae": m["mae"],
                           "baseline_mae": b["mae"], "improvement_pct": improvement(m, b)})
    oof_groups = pd.DataFrame(oof_groups)

    # Decision rule ("issue an advisory?"), replayed on validation months and on the test month.
    val = oof_ranges.copy()
    val["p10"] = np.minimum(val["p10"] - widen, val["prediction"]).clip(lower=0)
    val["p90"] = np.maximum(val["p90"] + widen, val["prediction"])
    decisions = decision.report(val, t)
    for frame in (val, t):
        frame["p_unhealthy"] = decision.prob_exceed(frame["prediction"], frame["p10"], frame["p90"]).round(3)

    lahore = by_city.set_index("city").loc["lahore"] if "lahore" in set(by_city["city"]) else None
    metrics = {
        "trained_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chosen_settings": best, "settings": params,
        "rolling_origin_mae": {k: v["mae"] for k, v in cv.items()},
        "train_rows": len(train_set), "test_rows": len(t),
        "train_target_days": [str(train_set["target_day"].min().date()), str(train_set["target_day"].max().date())],
        "test_target_days": [str(t["target_day"].min().date()), str(t["target_day"].max().date())],
        "test": {"model": model_s, "baseline_same_info": base_s, "baseline_full_day": base_full_s,
                 "model_without_tomorrow_inputs": strict_s,
                 "improvement_without_tomorrow_inputs_pct": improvement(strict_s, base_s),
                 "improvement_vs_baseline_pct": improvement(model_s, base_s),
                 "improvement_vs_full_day_baseline_pct": improvement(model_s, base_full_s),
                 "unhealthy_model": _exceedance(t["target"], t["prediction"]),
                 "unhealthy_baseline": _exceedance(t["target"], t["baseline_same_info"]),
                 "interval_80_coverage": round(float(inside.mean()), 3),
                 "interval_80_mean_width": round(float((t["p90"] - t["p10"]).mean()), 1),
                 "interval_80_coverage_before_calibration": round(float(raw_inside.mean()), 3),
                 "interval_80_mean_width_before_calibration": round(float((raw_p90 - raw_p10).mean()), 1)},
        "interval_calibration": {
            "method": "conformalized quantile regression on rolling-origin validation months",
            "widening_ugm3": round(widen, 2),
            "validation_coverage_before": round(val_raw, 3),
            "validation_coverage_after": round(val_cal, 3),
            "validation_days": int(len(oof_ranges))},
        "declared_success": {"rule": "at least 10% lower MAE than persistence, overall and for Lahore",
                             "overall_met": improvement(model_s, base_s) >= 10,
                             "lahore_met": bool(lahore is not None and lahore["improvement_pct"] >= 10)},
        "features": cols,
    }

    models_dir().mkdir(parents=True, exist_ok=True)
    # A "typical day" per city (medians of the training features), used to explain each live forecast.
    typical = train_set.groupby("city_id")[cols].median()
    joblib.dump({"point": point, "p10": low, "p90": high, "widen": widen, "features": cols, "metrics": metrics,
                 "typical": typical}, models_dir() / "model.joblib")
    reports_dir().mkdir(parents=True, exist_ok=True)
    (reports_dir() / "model_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    by_city.to_csv(reports_dir() / "test_errors_by_city.csv", index=False)
    oof_groups.to_csv(reports_dir() / "validation_errors_by_city_and_season.csv", index=False)
    keep = ["city_id", "target_day", "target", "prediction", "p10", "p90", "p_unhealthy",
            "baseline_same_info", "target_row_id"]
    t[keep].to_csv(reports_dir() / "test_predictions.csv", index=False)
    # Out-of-sample predictions for every validation month (each made by a model trained only on
    # earlier months). Used for the decision replay and the "replay a smog episode" view.
    val[keep].round({c: 2 for c in ("target", "prediction", "p10", "p90", "baseline_same_info")}).to_csv(reports_dir() / "validation_predictions.csv", index=False)
    (reports_dir() / "decision_metrics.json").write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    write_model_card(metrics, by_city, oof_groups, decisions)

    return (f"model MAE {model_s['mae']} vs baseline {base_s['mae']} µg/m³ on {model_s['n']} test city-days "
            f"({metrics['test']['improvement_vs_baseline_pct']}% better); 80% range held the true value "
            f"{metrics['test']['interval_80_coverage']:.0%} of the time "
            f"(before calibration {metrics['test']['interval_80_coverage_before_calibration']:.0%}; "
            f"widened by {widen:.1f} µg/m³ using validation months only); settings '{best}'")


# --- Model card (every number comes from the metrics above, never typed by hand) ------------

def _table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def _decision_section(dec: dict) -> str:
    r = str(dec["default_cost_ratio"])
    rows = []
    for period in ("validation", "test"):
        e = dec[period][r]
        for rule, label in (("our_rule", f"Our rule: warn if P(unhealthy) ≥ {e['threshold']:.0%}"),
                            ("warn_if_today_unhealthy", "Warn if today is unhealthy (persistence)"),
                            ("warn_if_forecast_number_unhealthy", "Warn if the forecast number ≥ 55.5")):
            s = e[rule]
            rows.append({"period": period, "rule": label, "days": s["days"], "unhealthy days": s["unhealthy_days"],
                         "hits": s["hits"], "misses": s["misses"], "false alarms": s["false_alarms"],
                         "cost per 100 days": s["cost_per_100_days"]})
    v = dec["validation"][r]
    return (
        '## From forecast to decision: "issue a smog advisory?"\n'
        f"We turn the calibrated range into a **chance that tomorrow is unhealthy** (mean ≥ {dec['unhealthy_level_ugm3']} µg/m³) and\n"
        "issue an advisory when that chance is at least 1/(1+R), where **R = how many times worse a missed unhealthy day\n"
        "is than an unnecessary advisory**. R is a policy choice made by the user, not fitted to data; our default is\n"
        f"R = {dec['default_cost_ratio']:g} (threshold {v['threshold']:.0%}). Cost is counted as 1 per false alarm and R per miss.\n\n"
        f"{_table(pd.DataFrame(rows))}\n\n"
        f"Probability quality on the validation months: Brier score {v['probability_brier']} (skill vs always saying\n"
        f"the base rate: {v['probability_brier_skill_vs_base_rate']}; above 0 means the probabilities carry real information).\n"
    )


def write_model_card(m: dict, by_city: pd.DataFrame, by_group: pd.DataFrame, dec: dict | None = None) -> None:
    t = m["test"]
    decision_text = _decision_section(dec) if dec else ""
    worst = by_city.sort_values("model_mae", ascending=False).iloc[0]
    text = f"""# Model card: next-day PM2.5 forecast

*Generated automatically by `python -m smog train` on {m['trained_at_utc']}. Do not edit numbers by hand.*

## What it does
Predicts tomorrow's mean PM2.5 (µg/m³, Pakistan calendar day) for Lahore, Islamabad/Rawalpindi,
Karachi and Peshawar, issued at {ISSUE_HOUR_PKT}:00 PKT. Users: district environment officers deciding on
smog advisories. **Information, not professional or medical advice.**

## Data
Gold tables built from OpenAQ sensors (target), Open-Meteo weather, NASA FIRMS fires (VIIRS NOAA-20)
and CAMS model values (feature only). See DATASET_DECLARATION.md and the quality log.

## Method
Gradient-boosted trees (scikit-learn `HistGradientBoostingRegressor`, absolute-error loss), plus two
quantile models (10% and 90%) for an 80% range. Settings chosen by rolling-origin validation
(month by month, training only on the past): {m['rolling_origin_mae']} MAE; chosen: **{m['chosen_settings']}**.
Random seed {SEED}.

Training target days: {m['train_target_days'][0]} to {m['train_target_days'][1]} ({m['train_rows']} city-days).
**Test target days: {m['test_target_days'][0]} to {m['test_target_days'][1]} ({m['test_rows']} city-days), never used for tuning.**

## Results on the test set
| | MAE (µg/m³) | RMSE | bias |
|---|---|---|---|
| Our model | {t['model']['mae']} | {t['model']['rmse']} | {t['model']['bias']} |
| Our model without any "tomorrow" inputs (strict check) | {t['model_without_tomorrow_inputs']['mae']} | {t['model_without_tomorrow_inputs']['rmse']} | {t['model_without_tomorrow_inputs']['bias']} |
| Declared baseline: persistence (today so far at 20:00) | {t['baseline_same_info']['mae']} | {t['baseline_same_info']['rmse']} | {t['baseline_same_info']['bias']} |
| Harder baseline: persistence using all of today | {t['baseline_full_day']['mae']} | {t['baseline_full_day']['rmse']} | {t['baseline_full_day']['bias']} |

Improvement over the declared baseline: **{t['improvement_vs_baseline_pct']}%** (vs the harder baseline: {t['improvement_vs_full_day_baseline_pct']}%;
strict model without "tomorrow" inputs: {t['improvement_without_tomorrow_inputs_pct']}%).
Declared success rule ({m['declared_success']['rule']}): overall **{'met' if m['declared_success']['overall_met'] else 'NOT met'}**, Lahore **{'met' if m['declared_success']['lahore_met'] else 'NOT met'}**.

Unhealthy days (≥ {UNHEALTHY} µg/m³): model recall {t['unhealthy_model']['recall_unhealthy_days']},
precision {t['unhealthy_model']['precision_unhealthy_days']}; baseline recall {t['unhealthy_baseline']['recall_unhealthy_days']},
precision {t['unhealthy_baseline']['precision_unhealthy_days']} ({t['unhealthy_model']['unhealthy_days']} unhealthy test days).

**Uncertainty:** the 80% range contained the real value {t['interval_80_coverage']:.0%} of the time
(ideal: about 80%); average width {t['interval_80_mean_width']} µg/m³. For the officer this means: plan for the
upper end of the range when deciding on an advisory.

How the range was calibrated: the raw 10%/90% models were too confident (on the test set they held only
{t['interval_80_coverage_before_calibration']:.0%} of real values, width {t['interval_80_mean_width_before_calibration']} µg/m³). We widened both ends by
{m['interval_calibration']['widening_ugm3']} µg/m³, an amount chosen **only from the validation months**
({m['interval_calibration']['validation_days']} city-days: coverage {m['interval_calibration']['validation_coverage_before']:.0%} → {m['interval_calibration']['validation_coverage_after']:.0%}),
using conformalized quantile regression. The test set was not used to choose it.

{decision_text}
## Errors by city (test set)
{_table(by_city)}

Weakest city on the test set: **{worst['city']}** (MAE {worst['model_mae']}). Users there should treat the
forecast as a rough guide and also check the official Punjab EPA / provincial readings.

## Errors by city and season (rolling-origin validation, before the test period)
{_table(by_group)}

## Limitations (what this model cannot tell you)
- Trained on low-cost sensors (no humidity correction applied); values may read high in very humid air.
- Training uses archived weather and CAMS values for "tomorrow", which are closer to the truth than the
  forecasts available at 20:00 the evening before. Live errors may therefore be larger than the test
  errors above; the "strict check" row shows the result with no "tomorrow" inputs at all.
- The test period is short and covers only the start of the smog season.
- One value per city: neighbourhoods can be much worse than the city average.
- Health decisions: follow official advisories (Punjab EPA, PMD, NDMA) and seek medical help in an emergency.
"""
    (reports_dir() / "MODEL_CARD.md").write_text(text, encoding="utf-8")


# --- Live forecast for tomorrow ----------------------------------------------------------------

def _forecast_daily(con, now: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Weather and CAMS per city and PKT day from the latest forecast snapshot issued before now."""
    out = {}
    for name, table, cols in (
        ("weather", "cleaned.weather_forecast",
         "avg(temperature_2m) AS temp_mean, avg(relative_humidity_2m) AS rh_mean, avg(wind_speed_10m) AS wind_mean_kmh, "
         "(degrees(atan2(avg(sin(radians(wind_direction_10m))), avg(cos(radians(wind_direction_10m))))) + 360) % 360 AS wind_dir_mean, "
         "sum(precipitation) AS precip_sum_mm, avg(boundary_layer_height) AS blh_mean_m, min(boundary_layer_height) AS blh_min_m"),
        ("cams", "cleaned.cams_forecast", "avg(pm2_5) AS cams_pm25_mean"),
    ):
        out[name] = con.execute(f"""
            WITH latest AS (SELECT city_id, max(issued_utc) AS issued FROM {table}
                            WHERE issued_utc <= ? GROUP BY city_id)
            SELECT f.city_id, CAST(f.ts_utc + INTERVAL 5 HOUR AS DATE) AS day_pkt, {cols}
            FROM {table} f JOIN latest l ON f.city_id = l.city_id AND f.issued_utc = l.issued
            GROUP BY 1, 2""", [now.to_pydatetime()]).df()
    return out


# Feature groups in plain words, for "why is tomorrow's forecast high or low?"
DRIVER_GROUPS = {
    "smog already in the air (today and recent days)": lambda c: c.startswith("pm_"),
    "tomorrow's weather (wind, rain, mixing height)": lambda c: c.endswith("_tomorrow") and not c.startswith(("cams_", "weekday")),
    "today's weather": lambda c: c.endswith("_today") and not c.startswith(("pm_", "fires_", "frp_", "cams_")),
    "fires nearby (crop burning)": lambda c: c.startswith(("fires_", "frp_")),
    "CAMS air-quality model": lambda c: c.startswith("cams_"),
    "season": lambda c: c.startswith("season_"),
}


def explain(point, rows: pd.DataFrame, cols: list[str], typical: pd.DataFrame) -> list[list[dict]]:
    """For each row: how much each group of inputs moves the forecast away from a typical day.

    Method ("what if this group were typical?"): set one group of inputs to that city's typical
    (median training) values, predict again, and report the difference. Positive = pushes the
    forecast up. Simple, model-agnostic, and honest about being an approximation (effects can overlap).
    """
    full = point.predict(rows[cols])
    out = []
    for i, (_, row) in enumerate(rows.iterrows()):
        base = typical.loc[row["city_id"]] if row["city_id"] in typical.index else typical.median()
        items = []
        for label, match in DRIVER_GROUPS.items():
            group = [c for c in cols if match(c)]
            if not group:
                continue
            what_if = rows.iloc[[i]][cols].copy()
            what_if.loc[:, group] = base[group].to_numpy()
            items.append({"driver": label, "effect_ugm3": round(float(full[i] - point.predict(what_if)[0]), 1)})
        out.append(sorted(items, key=lambda d: -abs(d["effect_ugm3"])))
    return out


def _append(con, table: str, df: pd.DataFrame) -> None:
    """Append rows; add any new columns first, so older history rows are kept (new columns are empty there)."""
    con.register("new_rows", df)
    con.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM new_rows WHERE false")
    have = set(con.execute(f"SELECT * FROM {table} LIMIT 0").df().columns)
    types = {r[0]: r[1] for r in con.execute("DESCRIBE new_rows").fetchall()}
    for col in df.columns:
        if col not in have:
            con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {types[col]}')
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM new_rows")
    con.unregister("new_rows")


def predict(now: datetime | None = None) -> str:
    """Forecast tomorrow for each city and append it to gold.predictions (history is kept)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    path = models_dir() / "model.joblib"
    if not path.exists():
        return "SKIPPED: no trained model on this computer yet (run `python -m smog train` once)"
    bundle = joblib.load(path)
    with warehouse.connect() as con:
        tables = _load(con)
        fc = _forecast_daily(con, now)
        # Today/tomorrow weather and CAMS come from the forecast where observations do not exist yet.
        for name in ("weather", "cams"):
            known = tables[name].assign(day_pkt=lambda d: pd.to_datetime(d["day_pkt"]))
            extra = fc[name].assign(day_pkt=lambda d: pd.to_datetime(d["day_pkt"]))
            merged = pd.concat([known, extra[~extra.set_index(["city_id", "day_pkt"]).index.isin(
                known.set_index(["city_id", "day_pkt"]).index)]], ignore_index=True)
            tables[name] = merged
        f = build_features(tables, extra_day=True)

        today = now.tz_convert("Asia/Karachi").tz_localize(None).normalize()
        rows = f[f["day"] == today].copy()
        if rows.empty:
            raise RuntimeError(f"no feature rows for today ({today.date()})")
        cols = bundle["features"]
        rows["prediction"] = bundle["point"].predict(rows[cols])
        b = np.sort(np.column_stack([bundle["p10"].predict(rows[cols]), rows["prediction"],
                                     bundle["p90"].predict(rows[cols])]), axis=1)
        widen = bundle.get("widen", 0.0)              # calibrated on validation months in train()
        b[:, 0] = np.clip(np.minimum(b[:, 0] - widen, b[:, 1]), 0, None)
        b[:, 2] = np.maximum(b[:, 2] + widen, b[:, 1])
        out = pd.DataFrame({
            "city_id": rows["city_id"], "issued_utc": now, "target_day_pkt": rows["target_day"].dt.date,
            "pm25_pred": b[:, 1].round(1), "pm25_p10": b[:, 0].round(1), "pm25_p90": b[:, 2].round(1),
            "baseline_persistence": rows["baseline_same_info"].round(1),
            "pm_sofar_today": rows["pm_sofar_today"].round(1),
            "hours_sofar_today": rows["hours_sofar_today"],
            "model_trained_at_utc": bundle["metrics"]["trained_at_utc"],
            "inputs_row_ids": [
                f"cityday|{c}|{(d - pd.Timedelta(days=1)):%Y%m%d}; weatherday|{c}|{d:%Y%m%d}; "
                f"weatherday|{c}|{(d + pd.Timedelta(days=1)):%Y%m%d}; fireday|{c}|{d:%Y%m%d}; camsday|{c}|{d:%Y%m%d}"
                for c, d in zip(rows["city_id"], rows["day"])],
        })
        out["category"] = np.select(
            [out["pm25_p10"] >= UNHEALTHY, out["pm25_pred"] >= UNHEALTHY, out["pm25_p90"] >= UNHEALTHY],
            ["unhealthy very likely", "unhealthy likely", "unhealthy possible"], "unhealthy unlikely")
        out["row_id"] = ("pred|" + out["city_id"] + "|" + pd.to_datetime(out["target_day_pkt"]).dt.strftime("%Y%m%d")
                         + "|" + now.strftime("%Y%m%dT%H%M"))
        # Decision layer: chance of an unhealthy day and the default advisory (see decision.py).
        out["p_unhealthy"] = decision.prob_exceed(out["pm25_pred"], out["pm25_p10"], out["pm25_p90"]).round(3)
        out["advisory_default"] = decision.advise(out["p_unhealthy"])
        if "typical" in bundle:
            out["drivers_json"] = [json.dumps(d) for d in explain(bundle["point"], rows, cols, bundle["typical"])]
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")
        _append(con, "gold.predictions", out.reset_index(drop=True))
    return "; ".join(f"{r.city_id}: {r.pm25_pred} ({r.pm25_p10}-{r.pm25_p90}) {r.category}, "
                     f"P(unhealthy) {r.p_unhealthy:.0%}{', ADVISORY' if r.advisory_default else ''}"
                     for r in out.itertuples()) + f" for {out['target_day_pkt'].iloc[0]}"
