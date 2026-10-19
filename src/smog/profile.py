"""Print a quick profile of the cleaned tables: `python -m smog profile`.

We look at the real data BEFORE writing the Data Quality Agent's rules, so the
rules come from evidence (what values, gaps and oddities actually exist), not
from guesses. Read-only: it never changes any data.
"""
from __future__ import annotations

from smog import warehouse

QUERIES = {
    "Rows per table": """
        SELECT table_name, estimated_size AS rows
        FROM duckdb_tables() WHERE schema_name = 'cleaned' ORDER BY table_name""",

    "PM2.5 values (µg/m³)": """
        SELECT count(*) AS hours,
               round(100.0 * count(*) FILTER (WHERE pm25 IS NULL) / count(*), 2) AS pct_missing,
               min(pm25) AS min,
               round(quantile_cont(pm25, 0.01), 1) AS p01,
               round(quantile_cont(pm25, 0.50), 1) AS median,
               round(quantile_cont(pm25, 0.99), 1) AS p99,
               max(pm25) AS max,
               count(*) FILTER (WHERE pm25 < 0) AS negative,
               count(*) FILTER (WHERE pm25 = 0) AS exactly_zero,
               count(*) FILTER (WHERE pm25 > 1000) AS above_1000,
               count(*) FILTER (WHERE observed_count < 3) AS hours_with_under_3_readings,
               count(*) FILTER (WHERE has_flags) AS flagged_by_openaq
        FROM cleaned.pm25_hourly""",

    "PM2.5 by city": """
        SELECT s.city_id,
               count(DISTINCT p.sensor_id) AS sensors_with_data,
               count(DISTINCT p.sensor_id) FILTER (WHERE p.ts_utc >= now() - INTERVAL 7 DAY) AS active_last_7_days,
               count(*) AS hours,
               round(median(p.pm25), 1) AS median_pm25,
               min(p.ts_utc)::DATE AS first_day,
               max(p.ts_utc) AS last_hour_utc
        FROM cleaned.pm25_hourly p JOIN cleaned.sensors s USING (sensor_id)
        GROUP BY 1 ORDER BY 1""",

    "Sensors that report the same value many hours in a row (possible stuck sensor)": """
        WITH runs AS (
            SELECT sensor_id, pm25,
                   row_number() OVER (PARTITION BY sensor_id ORDER BY ts_utc)
                 - row_number() OVER (PARTITION BY sensor_id, pm25 ORDER BY ts_utc) AS grp
            FROM cleaned.pm25_hourly WHERE pm25 IS NOT NULL)
        SELECT sensor_id, pm25, count(*) AS hours_in_a_row
        FROM runs GROUP BY sensor_id, pm25, grp HAVING count(*) >= 24
        ORDER BY hours_in_a_row DESC LIMIT 10""",

    "Biggest jumps between consecutive hours (possible spikes)": """
        SELECT sensor_id, ts_utc, prev, pm25, round(pm25 - prev, 1) AS jump
        FROM (SELECT sensor_id, ts_utc, pm25,
                     lag(pm25) OVER (PARTITION BY sensor_id ORDER BY ts_utc) AS prev
              FROM cleaned.pm25_hourly)
        WHERE prev IS NOT NULL ORDER BY abs(pm25 - prev) DESC LIMIT 10""",

    "Fires per month (after removing NRT duplicates of SP days)": """
        SELECT date_trunc('month', acq_ts_utc)::DATE AS month, family, count(*) AS detections
        FROM cleaned.fires GROUP BY 1, 2 ORDER BY 1, 2""",

    "Weather: missing values per column (%)": """
        SELECT city_id, count(*) AS hours,
               round(100.0 * count(*) FILTER (WHERE temperature_2m IS NULL) / count(*), 2) AS temp_missing,
               round(100.0 * count(*) FILTER (WHERE wind_speed_10m IS NULL) / count(*), 2) AS wind_missing,
               round(100.0 * count(*) FILTER (WHERE boundary_layer_height IS NULL) / count(*), 2) AS blh_missing,
               max(ts_utc) AS last_hour_utc
        FROM cleaned.weather_hourly GROUP BY 1 ORDER BY 1""",
}


def run() -> None:
    import pandas as pd
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    with warehouse.connect(read_only=True) as con:
        for title, sql in QUERIES.items():
            print(f"\n=== {title} ===")
            try:
                print(con.execute(sql).df().to_string(index=False))
            except Exception as error:   # e.g. a column that a source did not provide
                print(f"(could not compute: {error})")
