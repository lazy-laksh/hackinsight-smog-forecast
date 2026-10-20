"""Turn a forecast into a decision: "issue a smog advisory for tomorrow, or not?"

Plain-words version
-------------------
1. Chance of an unhealthy day.
   The model gives three numbers for tomorrow: a low value (p10), a middle value
   and a high value (p90). After calibration, about 80% of real days fall between
   low and high, 10% below and 10% above. From those three points we draw a simple
   "how likely is each value" curve and read off the chance that the day's mean
   PM2.5 is at or above 55.5 µg/m³ (the US EPA "unhealthy" line we declared).

2. When to warn: the cost-loss rule.
   A false alarm has a cost (schools or traffic disrupted for nothing). A missed
   unhealthy day has a bigger cost (people exposed with no warning). If a miss is
   R times worse than a false alarm, warning is worth it whenever
        chance of unhealthy  >=  1 / (1 + R).
   R is a policy choice, not something we fit to the data. Our default is R = 3
   ("a missed smog day is 3x worse than an unnecessary advisory"), giving a
   threshold of 25%. The dashboard lets the officer move R and see the effect.

3. Is the rule any good?
   We replay it on days the model never trained on (the validation months and
   the October test month) and count hits, misses and false alarms, next to
   two simple rules: "warn if today is unhealthy" (persistence) and "warn if the
   forecast number itself is >= 55.5".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UNHEALTHY = 55.5
DEFAULT_COST_RATIO = 3.0            # a missed unhealthy day counts as 3 false alarms (policy assumption)
COST_RATIOS = (1.0, 2.0, 3.0, 5.0, 10.0)


def threshold_for(cost_ratio: float) -> float:
    """Warn when P(unhealthy) >= this. Classic cost-loss result: C / L = 1 / (1 + R)."""
    return 1.0 / (1.0 + cost_ratio)


def prob_exceed(pred, p10, p90, level: float = UNHEALTHY) -> np.ndarray:
    """Chance that the real value is >= level, from the three forecast numbers.

    The curve goes through (p10, 10%), (middle, 50%), (p90, 90%) and keeps the same
    slope beyond the ends until it reaches 0% / 100%. Clipped to 1%..99%: we never
    claim certainty.
    """
    pred, p10, p90 = (np.asarray(x, dtype=float) for x in (pred, p10, p90))
    eps = 1e-6
    p10 = np.minimum(p10, pred - eps)
    p90 = np.maximum(p90, pred + eps)
    lo_slope = (pred - p10) / 0.4           # µg/m³ per unit of probability, below the middle
    hi_slope = (p90 - pred) / 0.4           # ... and above it
    cdf = np.where(
        level <= pred,
        np.where(level >= p10, 0.1 + 0.4 * (level - p10) / (pred - p10),
                 0.1 - (p10 - level) / lo_slope),
        np.where(level <= p90, 0.5 + 0.4 * (level - pred) / (p90 - pred),
                 0.9 + (level - p90) / hi_slope))
    return np.clip(1.0 - cdf, 0.01, 0.99)


def advise(prob: np.ndarray, cost_ratio: float = DEFAULT_COST_RATIO) -> np.ndarray:
    return np.asarray(prob) >= threshold_for(cost_ratio)


def _score(warn: np.ndarray, unhealthy: np.ndarray, cost_ratio: float) -> dict:
    warn, unhealthy = np.asarray(warn, bool), np.asarray(unhealthy, bool)
    hits = int((warn & unhealthy).sum())
    misses = int((~warn & unhealthy).sum())
    false_alarms = int((warn & ~unhealthy).sum())
    quiet = int((~warn & ~unhealthy).sum())
    n = len(warn)
    return {"days": n, "unhealthy_days": int(unhealthy.sum()), "advisories": int(warn.sum()),
            "hits": hits, "misses": misses, "false_alarms": false_alarms, "correct_quiet": quiet,
            "hit_rate": round(hits / max(hits + misses, 1), 3),
            "false_alarm_ratio": round(false_alarms / max(hits + false_alarms, 1), 3),
            # cost in "false-alarm units": each false alarm costs 1, each miss costs R
            "cost_per_100_days": round(100 * (false_alarms + cost_ratio * misses) / max(n, 1), 1)}


def evaluate(df: pd.DataFrame, cost_ratio: float = DEFAULT_COST_RATIO) -> dict:
    """Replay our rule and two simple rules on rows with target, prediction, p10, p90, baseline_same_info."""
    unhealthy = df["target"].to_numpy() >= UNHEALTHY
    prob = prob_exceed(df["prediction"], df["p10"], df["p90"])
    climatology = unhealthy.mean()
    brier = float(np.mean((prob - unhealthy) ** 2))
    brier_clim = float(np.mean((climatology - unhealthy) ** 2))
    return {
        "cost_ratio": cost_ratio, "threshold": round(threshold_for(cost_ratio), 3),
        "our_rule": _score(advise(prob, cost_ratio), unhealthy, cost_ratio),
        "warn_if_today_unhealthy": _score(df["baseline_same_info"].to_numpy() >= UNHEALTHY, unhealthy, cost_ratio),
        "warn_if_forecast_number_unhealthy": _score(df["prediction"].to_numpy() >= UNHEALTHY, unhealthy, cost_ratio),
        # Brier score: mean squared error of the probability (0 is perfect). The skill score compares
        # it with always saying the long-run base rate; > 0 means our probabilities add information.
        "probability_brier": round(brier, 4),
        "probability_brier_skill_vs_base_rate": round(1 - brier / brier_clim, 3) if brier_clim > 0 else None,
    }


def reliability(df: pd.DataFrame, bins=(0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001)) -> pd.DataFrame:
    """When we said 'about X% chance', how often was the day really unhealthy?"""
    prob = prob_exceed(df["prediction"], df["p10"], df["p90"])
    unhealthy = df["target"].to_numpy() >= UNHEALTHY
    cut = pd.cut(prob, bins, right=False)
    out = (pd.DataFrame({"bin": cut, "prob": prob, "unhealthy": unhealthy})
           .groupby("bin", observed=True).agg(days=("prob", "size"), said=("prob", "mean"), happened=("unhealthy", "mean"))
           .reset_index())
    out["bin"] = out["bin"].astype(str)
    return out.round(3)


def report(validation: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Everything the model card and dashboard need about the decision rule."""
    return {
        "unhealthy_level_ugm3": UNHEALTHY,
        "default_cost_ratio": DEFAULT_COST_RATIO,
        "rule": "issue an advisory when P(unhealthy) >= 1/(1+R), R = cost of a miss / cost of a false alarm",
        "validation": {str(r): evaluate(validation, r) for r in COST_RATIOS},
        "test": {str(r): evaluate(test, r) for r in COST_RATIOS},
        "reliability_validation": reliability(validation).to_dict("records"),
    }
