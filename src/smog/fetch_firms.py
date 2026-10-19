"""Download NASA FIRMS fire detections into data/raw/firms/.

How it works, step by step:
1. Ask FIRMS which dates each satellite product covers ("data availability").
   Save that answer as a raw file (one per day).
2. Split the time from history_start until today into 5-day windows
   (5 days is the most FIRMS allows in one request).
3. For each satellite and window, pick the best product:
   - SP  ("standard processing"): final, best-quality data, but it arrives
     a few months late.
   - NRT ("near real-time"): available within hours, used for recent days.
   If a window crosses the line where SP ends, it is split in two.
4. Download each window as CSV for our map box, save it exactly as received,
   and record it in manifest.csv (with the key hidden).

Finished windows (ended more than 2 days ago) are downloaded once and never
again. Recent windows are downloaded again each run, because new satellite
passes keep arriving.

When SP later becomes available for days we first took from NRT, both files
are kept. The cleaning step uses SP and drops the NRT copy of those days.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from smog import config, manifest
from smog.api import ApiClient, ApiError

BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"
MAX_DAYS = 5          # FIRMS allows 1-5 days per area request
SETTLE_DAYS = 2

# Each satellite family has a final (SP) and a fast (NRT) product.
FAMILIES = {
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
}

Availability = dict[str, tuple[date, date]]   # product -> (first day, last day)


@dataclass(frozen=True)
class Window:
    product: str
    first: date
    days: int

    @property
    def last(self) -> date:
        return self.first + timedelta(days=self.days - 1)


# --- Helpers -------------------------------------------------------------------

def raw_dir() -> Path:
    return config.RAW_DIR / "firms"


def make_client() -> tuple[ApiClient, str]:
    key = config.get_key("FIRMS_MAP_KEY")
    return ApiClient(BASE_URL, min_interval_s=0.5, secrets=[key]), key


def bbox_text() -> str:
    b = config.load_yaml("cities")["firms_bbox"]
    return f"{b['west']:g},{b['south']:g},{b['east']:g},{b['north']:g}"


def _check_csv(client: ApiClient, content: bytes, must_contain: str) -> str:
    """FIRMS sometimes answers an error as plain text with status 200. Catch that."""
    text = content.decode("utf-8", errors="replace")
    header = text.splitlines()[0] if text.strip() else ""
    if must_contain not in header:
        raise ApiError(f"FIRMS did not return the expected CSV: {client.hide(text[:200])!r}")
    return text


# --- Step 1: which dates does each product cover? -----------------------------

def fetch_availability(client: ApiClient, key: str, today: date) -> Availability:
    folder = raw_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"availability_{today:%Y%m%d}.csv"

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        resp = client.get(f"data_availability/csv/{key}/ALL", as_json=False)
        text = _check_csv(client, resp.content, "data_id")
        path.write_bytes(resp.content)
        manifest.record(path, "firms", resp.url, licence="NASA FIRMS metadata")

    availability: Availability = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            availability[row["data_id"].strip()] = (
                date.fromisoformat(row["min_date"].strip()),
                date.fromisoformat(row["max_date"].strip()),
            )
        except (KeyError, ValueError):
            continue   # a product with no dates, or an unexpected row
    return availability


# --- Steps 2 and 3: plan the windows ------------------------------------------

def product_for_day(family: str, day: date, availability: Availability) -> Optional[str]:
    """Prefer the final SP product; fall back to NRT; None if neither has this day."""
    for product in FAMILIES[family]:
        span = availability.get(product)
        if span and span[0] <= day <= span[1]:
            return product
    return None


def plan_windows(family: str, start: date, end: date, availability: Availability) -> list[Window]:
    """Fixed 5-day grid from `start`, split wherever the best product changes."""
    windows: list[Window] = []
    grid_start = start
    while grid_start <= end:
        grid_end = min(grid_start + timedelta(days=MAX_DAYS - 1), end)
        day, current, run_start = grid_start, None, None
        while day <= grid_end + timedelta(days=1):
            product = product_for_day(family, day, availability) if day <= grid_end else None
            if product != current:
                if current is not None:
                    windows.append(Window(current, run_start, (day - run_start).days))
                current, run_start = product, day
            day += timedelta(days=1)
        grid_start += timedelta(days=MAX_DAYS)
    return windows


def window_path(window: Window) -> Path:
    return raw_dir() / window.product / f"{window.first.isoformat()}_{window.days}d.csv"


def is_finished(window: Window, today: date) -> bool:
    return (today - window.last).days >= SETTLE_DAYS


# --- Step 4: download -----------------------------------------------------------

def fetch_window(client: ApiClient, key: str, window: Window, today: date) -> tuple[str, int]:
    """Returns ('skipped' | 'new' | 'refreshed', number of fire rows in the file)."""
    path = window_path(window)
    if path.exists() and is_finished(window, today):
        return "skipped", 0

    existed = path.exists()
    resp = client.get(
        f"area/csv/{key}/{window.product}/{bbox_text()}/{window.days}/{window.first.isoformat()}",
        as_json=False,
    )
    text = _check_csv(client, resp.content, "latitude")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    manifest.record(path, "firms", resp.url)
    rows = max(0, len(text.strip().splitlines()) - 1)
    return ("refreshed" if existed else "new"), rows


def run(client: Optional[ApiClient] = None, key: Optional[str] = None,
        now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if client is None:
        client, key = make_client()
    today = now.date()
    start = date.fromisoformat(config.load_yaml("cities")["history_start"])

    availability = fetch_availability(client, key, today)
    windows = [w for fam in FAMILIES for w in plan_windows(fam, start, today, availability)]
    if not windows:
        raise ApiError(f"FIRMS reports no data for our dates; products seen: {sorted(availability)}")

    to_download = sum(1 for w in windows if not (window_path(w).exists() and is_finished(w, today)))
    print(f"    {len(windows)} fire windows planned, {to_download} to download")

    counts = {"new": 0, "refreshed": 0, "skipped": 0, "failed": 0}
    fire_rows = 0
    for number, window in enumerate(windows, start=1):
        try:
            status, rows = fetch_window(client, key, window, today)
            counts[status] += 1
            fire_rows += rows
        except ApiError as error:
            if "Invalid" in str(error) or "refused" in str(error):
                raise   # bad key: stop now
            counts["failed"] += 1
            print(f"    WARNING {window.product} {window.first}: {error}")
        if number % 50 == 0:
            print(f"    ... {number}/{len(windows)} windows processed")

    if counts["failed"] == len(windows):
        raise ApiError("every FIRMS download failed; see the warnings above")

    return (f"{counts['new']} new + {counts['refreshed']} refreshed window files "
            f"({fire_rows} fire detections downloaded this run), {counts['skipped']} already complete, "
            f"{counts['failed']} failed, {client.calls} API calls")
