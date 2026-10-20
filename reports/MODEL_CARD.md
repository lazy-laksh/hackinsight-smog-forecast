# Model card: next-day PM2.5 forecast

*Generated automatically by `python -m smog train` on 2026-10-20T09:01:05+00:00. Do not edit numbers by hand.*

## What it does
Predicts tomorrow's mean PM2.5 (µg/m³, Pakistan calendar day) for Lahore, Islamabad/Rawalpindi,
Karachi and Peshawar, issued at 20:00 PKT. Users: district environment officers deciding on
smog advisories. **Information, not professional or medical advice.**

## Data
Gold tables built from OpenAQ sensors (target), Open-Meteo weather, NASA FIRMS fires (VIIRS NOAA-20)
and CAMS model values (feature only). See DATASET_DECLARATION.md and the quality log.

## Method
Gradient-boosted trees (scikit-learn `HistGradientBoostingRegressor`, absolute-error loss), plus two
quantile models (10% and 90%) for an 80% range. Settings chosen by rolling-origin validation
(month by month, training only on the past): {'small': 15.36, 'medium': 16.1} MAE; chosen: **small**.
Random seed 42.

Training target days: 2025-06-02 to 2026-09-30 (1922 city-days).
**Test target days: 2026-10-01 to 2026-10-19 (76 city-days), never used for tuning.**

## Results on the test set
| | MAE (µg/m³) | RMSE | bias |
|---|---|---|---|
| Our model | 8.9 | 11.39 | 0.18 |
| Our model without any "tomorrow" inputs (strict check) | 10.18 | 13.31 | 1.77 |
| Declared baseline: persistence (today so far at 20:00) | 10.9 | 15.04 | -3.49 |
| Harder baseline: persistence using all of today | 9.8 | 13.17 | -1.13 |

Improvement over the declared baseline: **18.3%** (vs the harder baseline: 9.2%;
strict model without "tomorrow" inputs: 6.6%).
Declared success rule (at least 10% lower MAE than persistence, overall and for Lahore): overall **met**, Lahore **met**.

Unhealthy days (≥ 55.5 µg/m³): model recall 0.921,
precision 0.897; baseline recall 0.711,
precision 0.794 (38 unhealthy test days).

**Uncertainty:** the 80% range contained the real value 91% of the time
(ideal: about 80%); average width 36.9 µg/m³. For the officer this means: plan for the
upper end of the range when deciding on an advisory.

How the range was calibrated: the raw 10%/90% models were too confident (on the test set they held only
64% of real values, width 25.6 µg/m³). We widened both ends by
5.66 µg/m³, an amount chosen **only from the validation months**
(1458 city-days: coverage 59% → 80%),
using conformalized quantile regression. The test set was not used to choose it.

## From forecast to decision: "issue a smog advisory?"
We turn the calibrated range into a **chance that tomorrow is unhealthy** (mean ≥ 55.5 µg/m³) and
issue an advisory when that chance is at least 1/(1+R), where **R = how many times worse a missed unhealthy day
is than an unnecessary advisory**. R is a policy choice made by the user, not fitted to data; our default is
R = 3 (threshold 25%). Cost is counted as 1 per false alarm and R per miss.

| period | rule | days | unhealthy days | hits | misses | false alarms | cost per 100 days |
|---|---|---|---|---|---|---|---|
| validation | Our rule: warn if P(unhealthy) ≥ 25% | 1458 | 632 | 614 | 18 | 183 | 16.3 |
| validation | Warn if today is unhealthy (persistence) | 1458 | 632 | 558 | 74 | 69 | 20.0 |
| validation | Warn if the forecast number ≥ 55.5 | 1458 | 632 | 559 | 73 | 65 | 19.5 |
| test | Our rule: warn if P(unhealthy) ≥ 25% | 76 | 38 | 37 | 1 | 15 | 23.7 |
| test | Warn if today is unhealthy (persistence) | 76 | 38 | 27 | 11 | 7 | 52.6 |
| test | Warn if the forecast number ≥ 55.5 | 76 | 38 | 35 | 3 | 4 | 17.1 |

Probability quality on the validation months: Brier score 0.0688 (skill vs always saying
the base rate: 0.72; above 0 means the probabilities carry real information).

## Errors by city (test set)
| city | days | model_mae | baseline_mae | improvement_pct | model_bias | interval_coverage |
|---|---|---|---|---|---|---|
| islamabad_rawalpindi | 19 | 6.53 | 10.7 | 39.0 | -0.15 | 0.89 |
| karachi | 19 | 4.45 | 4.16 | -7.0 | 0.32 | 0.89 |
| lahore | 19 | 12.52 | 14.56 | 14.0 | -2.97 | 0.89 |
| peshawar | 19 | 12.1 | 14.19 | 14.7 | 3.51 | 0.95 |

Weakest city on the test set: **lahore** (MAE 12.52). Users there should treat the
forecast as a rough guide and also check the official Punjab EPA / provincial readings.

## Errors by city and season (rolling-origin validation, before the test period)
| city | season | days | model_mae | baseline_mae | improvement_pct |
|---|---|---|---|---|---|
| islamabad_rawalpindi | rest of year (Mar-Sep) | 214 | 6.08 | 7.55 | 19.5 |
| islamabad_rawalpindi | smog season (Oct-Feb) | 151 | 15.51 | 15.26 | -1.6 |
| karachi | rest of year (Mar-Sep) | 214 | 2.92 | 3.73 | 21.7 |
| karachi | smog season (Oct-Feb) | 150 | 13.08 | 17.71 | 26.1 |
| lahore | rest of year (Mar-Sep) | 214 | 10.77 | 14.44 | 25.4 |
| lahore | smog season (Oct-Feb) | 151 | 41.7 | 27.34 | -52.5 |
| peshawar | rest of year (Mar-Sep) | 214 | 8.6 | 11.43 | 24.8 |
| peshawar | smog season (Oct-Feb) | 150 | 38.19 | 37.03 | -3.1 |

## Limitations (what this model cannot tell you)
- Trained on low-cost sensors (no humidity correction applied); values may read high in very humid air.
- Training uses archived weather and CAMS values for "tomorrow", which are closer to the truth than the
  forecasts available at 20:00 the evening before. Live errors may therefore be larger than the test
  errors above; the "strict check" row shows the result with no "tomorrow" inputs at all.
- The test period is short and covers only the start of the smog season.
- One value per city: neighbourhoods can be much worse than the city average.
- Health decisions: follow official advisories (Punjab EPA, PMD, NDMA) and seek medical help in an emergency.
