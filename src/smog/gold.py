"""Build the GOLD layer from the quality-checked data (schema `gold`).

Every gold row has a stable `row_id`, so any number shown on the dashboard or
written by the Insight Agent can be traced back to exactly the rows behind it.

Tables:
  city_hourly   city x hour: median PM2.5 of the usable sensors, and how many sensors
  city_daily    city x day (Pakistan time): mean of the hourly medians  <- the model's target
  weather_daily city x day: weather that traps or clears smog
  fires_daily   city x day: VIIRS NOAA-20 fire detections within 100 km and 300 km
  cams_daily    city x day: CAMS model PM2.5 (a feature only, never the target)
  freshness     source x newest data x status (copied from the quality agent)

Why "day in Pakistan time": an environment officer's "tomorrow" is a PKT calendar
day. We store UTC everywhere and convert only here, where a day is defined.
"""
from __future__ import annotations

from smog import config, warehouse

PKT_OFFSET = "INTERVAL 5 HOUR"     # Pakistan has no daylight saving: always UTC+5
MIN_SENSORS_PER_HOUR = 1           # an hour counts for a city if at least one usable sensor reported
MIN_HOURS_PER_DAY = 18             # a day's mean needs at least 18 of 24 hours, else it is marked incomplete
FIRE_FAMILY = "VIIRS_NOAA20"       # complete every month (see quality agent: fire_product_gap)

SQL = {
    # Measured values first: filled estimates count only for a city-hour where no
    # sensor actually measured anything (so estimates never outvote measurements).
    "city_hourly": f"""
        WITH u AS (
            SELECT *, count(*) FILTER (WHERE qc_status = 'ok') OVER (PARTITION BY city_id, ts_utc) AS n_measured
            FROM qc.pm25_hourly WHERE usable)
        SELECT city_id, ts_utc,
               round(median(pm25), 2) AS pm25_median,
               round(avg(pm25), 2) AS pm25_mean,
               count(*) AS n_sensors,
               count(*) FILTER (WHERE qc_status = 'filled') AS n_filled,
               'cityhour|' || city_id || '|' || strftime(ts_utc, '%Y%m%dT%H') AS row_id
        FROM u
        WHERE qc_status = 'ok' OR n_measured = 0
        GROUP BY city_id, ts_utc
        HAVING count(*) >= {MIN_SENSORS_PER_HOUR}
        ORDER BY city_id, ts_utc""",

    "city_daily": f"""
        SELECT city_id,
               CAST(ts_utc + {PKT_OFFSET} AS DATE) AS day_pkt,
               round(avg(pm25_median), 2) AS pm25_mean,
               round(max(pm25_median), 2) AS pm25_max_hour,
               count(*) AS hours_covered,
               round(avg(n_sensors), 1) AS avg_sensors,
               count(*) >= {MIN_HOURS_PER_DAY} AS complete,
               'cityday|' || city_id || '|' || strftime(CAST(ts_utc + {PKT_OFFSET} AS DATE), '%Y%m%d') AS row_id
        FROM gold.city_hourly
        GROUP BY city_id, CAST(ts_utc + {PKT_OFFSET} AS DATE)
        ORDER BY city_id, day_pkt""",

    "weather_daily": f"""
        SELECT city_id,
               CAST(ts_utc + {PKT_OFFSET} AS DATE) AS day_pkt,
               round(avg(temperature_2m), 2) AS temp_mean,
               round(min(temperature_2m), 2) AS temp_min,
               round(avg(relative_humidity_2m), 1) AS rh_mean,
               round(avg(wind_speed_10m), 2) AS wind_mean_kmh,
               -- average wind direction must be computed as a vector, not a plain mean of degrees
               round((degrees(atan2(avg(sin(radians(wind_direction_10m))),
                                    avg(cos(radians(wind_direction_10m))))) + 360) % 360, 0) AS wind_dir_mean,
               round(sum(precipitation), 2) AS precip_sum_mm,
               round(avg(boundary_layer_height), 0) AS blh_mean_m,
               round(min(boundary_layer_height), 0) AS blh_min_m,
               round(avg(surface_pressure), 1) AS pressure_mean,
               count(*) AS hours_covered,
               'weatherday|' || city_id || '|' || strftime(CAST(ts_utc + {PKT_OFFSET} AS DATE), '%Y%m%d') AS row_id
        FROM qc.weather_observed
        GROUP BY city_id, CAST(ts_utc + {PKT_OFFSET} AS DATE)
        ORDER BY city_id, day_pkt""",

    "fires_daily": f"""
        WITH c AS (SELECT * FROM (VALUES {{city_values}}) t(city_id, lat, lon)),
        f AS (SELECT lat AS flat, lon AS flon, frp, CAST(acq_ts_utc + {PKT_OFFSET} AS DATE) AS day_pkt
              FROM cleaned.fires WHERE family = '{FIRE_FAMILY}'),
        d AS (  -- great-circle distance in km (haversine)
            SELECT c.city_id, f.day_pkt, f.frp,
                   2 * 6371 * asin(sqrt(pow(sin(radians(f.flat - c.lat) / 2), 2)
                       + cos(radians(c.lat)) * cos(radians(f.flat)) * pow(sin(radians(f.flon - c.lon) / 2), 2))) AS km
            FROM c CROSS JOIN f)
        SELECT city_id, day_pkt,
               count(*) FILTER (WHERE km <= 100) AS fires_100km,
               count(*) FILTER (WHERE km <= 300) AS fires_300km,
               round(coalesce(sum(frp) FILTER (WHERE km <= 300), 0), 1) AS frp_300km,
               'fireday|' || city_id || '|' || strftime(day_pkt, '%Y%m%d') AS row_id
        FROM d GROUP BY city_id, day_pkt ORDER BY city_id, day_pkt""",

    "cams_daily": f"""
        SELECT city_id, CAST(ts_utc + {PKT_OFFSET} AS DATE) AS day_pkt,
               round(avg(pm2_5), 2) AS cams_pm25_mean,
               round(avg(dust), 2) AS cams_dust_mean,
               round(avg(aerosol_optical_depth), 3) AS cams_aod_mean,
               'camsday|' || city_id || '|' || strftime(CAST(ts_utc + {PKT_OFFSET} AS DATE), '%Y%m%d') AS row_id
        FROM cleaned.cams_hourly
        GROUP BY city_id, CAST(ts_utc + {PKT_OFFSET} AS DATE) ORDER BY city_id, day_pkt""",

    "freshness": "SELECT * FROM qc.source_freshness",
}


def run() -> str:
    cities = config.cities()
    city_values = ", ".join(f"('{cid}', {c['lat']}, {c['lon']})" for cid, c in cities.items())
    counts = {}
    with warehouse.connect() as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")
        for name, sql in SQL.items():
            con.execute(f"CREATE OR REPLACE TABLE gold.{name} AS {sql.replace('{city_values}', city_values)}")
            counts[name] = con.execute(f"SELECT count(*) FROM gold.{name}").fetchone()[0]
        # Parquet copies, for the dashboard and for anyone who wants the gold data without DuckDB.
        config.GOLD_DIR.mkdir(parents=True, exist_ok=True)
        for name in counts:
            path = (config.GOLD_DIR / f"{name}.parquet").as_posix()
            con.execute(f"COPY gold.{name} TO '{path}' (FORMAT parquet)")
    return ", ".join(f"{k} {v:,}" for k, v in counts.items()) + " rows"
