"""Drift monitor: is the world still like the data the model learned from, and is the model still as good?

Run inside every hourly publish; the result goes to the dashboard (drift.json) and one line per run is
added to data/logs/drift_log.csv, so the history of checks is kept.

1. Input drift. For each input we compare the last 14 days with the SAME weeks one year earlier (the model
   trained on those weeks, and smog is seasonal, so "last month" would always look different in October).
   Measure: Population Stability Index (PSI) on 5 bins cut at the reference quantiles.
   PSI < 0.10 stable, 0.10-0.25 watch, > 0.25 drift (the usual credit-scoring rule of thumb). With only ~56
   recent values PSI is noisy even when nothing changed (its expected value is about (bins-1) x (1/n_ref + 1/n_recent),
   ~0.1 here), so we subtract that noise floor before applying the rule and show both numbers.
2. Performance drift. The live error of our evening forecasts (days that are complete) against the error on
   the locked test month. Ratio <= 1.25 ok, <= 1.5 watch, above that drift. We also show the live error of
   "tomorrow equals today", so a hard week for everyone is not mistaken for a broken model.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from smog import config

RECENT_DAYS = 14
REFERENCE_PADDING = 7              # the recent 14 days moved back one year, plus 7 days on each side (28 days)
MIN_VALUES = 20
FEATURES = [  # (table, column, label)
    ("city_daily", "pm25_mean", "PM2.5 daily mean"),
    ("weather_daily", "wind_mean_kmh", "wind speed"),
    ("weather_daily", "blh_mean_m", "mixing height"),
    ("weather_daily", "rh_mean", "humidity"),
    ("fires_daily", "fires_300km", "fires within 300 km"),
]
RANK = {"ok": 0, "watch": 1, "drift": 2, "not enough data": -1}


def psi(reference, current, bins: int = 5) -> float:
    ref = np.asarray(pd.Series(reference).dropna(), dtype=float)
    cur = np.asarray(pd.Series(current).dropna(), dtype=float)
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:                                     # reference is (almost) constant
        edges = np.array([ref.min() - 1e-9, ref.min() + 1e-9, max(ref.max(), cur.max()) + 1])
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


def noise_floor(n_ref: int, n_cur: int, bins: int = 5) -> float:
    """Expected PSI between two samples of the SAME distribution (chi-square approximation)."""
    return (bins - 1) * (1 / n_ref + 1 / n_cur)


def psi_status(value: float) -> str:
    return "ok" if value < 0.10 else "watch" if value <= 0.25 else "drift"


def windows(today: date) -> dict:
    recent = (today - timedelta(days=RECENT_DAYS), today - timedelta(days=1))
    # Exactly the same calendar weeks one year earlier (smog builds up fast in Oct-Nov, so the windows must line up),
    # padded by a week each side so there are enough values to compare.
    reference = (recent[0] - timedelta(days=365 + REFERENCE_PADDING), recent[1] - timedelta(days=365 - REFERENCE_PADDING))
    return {"recent": recent, "reference": reference}


def input_drift(tables: dict[str, pd.DataFrame], today: date) -> list[dict]:
    w = windows(today)
    out = []
    for table, column, label in FEATURES:
        df = tables.get(table)
        if df is None or column not in df:
            continue
        d = df.copy()
        d["day_pkt"] = pd.to_datetime(d["day_pkt"]).dt.date
        if "complete" in d:
            d = d[d["complete"].astype(str).str.lower().isin(["true", "1"])]
        ref = d[(d["day_pkt"] >= w["reference"][0]) & (d["day_pkt"] <= w["reference"][1])][column].dropna()
        cur = d[(d["day_pkt"] >= w["recent"][0]) & (d["day_pkt"] <= w["recent"][1])][column].dropna()
        row = {"input": label, "reference_values": len(ref), "recent_values": len(cur),
               "reference_mean": round(float(ref.mean()), 1) if len(ref) else None,
               "recent_mean": round(float(cur.mean()), 1) if len(cur) else None}
        if len(ref) < MIN_VALUES or len(cur) < 5:
            row.update(psi=None, status="not enough data")
        else:
            value = psi(ref, cur)
            noise = noise_floor(len(ref), len(cur))
            row.update(psi=round(value, 3), noise_floor=round(noise, 3), status=psi_status(max(0.0, value - noise)))
        out.append(row)
    return out


def performance_drift(scorecard: pd.DataFrame, metrics: dict | None) -> dict:
    test_mae = ((metrics or {}).get("test") or {}).get("model", {}).get("mae")
    sc = scorecard if scorecard is not None else pd.DataFrame()
    if not sc.empty and "status" in sc:
        sc = sc[(sc["status"] == "final") & sc["error"].notna()]
    if sc.empty or test_mae is None:
        return {"status": "not enough data", "live_days": int(len(sc)), "test_mae": test_mae}
    live = float(sc["error"].abs().mean())
    base = float(sc["persistence_error"].abs().mean()) if "persistence_error" in sc else None
    ratio = live / float(test_mae)
    status = "ok" if ratio <= 1.25 else "watch" if ratio <= 1.5 else "drift"
    if len(sc) < 8 and status != "ok":
        status = "watch"                      # too few days to call it drift
    return {"status": status, "live_days": int(len(sc)), "live_mae": round(live, 1),
            "live_persistence_mae": round(base, 1) if base is not None else None,
            "test_mae": round(float(test_mae), 1), "ratio_to_test": round(ratio, 2)}


def report(tables: dict[str, pd.DataFrame], scorecard: pd.DataFrame, metrics: dict | None,
           now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = (now + timedelta(hours=5)).date()          # Pakistan calendar day
    w = windows(today)
    inputs = input_drift(tables, today)
    perf = performance_drift(scorecard, metrics)
    statuses = [r["status"] for r in inputs] + [perf["status"]]
    known = [s for s in statuses if RANK[s] >= 0]
    overall = max(known, key=RANK.get) if known else "not enough data"
    return {"checked_utc": now.isoformat(timespec="seconds"), "overall": overall,
            "recent_window": [str(w["recent"][0]), str(w["recent"][1])],
            "reference_window": [str(w["reference"][0]), str(w["reference"][1])],
            "inputs": inputs, "performance": perf,
            "rule": "PSI minus its noise floor: < 0.10 ok, 0.10-0.25 watch, > 0.25 drift; live error / test error <= 1.25 ok, <= 1.5 watch"}


def log(result: dict) -> None:
    path = config.ROOT / "data/logs/drift_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["checked_utc", "overall", "performance", "live_mae", "test_mae"] +
                       [f"psi_{column}" for _, column, _ in FEATURES])
        psis = {r["input"]: r["psi"] for r in result["inputs"]}
        w.writerow([result["checked_utc"], result["overall"], result["performance"]["status"],
                    result["performance"].get("live_mae"), result["performance"].get("test_mae")] +
                   [psis.get(label) for _, _, label in FEATURES])
