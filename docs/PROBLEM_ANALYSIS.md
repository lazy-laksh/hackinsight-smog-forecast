# Problem analysis (the guide's seven steps)

## 1. Primary user and the decision
**User:** a district environment officer (Punjab EPA / district administration) and, through them, school heads and
hospital managers in Lahore, Islamabad/Rawalpindi, Peshawar and Karachi.
**Decision:** every evening, whether to issue a smog advisory for tomorrow (outdoor activity limits for schools, masks,
hospital smog counters, enforcement against burning), instead of reacting to the next morning's reading.

## 2. The problem, with evidence
* Smog is a recurring seasonal emergency in Punjab. The Punjab Environment Department reports that Lahore's daily PM2.5
  peak in November 2024 was 539 µg/m³, and 237 µg/m³ in November 2025 after enforcement
  ([Punjab EPD, Dec 2025](https://epd.punjab.gov.pk/system/files/Punjab-PR-smog_0.pdf)). Both are many times the WHO
  24-hour guideline level of 15 µg/m³ ([WHO 2021 AQG](https://www.airclim.org/who-air-quality-guidelines)) and above
  Pakistan's own 24-hour standard of 35 µg/m³ ([NEQS, S.R.O. 1062(I)/2010](https://faolex.fao.org/docs/pdf/pak206474.pdf)).
* The government is acting **before** the season: the 2026 Pre-Smog Action Plan tells schools to check the AQI before
  outdoor activity and to restrict outdoor play when air is poor ([Dawn, 19 Aug 2026](https://www.dawn.com/news/2023819)).
  Smog reached Islamabad, Rawalpindi and Lahore before the official season in September 2026
  ([Express Tribune, 28 Sep 2026](https://tribune.com.pk/story/2631802/smog-hits-twin-cities-before-season)).
* Punjab's Smog Action Plan 2025-26 explicitly calls for a monitoring dashboard with **early warnings using AI-based
  forecasting** ([P&D Board](https://pnd.punjab.gov.pk/index.php/node/3956)). Our project is a working, open version of
  that early warning.

## 3. Who is affected and where
* **People:** children (lungs still developing), older adults and people with heart or lung disease are the sensitive
  groups ([US EPA guide](https://portal.ct.gov/-/media/deep/air/aqi/airqualityguideforparticlespdf.pdf)); outdoor workers
  and commuters are exposed longest.
* **Places:** the four cities above. Lahore has the worst and longest smog season; Karachi's air is usually cleaner and
  more stable. These cities are our **groups** for error analysis, together with **season**.

## 4. Outcome predicted
Tomorrow's **daily mean PM2.5 (µg/m³)** for each city, on the Pakistan calendar day, issued at **20:00 PKT** the
evening before, updated hourly. Derived outcome: chance that tomorrow is **unhealthy (≥ 55.5 µg/m³, the lower edge of
the US EPA "Unhealthy" band)** and an advisory recommendation.

## 5. Baseline and success measure (declared before results)
Baseline: **persistence**, tomorrow equals today's mean so far at 20:00. Metric: MAE. Success: at least **10% lower MAE
than persistence, overall and for Lahore**, on target days from 1 October 2026 (never used for tuning).
See [DATASET_DECLARATION.md](../DATASET_DECLARATION.md) and the generated [model card](../reports/MODEL_CARD.md).

## 6. Limits and risks
* Sensors: mostly low-cost optical sensors without humidity correction; uneven coverage (Lahore has many more sensors
  than Peshawar); one value per city hides neighbourhood hot spots.
* Data gaps: some OpenAQ sensor-months return server errors and are logged as known gaps; VIIRS SNPP fire data is
  missing for May 2026, so we use VIIRS NOAA-20 only.
* Weather: training used archived weather for "tomorrow", which is closer to the truth than a real forecast, so live
  errors may be larger than test errors.
* Short test period at the start of the smog season.
* Wrong forecasts can mislead: a missed smog day exposes people; a false alarm disrupts schools.

## 7. Recommendation and what we do not claim
We recommend an advisory when the chance of an unhealthy day passes the officer's own threshold (default 25%), with the
reasons shown. Outputs are **information, not medical or professional advice**. People should follow official
advisories from Punjab EPA, PDMA and PMD, and in a medical emergency call **Rescue 1122** or go to the nearest hospital.
We do not forecast individual neighbourhoods, other cities, or more than one day ahead.
