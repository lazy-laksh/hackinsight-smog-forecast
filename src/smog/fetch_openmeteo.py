"""Download weather and CAMS air-quality model data from Open-Meteo (no key needed).

What we download for each city centre:

1. Past weather, hourly, one file per month (data/raw/openmeteo_weather/).
   Wind, humidity, temperature, rain, pressure, cloud and boundary-layer
   height: the weather that traps smog or blows it away.
2. Past CAMS air-quality model values, hourly, one file per month
   (data/raw/openmeteo_air/). These are MODEL ESTIMATES, used only as an
   input feature, never as the target (see DATASET_DECLARATION.md).
3. Forecast snapshots (data/raw/openmeteo_forecast/): the weather and CAMS
   forecast for the next 3 days, saved with the time we fetched it. At
   prediction time the model may only use what was forecast BEFORE that
   day, so we keep every snapshot instead of overwriting it.

Past months are fetched once they are settled; Open-Meteo's reanalysis runs
about 5 days behind, so a month counts as settled 7 days after it ends.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from smog import config, manifest
from smog.api import ApiClient, ApiError
from smog.fetch_openaq import month_starts, next_month

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1"
FORECAST_URL = "https://api.open-meteo.com/v1"
AIR_URL = "https://air-quality-api.open-meteo.com/v1"

SETTLE_DAYS = 7
WEATHER_VARS = [
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
    "wind_speed_10m", "wind_direction_10m", "surface_pressure", "cloud_cover",
]
OPTIONAL_WEATHER_VARS = ["boundary_layer_height"]   # dropped automatically if not offered
AIR_VARS = ["pm2_5", "pm10", "dust", "aerosol_optical_depth", "carbon_monoxide", "nitrogen_dioxide"]


def make_clients() -> dict[str, ApiClient]:
    # Open-Meteo's free tier allows 600 calls a minute; we stay far below that.
    # A month of hourly data counts as several calls in Open-Meteo's fair-use limit, and a fresh run asks for
    # ~140 months at once; one request per second keeps a from-empty run well inside it (about 3 minutes).
    return {name: ApiClient(url, min_interval_s=1.0)
            for name, url in (("archive", ARCHIVE_URL), ("forecast", FORECAST_URL), ("air", AIR_URL))}


def is_settled(month: date, today: date) -> bool:
    return (today - next_month(month)).days >= SETTLE_DAYS


def _base_params(city: dict) -> dict:
    return {"latitude": city["lat"], "longitude": city["lon"], "timezone": "GMT"}


_REJECTED: set[str] = set()   # optional variables Open-Meteo refused during this run, per endpoint


def _get_weather(client: ApiClient, path: str, params: dict, variables: list[str]):
    """Ask for weather including the optional variables; if Open-Meteo says a
    variable does not exist, ask again without them and remember that."""
    optional = [] if path in _REJECTED else OPTIONAL_WEATHER_VARS
    try:
        return client.get(path, {**params, "hourly": ",".join(variables + optional)})
    except ApiError as error:
        if optional and error.status == 400 and any(v in str(error) for v in optional):
            _REJECTED.add(path)
            print(f"    note: Open-Meteo {path} does not offer {optional}; continuing without it")
            return client.get(path, {**params, "hourly": ",".join(variables)})
        raise


def _save(resp, path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    manifest.record(path, source, resp.url)


def fetch_month(clients: dict, kind: str, city_id: str, city: dict, month: date, today: date) -> str:
    """kind is 'weather' or 'air'. Returns 'skipped', 'new' or 'refreshed'."""
    source = "openmeteo_weather" if kind == "weather" else "openmeteo_air"
    path = config.RAW_DIR / source / city_id / f"{month:%Y-%m}.json"
    if path.exists() and is_settled(month, today):
        return "skipped"

    existed = path.exists()
    last_day = min(next_month(month) - timedelta(days=1), today)
    params = {**_base_params(city), "start_date": month.isoformat(), "end_date": last_day.isoformat()}
    if kind == "weather":
        resp = _get_weather(clients["archive"], "archive", params, WEATHER_VARS)
    else:
        resp = clients["air"].get("air-quality", {**params, "hourly": ",".join(AIR_VARS),
                                                  "domains": "cams_global"})
    _save(resp, path, source)
    return "refreshed" if existed else "new"


def fetch_forecast_snapshot(clients: dict, city_id: str, city: dict, now: datetime) -> int:
    """Save this hour's weather and CAMS forecasts. Returns how many files were written."""
    stamp = f"{now:%Y%m%dT%H}"
    folder = config.RAW_DIR / "openmeteo_forecast" / city_id
    written = 0
    weather_path = folder / f"weather_{stamp}.json"
    if not weather_path.exists():
        params = {**_base_params(city), "past_days": 1, "forecast_days": 3}
        _save(_get_weather(clients["forecast"], "forecast", params, WEATHER_VARS),
              weather_path, "openmeteo_weather")
        written += 1
    air_path = folder / f"air_{stamp}.json"
    if not air_path.exists():
        params = {**_base_params(city), "past_days": 1, "forecast_days": 3,
                  "hourly": ",".join(AIR_VARS), "domains": "cams_global"}
        _save(clients["air"].get("air-quality", params), air_path, "openmeteo_air")
        written += 1
    return written


def run(clients: Optional[dict] = None, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    clients = clients or make_clients()
    _REJECTED.clear()
    start = date.fromisoformat(config.load_yaml("cities")["history_start"])
    months = month_starts(start, today)

    counts = {"new": 0, "refreshed": 0, "skipped": 0, "failed": 0}
    snapshots = 0
    for city_id, city in config.cities().items():
        for kind in ("weather", "air"):
            for month in months:
                try:
                    counts[fetch_month(clients, kind, city_id, city, month, today)] += 1
                except ApiError as error:
                    counts["failed"] += 1
                    print(f"    WARNING {kind} {city_id} {month:%Y-%m}: {error}")
        try:
            snapshots += fetch_forecast_snapshot(clients, city_id, city, now)
        except ApiError as error:
            counts["failed"] += 1
            print(f"    WARNING forecast {city_id}: {error}")

    total = len(config.cities()) * 2 * len(months)
    if counts["failed"] >= total:
        raise ApiError("every Open-Meteo download failed; see the warnings above")
    calls = sum(c.calls for c in clients.values())
    return (f"{counts['new']} new + {counts['refreshed']} refreshed month files, "
            f"{counts['skipped']} already complete, {snapshots} forecast snapshots, "
            f"{counts['failed']} failed, {calls} API calls")
