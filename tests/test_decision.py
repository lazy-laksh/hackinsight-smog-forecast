"""Tests for the decision layer (probability of an unhealthy day + cost-loss advisory rule)."""
import numpy as np
import pandas as pd

from smog import decision


def test_probability_goes_through_the_three_points():
    p = decision.prob_exceed([60, 60, 60], [40, 40, 40], [80, 80, 80], level=np.array([40.0, 60.0, 80.0]))
    assert np.allclose(p, [0.9, 0.5, 0.1])


def test_probability_moves_the_right_way_and_never_claims_certainty():
    preds = np.array([20.0, 50.0, 55.5, 70.0, 200.0])
    p = decision.prob_exceed(preds, preds - 10, preds + 10)
    assert np.all(np.diff(p) >= 0) and p[-1] > p[0]   # higher forecast -> higher (or capped) chance
    assert p.min() >= 0.01 and p.max() <= 0.99


def test_zero_width_range_does_not_crash():
    assert np.isfinite(decision.prob_exceed([60.0], [60.0], [60.0])).all()


def test_cost_loss_threshold():
    assert decision.threshold_for(1) == 0.5 and decision.threshold_for(3) == 0.25
    assert list(decision.advise(np.array([0.2, 0.3]), cost_ratio=3)) == [False, True]


def test_scores_add_up():
    df = pd.DataFrame({"target": [80, 30, 70, 20], "prediction": [75, 60, 40, 25],
                       "p10": [60, 45, 30, 15], "p90": [90, 75, 55, 35], "baseline_same_info": [70, 70, 20, 20]})
    e = decision.evaluate(df, cost_ratio=3)
    s = e["our_rule"]
    assert s["hits"] + s["misses"] + s["false_alarms"] + s["correct_quiet"] == 4
    assert e["warn_if_today_unhealthy"] == {**e["warn_if_today_unhealthy"], "hits": 1, "false_alarms": 1, "misses": 1}
