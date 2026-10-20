# Datasheet: where each source comes from and what it cannot tell you

Every raw file is listed in `data/manifest.csv` with source, query, fetch time (UTC), SHA-256 and licence.
Counts below come from the pipeline logs on 20 Oct 2026 and grow every hour.

| | OpenAQ PM2.5 sensors (target) | NASA FIRMS fires | Open-Meteo weather | CAMS air-quality model |
|---|---|---|---|---|
| Original publisher | Each location's owner (recorded per sensor, e.g. The Urban Unit, Punjab); aggregated by OpenAQ | NASA LANCE / ESDIS | Open-Meteo from ECMWF ERA5/IFS | Copernicus Atmosphere Monitoring Service, served by Open-Meteo |
| Access | API v3 with free key; 60 requests/min, 2,000/h | Area API with free MAP_KEY | Archive + forecast API, no key | Air-quality API, no key |
| Licence | Per location, mostly CC BY 4.0 (stored per manifest row) | NASA open data; acknowledgement required | CC BY 4.0; free API is non-commercial | CC BY 4.0 |
| Coverage used | 202 sensors within 25 km of each city centre (Lahore 87, Islamabad/Rawalpindi 39, Karachi 53, Peshawar 23), hourly, June 2025 → now | VIIRS NOAA-20 detections in a box covering Pakistani and Indian Punjab, Sindh and KP | Hourly, city centres | Hourly, city centres |
| Update frequency | Hourly, provider delay from minutes to hours | Near-real-time ~3 h after overpass; standard product later | Archive daily (settles after ~7 days); forecast several times a day | About twice a day |
| Volume | ~900,000 hourly readings | ~285,000 detections | ~48,600 hourly rows + forecast snapshots | ~48,600 hourly rows + forecast snapshots |
| Role | Target and "smog already in the air" features | Crop-burning features (fires within 100 / 300 km) | Wind, rain, humidity, temperature, mixing height | One input feature only, never the target |
| Known biases and limits | Low-cost optical sensors read high in very humid air; more sensors in richer/central areas; some sensors stuck or spiking (flagged by the Data Quality Agent); some sensor-months return HTTP 500 and are logged as known gaps | Clouds and thick haze hide fires; satellites pass only at fixed times; small fires missed; SNPP missing May 2026, so NOAA-20 only | Grid cells are kilometres wide, so city heat and street-level wind are smoothed; archive differs from the forecast available on the evening before | Coarse grid; a model, not a measurement |

## Quality handling (summary)
Stuck, spiking, zero and impossible readings are **flagged** and kept out of city averages; 1–2 hour gaps are
**filled** and marked; long gaps stay **missing**; sources older than their limit are **flagged as stale** on the
dashboard. Every decision is in `qc.decisions` and `data/logs/quality_log.csv` with rule, reason and row count.

## Independence
OpenAQ (ground sensors), FIRMS (satellites) and Open-Meteo/CAMS (weather and atmospheric models) are produced by
different organisations from different instruments. FIRMS is not on the guide's air-quality track list.
