"""Download hourly PM2.5 from OpenAQ into data/raw/openaq/.

How it works, step by step:
1. For each city, ask OpenAQ for every location within 25 km of the centre.
   Save that list as a raw file (one per city per day).
2. From each location, take its PM2.5 sensor(s).
3. For each sensor, download its hourly averages one calendar month at a time
   (a month has at most 744 hours, so it always fits in one page of 1,000).
4. Save every response exactly as received and record it in manifest.csv.

To save time and API calls:
- A month that is finished (ended more than 2 days ago) is downloaded once and
  never again. It is history and does not change.
- The current month is downloaded again on every run, because new hours keep
  arriving. Its raw file is replaced and its manifest row updated.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from smog import config, manifest
from smog.api import ApiClient, ApiError

BASE_URL = "https://api.openaq.org/v3"
RAW_DIR_NAME = "openaq"
PM25 = "pm25"
SETTLE_DAYS = 2   # a month is "finished" once it ended at least this many days ago


@dataclass
class Sensor:
    sensor_id: int
    location_id: int
    location_name: str
    city_id: str
    licence: str
    first: Optional[datetime]   # first measurement (UTC), if OpenAQ reports it
    last: Optional[datetime]    # latest measurement (UTC)


# --- Small helpers -------------------------------------------------------------

def raw_dir() -> Path:
    return config.RAW_DIR / RAW_DIR_NAME


def make_client() -> ApiClient:
    return ApiClient(BASE_URL, headers={"X-API-Key": config.get_key("OPENAQ_API_KEY")})


def _parse_utc(value: Optional[dict]) -> Optional[datetime]:
    """OpenAQ dates look like {"utc": "2025-06-04T10:00:00Z", "local": ...}."""
    if not value or not value.get("utc"):
        return None
    return datetime.fromisoformat(value["utc"].replace("Z", "+00:00"))


def _iso(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def month_starts(start: date, end: date) -> list[date]:
    """First day of every month from start's month up to and including end's month."""
    months, current = [], date(start.year, start.month, 1)
    while current <= end:
        months.append(current)
        current = next_month(current)
    return months


def is_finished(month: date, now: datetime) -> bool:
    month_end = datetime.combine(next_month(month), datetime.min.time(), tzinfo=timezone.utc)
    return now - month_end >= timedelta(days=SETTLE_DAYS)


def licence_text(location: dict) -> str:
    names = sorted({lic.get("name", "") for lic in location.get("licenses") or [] if lic.get("name")})
    return "; ".join(names) if names else "UNKNOWN - check before use"


# --- Step 1 and 2: find PM2.5 sensors ------------------------------------------

def fetch_locations(client: ApiClient, city_id: str, city: dict, today: date) -> list[dict]:
    """Return all PM2.5 locations near a city, saving the raw response(s).

    If today's file already exists we reuse it, so an hourly run asks for the
    location list only once a day.
    """
    folder = raw_dir() / "locations"
    folder.mkdir(parents=True, exist_ok=True)
    locations, page = [], 1

    while True:
        path = folder / f"{city_id}_{today:%Y%m%d}_p{page}.json"
        if path.exists():
            body = json.loads(path.read_text(encoding="utf-8"))
        else:
            params = {
                "coordinates": f"{city['lat']},{city['lon']}",
                "radius": city["radius_m"],
                "limit": 1000,
                "page": page,
            }
            resp = client.get("locations", params)
            path.write_bytes(resp.content)
            manifest.record(path, "openaq", resp.url, licence="OpenAQ API metadata")
            body = resp.data

        results = body.get("results", [])
        locations.extend(results)
        if len(results) < 1000:
            return locations
        page += 1


def pm25_sensors(locations: Iterable[dict], city_id: str) -> list[Sensor]:
    sensors = []
    for loc in locations:
        if loc.get("isMobile"):
            continue   # moving sensors cannot represent one place
        for s in loc.get("sensors") or []:
            if (s.get("parameter") or {}).get("name") == PM25:
                sensors.append(Sensor(
                    sensor_id=s["id"],
                    location_id=loc["id"],
                    location_name=loc.get("name") or str(loc["id"]),
                    city_id=city_id,
                    licence=licence_text(loc),
                    first=_parse_utc(loc.get("datetimeFirst")),
                    last=_parse_utc(loc.get("datetimeLast")),
                ))
    return sensors


# --- Step 3 and 4: download hourly data -----------------------------------------

def months_for(sensor: Sensor, history_start: date, now: datetime) -> list[date]:
    start = max(history_start, sensor.first.date()) if sensor.first else history_start
    end = min(now, sensor.last).date() if sensor.last else now.date()
    return month_starts(start, end) if start <= end else []


def month_path(sensor: Sensor, month: date) -> Path:
    return raw_dir() / "hours" / f"sensor_{sensor.sensor_id}" / f"{month:%Y-%m}.json"


def week_parts(sensor: Sensor, month: date) -> list[tuple[Path, date, date]]:
    """The month cut into pieces of at most 7 days: (file, first day, day after last)."""
    parts, start, end = [], month, next_month(month)
    while start < end:
        stop = min(start + timedelta(days=7), end)
        name = f"{month:%Y-%m}_part{len(parts) + 1}.json"
        parts.append((month_path(sensor, month).with_name(name), start, stop))
        start = stop
    return parts


def already_complete(sensor: Sensor, month: date, now: datetime) -> bool:
    """A finished month we already have, either as one file or as all its weekly parts."""
    if not is_finished(month, now):
        return False
    return month_path(sensor, month).exists() or all(p.exists() for p, _, _ in week_parts(sensor, month))


def _download(client: ApiClient, sensor: Sensor, path: Path, start: date, stop: date,
              max_retries: int = 3) -> None:
    params = {"datetime_from": _iso(start), "datetime_to": _iso(stop), "limit": 1000, "page": 1}
    resp = client.get(f"sensors/{sensor.sensor_id}/hours", params, max_retries=max_retries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    manifest.record(path, "openaq", resp.url, licence=sensor.licence)


def fetch_sensor_month(client: ApiClient, sensor: Sensor, month: date, now: datetime) -> str:
    """Download one sensor-month. Returns 'skipped', 'new', 'refreshed' or 'split'.

    If OpenAQ's server fails on the whole month (HTTP 5xx), the month is
    downloaded again in 7-day parts, which are lighter queries ('split').
    """
    if already_complete(sensor, month, now):
        return "skipped"

    path = month_path(sensor, month)
    existed = path.exists()
    try:
        # Only 2 tries for the whole month: if OpenAQ's server keeps failing,
        # switching to lighter weekly requests is faster than waiting longer.
        _download(client, sensor, path, month, next_month(month), max_retries=2)
        return "refreshed" if existed else "new"
    except ApiError as error:
        if not error.is_server_error:
            raise

    finished = is_finished(month, now)
    for part_path, start, stop in week_parts(sensor, month):
        if finished and part_path.exists():
            continue   # this part was already saved on an earlier run
        _download(client, sensor, part_path, start, stop)
    return "split"


# --- Run everything ---------------------------------------------------------------

def run(
    city_ids: Optional[list[str]] = None,
    max_locations: Optional[int] = None,
    client: Optional[ApiClient] = None,
    now: Optional[datetime] = None,
    retry_gaps: bool = False,
) -> str:
    """Fetch everything and return a one-line summary for `make data`."""
    now = now or datetime.now(timezone.utc)
    client = client or make_client()
    all_cities = config.cities()
    city_ids = city_ids or list(all_cities)
    history_start = date.fromisoformat(config.load_yaml("cities")["history_start"])

    # Steps 1-2: which sensors exist?
    sensors: list[Sensor] = []
    seen: set[int] = set()
    for city_id in city_ids:
        found = pm25_sensors(fetch_locations(client, city_id, all_cities[city_id], now.date()), city_id)
        found = [s for s in found if s.sensor_id not in seen]   # never count a sensor twice
        seen.update(s.sensor_id for s in found)
        if max_locations:
            found = found[:max_locations]
        print(f"    {all_cities[city_id]['name']}: {len(found)} PM2.5 sensor(s)")
        sensors.extend(found)

    # Steps 3-4: download each sensor-month.
    jobs = [(s, m) for s in sensors for m in months_for(s, history_start, now)]
    to_download = sum(1 for s, m in jobs if not already_complete(s, m, now))
    print(f"    {len(jobs)} sensor-months in total, {to_download} to download "
          f"(about {to_download * client.min_interval_s / 60:.0f} min at the API speed limit)")

    gaps = load_gaps()
    counts = {"new": 0, "refreshed": 0, "split": 0, "skipped": 0, "failed": 0, "known_gap": 0}
    done = 0
    for sensor, month in jobs:
        key = (sensor.sensor_id, f"{month:%Y-%m}")
        if not retry_gaps and is_finished(month, now) and gaps.get(key, {}).get("failures", 0) >= GIVE_UP_AFTER:
            counts["known_gap"] += 1   # OpenAQ already failed on this finished month; don't waste time
        else:
            try:
                counts[fetch_sensor_month(client, sensor, month, now)] += 1
                gaps.pop(key, None)    # it worked this time, so it is no longer a gap
            except ApiError as error:
                if "API key was refused" in str(error):
                    raise   # no point continuing with a bad key
                counts["failed"] += 1
                entry = gaps.setdefault(key, {"failures": 0, "city_id": sensor.city_id,
                                              "location_name": sensor.location_name})
                entry["failures"] += 1
                entry["last_error"] = str(error)[:200]
                entry["last_tried_utc"] = now.isoformat(timespec="seconds")
                print(f"    WARNING sensor {sensor.sensor_id} {month:%Y-%m}: {error}")
        done += 1
        if done % 50 == 0:
            print(f"    ... {done}/{len(jobs)} sensor-months processed")
    save_gaps(gaps)

    if counts["failed"] and counts["failed"] == len(jobs):
        raise ApiError("every download failed; see the warnings above")

    return (f"{len(sensors)} sensors, {counts['new']} new + {counts['refreshed']} refreshed month files, "
            f"{counts['split']} months fetched in weekly parts, "
            f"{counts['skipped']} already complete, {counts['failed']} failed, "
            f"{counts['known_gap']} skipped as known gaps (see {GAPS_FILE_NAME}), {client.calls} API calls")


# --- Known gaps: months OpenAQ's server could not deliver -----------------------
# Kept in data/logs/ (committed to Git) so the gaps are visible and explained,
# not hidden. Run `python -m smog fetch-openaq --retry-gaps` to try them again.

GIVE_UP_AFTER = 1   # one failed run already includes the monthly AND the weekly retries
GAPS_FILE_NAME = "data/logs/openaq_known_gaps.csv"
GAP_COLUMNS = ["sensor_id", "month", "city_id", "location_name", "failures", "last_tried_utc", "last_error"]


def _gaps_path() -> Path:
    return config.ROOT / GAPS_FILE_NAME


def load_gaps() -> dict:
    path = _gaps_path()
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(int(r["sensor_id"]), r["month"]): {**r, "failures": int(r["failures"])}
                for r in csv.DictReader(f)}


def save_gaps(gaps: dict) -> None:
    path = _gaps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=GAP_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for (sensor_id, month), entry in sorted(gaps.items()):
            writer.writerow({**entry, "sensor_id": sensor_id, "month": month})
