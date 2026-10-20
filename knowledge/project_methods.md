---
id: project-methods
title: How this project's pipeline, quality checks, model and advisory rule work
publisher: This project (source code in src/smog/; this file summarises it)
url: https://github.com/lazy-laksh/hackinsight-smog-forecast
accessed: 2026-10-20
---
## Data sources
The pipeline uses hourly PM2.5 from OpenAQ sensors within 25 km of each city centre, NASA FIRMS fire detections
(VIIRS NOAA-20), Open-Meteo weather (wind, rain, temperature, humidity, boundary layer height) and CAMS model PM2.5
values from Open-Meteo. Every raw file is recorded in data/manifest.csv with its SHA-256 fingerprint, source, query,
fetch time and licence, and every run checks the files against those fingerprints.

## Hourly pipeline
A Prefect flow runs at 5 minutes past every hour: download, verify fingerprints, build the cleaned layer, run the Data
Quality Agent, build the gold tables, forecast tomorrow, and publish the results to the public dashboard. A failed
download is retried twice and the run continues with existing data (the source then shows as stale). A failed quality
gate stops the run, so gold tables and forecasts are not rebuilt from bad data.

## Data Quality Agent: what it flags
A sensor reading is flagged and kept out of the city average when the same exact value repeats for 6 or more
consecutive hours (a stuck sensor), when one hour is more than 4 times its neighbours and more than 200 µg/m³ above
them (a spike), when a value above 500 µg/m³ has no neighbour within 3 hours, when the value is exactly 0, or when it
is below 0 or above 2000 µg/m³. Gaps of 1 or 2 hours are filled with a straight-line estimate; longer gaps stay
missing. Filled values are used only for a city-hour where no sensor measured anything. Every decision is logged
with its rule, reason and the number of rows affected.

## Gold tables and days
All times are stored in UTC. A "day" is a Pakistan calendar day (UTC+5). The city's hourly value is the median of the
usable sensors; the daily value is the mean of the hourly values, and a day counts as complete with at least 18 hours.

## The forecast model
A gradient-boosted tree model (scikit-learn HistGradientBoostingRegressor) predicts tomorrow's mean PM2.5 using only
what is known at 20:00 PKT: today's readings so far, recent days, weather today and the weather forecast for
tomorrow, nearby fires and the CAMS model value. Settings are chosen by month-by-month rolling-origin validation that
trains only on the past. The test period (target days from 1 October 2026) was never used for tuning. The baseline is
persistence: tomorrow equals today's mean so far at 20:00.

## The 80% range and its calibration
Two extra models give the 10% and 90% values. The raw range was too narrow, so both ends are widened by an amount
chosen only on the validation months (conformalized quantile regression), until about 80% of real values fall inside.

## From forecast to advisory
The calibrated range is turned into a chance that tomorrow's mean is at or above 55.5 µg/m³. An advisory is
recommended when that chance is at least 1/(1+R), where R is how many times worse a missed unhealthy day is than an
unnecessary advisory. R is a policy choice, not fitted to data; the default is R = 3, which gives a threshold of 25%.

## Why a forecast is high or low
For each forecast, each group of inputs is set to that city's typical (median) value and the model is run again; the
difference is that group's effect in µg/m³. Positive means the group pushes the forecast up. Effects can overlap, so
they are an approximation, not an exact split.

## Drift monitor
Every hourly run checks drift. Inputs (PM2.5 daily mean, wind speed, mixing height, humidity, fires within 300 km)
from the last 14 days are compared with the same weeks one year earlier, which the model learned from, using the
Population Stability Index (PSI). Because PSI is noisy with few days, its expected noise is subtracted first; then
below 0.10 is ok, 0.10 to 0.25 is watch, and above 0.25 is drift. Accuracy drift compares the live error of the
evening forecasts on finished days with the error on the locked test month: up to 1.25 times is ok, up to 1.5 times
is watch, more is drift. Results are shown on the dashboard and logged in data/logs/drift_log.csv.

## Local validation against Punjab EPA
Our city value comes mostly from low-cost OpenAQ sensors. As a spot check, it is compared with the mean PM2.5 of
Punjab EPA's government monitoring stations in Lahore (aqi.punjab.gov.pk) for the same hour, on the hours listed in
config/punjab_epa_reference.csv. The result is in reports/LOCAL_VALIDATION.md and on the dashboard's Data quality tab.
It covers only a few hours, so it is a check, not a correction.

## Limitations
The sensors are mostly low-cost sensors without humidity correction. Training used archived weather for "tomorrow",
which is closer to the truth than a real forecast, so live errors may be larger than test errors. One value per city
hides neighbourhood differences. This is information, not medical advice: follow official advisories.
