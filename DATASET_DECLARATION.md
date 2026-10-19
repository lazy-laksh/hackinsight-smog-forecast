# Dataset Declaration

**Team:** XYZ
**Track:** Air quality
**Date declared:** 19 October 2026 (PKT)

## Problem

Forecast **next-day smog (PM2.5) levels** for four Pakistani cities: **Lahore, Islamabad/Rawalpindi, Karachi and Peshawar**.

**Primary user and decision:** a district environment officer or deputy commissioner who decides each evening whether to issue a smog/health advisory or change school timings for the next day.

---

## Source 1: OpenAQ, Pakistan low-cost PM2.5 sensor network (main target data)

| Field | Value |
|---|---|
| Name | Hourly PM2.5 (plus PM1, temperature, relative humidity) from air sensors in Pakistan |
| Original publisher / owner | Recorded per location. Example checked: *Gulberg III, Lahore*, owner **The Urban Unit** (Punjab), instrument AirGradient Open Air, provider **AirGradient** |
| Access | OpenAQ API v3, https://api.openaq.org/v3 (platform: https://openaq.org) |
| Licence | **CC BY 4.0** (checked on the location page). The licence of every location is recorded in `manifest.csv`. Attribution to the data owner and to OpenAQ. |
| Update frequency | Hourly |
| Coverage | About 60 locations in Lahore, 19 in Islamabad/Rawalpindi, 19 in Karachi and 10 in Peshawar with recent updates (counted on OpenAQ Explorer, 19 Oct 2026). Most report from around June 2025. |

## Source 2: NASA FIRMS active fire detections (not on the track list)

| Field | Value |
|---|---|
| Name | Fire Information for Resource Management System (FIRMS): VIIRS 375 m (NOAA-20, Suomi NPP) and MODIS C6.1 |
| Publisher | NASA Earth Science Data and Information System (ESDIS) / LANCE |
| URL / DOI | https://firms.modaps.eosdis.nasa.gov/api/ — DOIs: 10.5067/FIRMS/VIIRS/VJ114IMGT_NRT.002, 10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002, 10.5067/FIRMS/MODIS/MCD14DL.NRT.0061 |
| Licence | NASA open data policy (full and open sharing); FIRMS acknowledgement included in the README |
| Update frequency | Near real time (within about 3 hours of a satellite pass) |
| Area | Bounding box covering Pakistani Punjab, Khyber Pakhtunkhwa, Islamabad and Indian Punjab (smoke crosses the border) |

## Source 3: Open-Meteo historical and forecast weather

| Field | Value |
|---|---|
| Name | Hourly weather: wind speed and direction, temperature, relative humidity, precipitation, boundary-layer height |
| Publisher | Open-Meteo (reanalysis from ECMWF ERA5 / IFS) |
| URL | https://open-meteo.com |
| Licence | CC BY 4.0; "Weather data by Open-Meteo.com" shown wherever the data is displayed |
| Update frequency | Hourly |

## Source 4: Open-Meteo Air Quality (Copernicus CAMS global), input feature only

| Field | Value |
|---|---|
| Name | Modelled hourly PM2.5 and PM10 from the CAMS global atmospheric composition model |
| Publisher | Copernicus Atmosphere Monitoring Service (CAMS), served by Open-Meteo |
| URL | https://open-meteo.com/en/docs/air-quality-api |
| Licence | CC BY 4.0, with attribution to CAMS and Open-Meteo |
| Use | **Model estimates, not measurements.** Used only as an input feature, never as the target or for evaluation. |

## Local validation only (not used for training)

**Punjab EPA official AQI portal**, https://aqi.punjab.gov.pk: official, hourly, regulatory-grade stations. Its robots.txt allows access, but the site states no data licence. We therefore use it only to compare a small number of our forecasts with official readings, cited by date and time, and do not train on it.

## Considered and not used

**US Department of State embassy and consulate monitors (AirNow).** These are reference-grade PM2.5 monitors in Lahore, Karachi, Islamabad and Peshawar (Lahore SiteID 586PK3010003), with data from 2019. AirNow removed the public feed and historical download page in March 2025, so the data cannot be gathered with a stated source and licence during the event, and cannot support a live hourly pipeline.

---

## Which source is not on the track list, and why is it credible?

**NASA FIRMS.** It is published by NASA's Earth Science Data and Information System, has DOIs, is used widely in peer-reviewed research on crop-residue burning and smog in South Asia, and is fully independent of the ground sensors (satellite observation versus ground measurement).

## Expected volume

- Source 1: about 108 sensor locations, each up to about 11,500 hourly records (June 2025 to October 2026). We expect **several hundred thousand hourly rows**. The 50,000-row minimum is met even if only 5 sensors have complete history.
- Source 2: fire detections over at least one full burning season (October 2025 to February 2026) and the current one.
- Exact row counts will be reported from `manifest.csv` after the first fetch.

## Why this data fits the problem and the user

Smog in these cities is driven by local emissions, crop-residue fires and weather that traps pollution near the ground (low wind, cold nights, humidity). The sensors measure the outcome hour by hour across many neighbourhoods, FIRMS measures the fires, and Open-Meteo measures the weather. Together they cover the main causes and the effect, at the hourly speed an evening advisory decision needs.

## Target and declared baseline (fixed before any results)

- **Target:** next-day mean PM2.5 (µg/m³) per city, for the calendar day in PKT, computed from the hourly sensor data. Secondary target: whether that mean is at or above **55.5 µg/m³** (the US EPA "Unhealthy" 24-hour PM2.5 breakpoint).
- **Baseline:** **persistence**: tomorrow's mean PM2.5 in a city equals today's mean.
- **Metric:** mean absolute error (MAE, µg/m³), reported overall and per city.
- **Split:** time-based. The final test period is **1 October 2026 onwards** and is never used for tuning. Model selection uses rolling-origin validation on earlier months.
- **Success:** at least **10% lower MAE than persistence**, overall and for Lahore.

## Data rules we follow

- All data is fetched by our code during the event; every raw file is logged in `manifest.csv` (source, query, fetch time, SHA-256, licence).
- Timestamps are stored in UTC and converted to PKT only for display.
- No personal or identifiable data. No synthetic or AI-generated data for training or evaluation.
