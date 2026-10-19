"""Build the CLEANED layer: data/raw/  ->  data/cleaned/*.parquet + DuckDB tables.

The cleaned layer only does safe, mechanical work. It does NOT decide what is
"bad data"; that is the Data Quality Agent's job, and it explains each decision.

What cleaning does:
- reads every raw file (always as UTF-8),
- fixes types (numbers are numbers, times are real UTC timestamps),
- checks units are the ones we expect and stops if they are not,
- removes duplicates (the same hour fetched twice, e.g. a month file AND its
  weekly parts; for fires, the NRT copy of days that also have final SP data),
- keeps missing values as missing (NULL), never as zero,
- remembers which raw file each row came from (`source_file`), so every
  number can be traced back to its source.

Tables produced (schema `cleaned` in data/warehouse.duckdb):
  sensors           one row per OpenAQ PM2.5 sensor (location, owner, licence)
  pm25_hourly       one row per sensor per hour
  fires             one row per satellite fire detection
  weather_hourly    one row per city per hour (past weather)
  cams_hourly       one row per city per hour (past CAMS model values)
  weather_forecast  one row per city per forecast-issue-time per hour
  cams_forecast     same, for CAMS forecasts
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from smog import config, warehouse

EXPECTED_PM25_UNIT = "µg/m³"
EXPECTED_WEATHER_UNITS = {
    "temperature_2m": "°C", "relative_humidity_2m": "%", "dew_point_2m": "°C",
    "precipitation": "mm", "wind_speed_10m": "km/h", "wind_direction_10m": "°",
    "surface_pressure": "hPa", "cloud_cover": "%", "boundary_layer_height": "m",
}
EXPECTED_AIR_UNITS = {
    "pm2_5": "μg/m³", "pm10": "μg/m³", "dust": "μg/m³",
    "carbon_monoxide": "μg/m³", "nitrogen_dioxide": "μg/m³",
    # aerosol_optical_depth has no unit (it is a ratio), so it is not checked.
}


class UnitError(ValueError):
    """A source sent a unit we did not expect. We stop rather than guess."""


def _same_unit(a: str, b: str) -> bool:
    # Open-Meteo writes "μ" (Greek mu) and OpenAQ writes "µ" (micro sign); same meaning.
    norm = lambda s: (s or "").replace("μ", "µ").replace(" ", "")
    return norm(a) == norm(b)


def _rel(path: Path) -> str:
    return path.resolve().relative_to(config.ROOT).as_posix()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


# --- OpenAQ --------------------------------------------------------------------

def build_sensors() -> pd.DataFrame:
    """Sensor metadata from the location snapshots (newest snapshot wins)."""
    folder = config.RAW_DIR / "openaq" / "locations"
    rows: dict[int, dict] = {}
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stem.split("_")[-2]):   # by date
        city_id = path.stem.rsplit("_", 2)[0]
        for loc in _read_json(path).get("results", []):
            coords = loc.get("coordinates") or {}
            for s in loc.get("sensors") or []:
                if (s.get("parameter") or {}).get("name") != "pm25" or loc.get("isMobile"):
                    continue
                rows[s["id"]] = {
                    "sensor_id": s["id"],
                    "location_id": loc["id"],
                    "location_name": loc.get("name"),
                    "city_id": city_id,
                    "latitude": coords.get("latitude"),
                    "longitude": coords.get("longitude"),
                    "owner": (loc.get("owner") or {}).get("name"),
                    "provider": (loc.get("provider") or {}).get("name"),
                    "is_reference_monitor": bool(loc.get("isMonitor")),
                    "licence": "; ".join(sorted({l.get("name", "") for l in loc.get("licenses") or []})) or None,
                    "first_utc": ((loc.get("datetimeFirst") or {}).get("utc")),
                    "last_utc": ((loc.get("datetimeLast") or {}).get("utc")),
                    "source_file": _rel(path),
                }
    df = pd.DataFrame(rows.values())
    if not df.empty:
        df["first_utc"] = _utc(df["first_utc"])
        df["last_utc"] = _utc(df["last_utc"])
    return df


# Only these fields are read from each hourly record; anything else is ignored.
_HOURS_SCHEMA = {"results": (
    "STRUCT(value DOUBLE, flagInfo STRUCT(hasFlags BOOLEAN), parameter STRUCT(units VARCHAR), "
    "period STRUCT(datetimeFrom STRUCT(utc VARCHAR)), "
    "coverage STRUCT(observedCount INTEGER, expectedCount INTEGER, percentCoverage DOUBLE))[]"
)}


def _fetch_times() -> pd.DataFrame:
    """When each raw file was fetched, from manifest.csv (used to pick the newest copy)."""
    from smog import manifest
    rows = manifest.load()
    return pd.DataFrame({"source_file": [r["file_path"] for r in rows],
                         "fetched_at": _utc(pd.Series([r["fetched_at_utc"] for r in rows], dtype="object"))})


def build_pm25_hourly() -> pd.DataFrame:
    """All OpenAQ hourly files, read by DuckDB (about 1 million rows in a few seconds)."""
    files = sorted((config.RAW_DIR / "openaq" / "hours").glob("sensor_*/*.json"))
    if not files:
        return pd.DataFrame()
    con = duckdb.connect()                 # in-memory: only used to read the JSON files
    con.execute("SET TimeZone = 'UTC'")
    df = con.execute("""
        SELECT filename AS source_file,
               CAST(r.period.datetimeFrom.utc AS TIMESTAMPTZ) AS ts_utc,
               r.value AS pm25,
               r.parameter.units AS unit,
               r.coverage.observedCount AS observed_count,
               r.coverage.expectedCount AS expected_count,
               r.coverage.percentCoverage AS percent_coverage,
               r.flagInfo.hasFlags AS has_flags
        FROM (SELECT filename, unnest(results) AS r
              FROM read_json(?, columns = ?, filename = true, format = 'auto'))
    """, [[str(f) for f in files], _HOURS_SCHEMA]).df()
    con.close()
    if df.empty:
        return df

    relative = {p: _rel(Path(p)) for p in df["source_file"].unique()}   # once per file, not per row
    df["source_file"] = df["source_file"].map(relative)
    bad = df[~df["unit"].map(lambda u: _same_unit(u, EXPECTED_PM25_UNIT))]
    if not bad.empty:
        row = bad.iloc[0]
        raise UnitError(f"{row['source_file']}: PM2.5 unit is {row['unit']!r}, expected {EXPECTED_PM25_UNIT!r}")

    df["sensor_id"] = df["source_file"].str.extract(r"sensor_(\d+)/", expand=False).astype("int64")
    # The same hour can appear in two files (a month file and a weekly part):
    # keep the copy that was fetched most recently, according to the manifest.
    df = df.merge(_fetch_times(), on="source_file", how="left")
    df = (df.dropna(subset=["ts_utc"])
            .sort_values("fetched_at", na_position="first")
            .drop_duplicates(["sensor_id", "ts_utc"], keep="last")
            .sort_values(["sensor_id", "ts_utc"])
            .reset_index(drop=True))
    return df[["sensor_id", "ts_utc", "pm25", "observed_count", "expected_count",
               "percent_coverage", "has_flags", "source_file"]]


# --- NASA FIRMS -------------------------------------------------------------------

def _firms_family(product: str) -> str:
    return product.rsplit("_", 1)[0]          # VIIRS_SNPP_SP -> VIIRS_SNPP


def _confidence_level(value) -> str | None:
    """VIIRS reports l/n/h; MODIS reports 0-100. Put both on one scale."""
    if pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in ("l", "low"):
        return "low"
    if text in ("n", "nominal"):
        return "nominal"
    if text in ("h", "high"):
        return "high"
    try:
        number = float(text)
    except ValueError:
        return None
    return "low" if number < 30 else ("nominal" if number < 80 else "high")


def build_fires() -> pd.DataFrame:
    folder = config.RAW_DIR / "firms"
    frames, sp_days = [], {}
    for path in sorted(folder.glob("*/*.csv")):
        product = path.parent.name
        family = _firms_family(product)
        first_day, days = path.stem.split("_")
        window = pd.date_range(first_day, periods=int(days.rstrip("d")), freq="D").date
        if product.endswith("_SP"):
            sp_days.setdefault(family, set()).update(window)
        df = pd.read_csv(path, dtype={"acq_time": str, "confidence": str, "version": str})
        if df.empty:
            continue
        df["product"] = product
        df["family"] = family
        df["source_file"] = _rel(path)
        frames.append(df)
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    # acq_time is HHMM in UTC, sometimes without the leading zero (e.g. "812").
    hhmm = df["acq_time"].str.zfill(4)
    df["acq_ts_utc"] = pd.to_datetime(df["acq_date"] + " " + hhmm.str[:2] + ":" + hhmm.str[2:], utc=True)
    df["acq_date"] = df["acq_ts_utc"].dt.date
    # Prefer final SP data: drop NRT rows for days that SP already covers.
    covered = pd.DataFrame([(fam, day) for fam, days in sp_days.items() for day in days],
                           columns=["family", "acq_date"]).assign(_sp_covered=True)
    df = df.merge(covered, on=["family", "acq_date"], how="left")
    df = df[~(df["product"].str.endswith("_NRT") & df["_sp_covered"].fillna(False).astype(bool))]

    df["confidence_level"] = df["confidence"].map(_confidence_level)
    out = df.rename(columns={"latitude": "lat", "longitude": "lon"})[
        ["family", "product", "lat", "lon", "acq_ts_utc", "acq_date", "frp",
         "confidence_level", "daynight", "source_file"]
    ]
    out = out.drop_duplicates(["product", "lat", "lon", "acq_ts_utc"])
    return out.sort_values(["acq_ts_utc", "family"]).reset_index(drop=True)


# --- Open-Meteo -------------------------------------------------------------------

def _openmeteo_frame(path: Path, expected_units: dict) -> pd.DataFrame:
    body = _read_json(path)
    hourly = body.get("hourly") or {}
    units = body.get("hourly_units") or {}
    for var, unit in units.items():
        if var in expected_units and not _same_unit(unit, expected_units[var]):
            raise UnitError(f"{_rel(path)}: {var} unit is {unit!r}, expected {expected_units[var]!r}")
    if body.get("utc_offset_seconds", 0) != 0:
        raise UnitError(f"{_rel(path)}: times are not UTC (offset {body.get('utc_offset_seconds')}s)")
    df = pd.DataFrame(hourly)
    if df.empty:
        return df
    df["ts_utc"] = _utc(df.pop("time"))
    for col in df.columns.drop("ts_utc"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["source_file"] = _rel(path)
    return df


def build_openmeteo_history(source: str, expected_units: dict) -> pd.DataFrame:
    frames = []
    for path in sorted((config.RAW_DIR / source).glob("*/*.json")):
        df = _openmeteo_frame(path, expected_units)
        if not df.empty:
            df.insert(0, "city_id", path.parent.name)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return (df.drop_duplicates(["city_id", "ts_utc"], keep="last")
              .sort_values(["city_id", "ts_utc"]).reset_index(drop=True))


def build_forecasts(prefix: str, expected_units: dict) -> pd.DataFrame:
    """Every forecast snapshot, with the hour it was issued (from the file name)."""
    frames = []
    for path in sorted((config.RAW_DIR / "openmeteo_forecast").glob(f"*/{prefix}_*.json")):
        df = _openmeteo_frame(path, expected_units)
        if df.empty:
            continue
        stamp = path.stem.split("_", 1)[1]                      # e.g. 20261019T14
        issued = datetime.strptime(stamp, "%Y%m%dT%H").replace(tzinfo=timezone.utc)
        df.insert(0, "issued_utc", pd.Timestamp(issued))
        df.insert(0, "city_id", path.parent.name)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["city_id", "issued_utc", "ts_utc"])


# --- Write everything ----------------------------------------------------------------

def run() -> str:
    tables = {
        "sensors": build_sensors(),
        "pm25_hourly": build_pm25_hourly(),
        "fires": build_fires(),
        "weather_hourly": build_openmeteo_history("openmeteo_weather", EXPECTED_WEATHER_UNITS),
        "cams_hourly": build_openmeteo_history("openmeteo_air", EXPECTED_AIR_UNITS),
        "weather_forecast": build_forecasts("weather", EXPECTED_WEATHER_UNITS),
        "cams_forecast": build_forecasts("air", EXPECTED_AIR_UNITS),
    }
    config.CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    with warehouse.connect() as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS cleaned")
        for name, df in tables.items():
            path = config.CLEANED_DIR / f"{name}.parquet"
            if df.empty:
                path.unlink(missing_ok=True)
                con.execute(f"DROP TABLE IF EXISTS cleaned.{name}")
                continue
            df.to_parquet(path, index=False)
            con.execute(f"CREATE OR REPLACE TABLE cleaned.{name} AS SELECT * FROM read_parquet(?)", [str(path)])
    return ", ".join(f"{name} {len(df):,}" for name, df in tables.items()) + " rows"
