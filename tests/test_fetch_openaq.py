"""Tests for the OpenAQ fetcher, using a fake API (no internet needed).

The fake answers look like OpenAQ v3 responses, so these tests check our
logic: which months we download, which we skip, and what goes in the manifest.
"""
import json
from datetime import date, datetime, timezone

import pytest

from smog import config, fetch_openaq, manifest
from smog.api import ApiClient, ApiError

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

LOCATIONS = {
    "results": [
        {   # a fixed sensor that measures PM2.5 and humidity
            "id": 101, "name": "Gulberg III", "isMobile": False,
            "licenses": [{"id": 41, "name": "CC BY 4.0"}],
            "datetimeFirst": {"utc": "2026-08-15T00:00:00Z"},
            "datetimeLast": {"utc": "2026-10-19T11:00:00Z"},
            "sensors": [
                {"id": 9001, "parameter": {"id": 2, "name": "pm25", "units": "µg/m³"}},
                {"id": 9002, "parameter": {"id": 98, "name": "relativehumidity", "units": "%"}},
            ],
        },
        {   # a mobile sensor: must be ignored
            "id": 102, "name": "Moving van", "isMobile": True, "licenses": [],
            "sensors": [{"id": 9003, "parameter": {"name": "pm25"}}],
        },
        {   # no PM2.5 at all: must be ignored
            "id": 103, "name": "Temp only", "isMobile": False, "licenses": [],
            "sensors": [{"id": 9004, "parameter": {"name": "temperature"}}],
        },
    ]
}


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = headers or {}
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    """Answers like OpenAQ. Can be told to return 429 once, to test waiting."""

    def __init__(self, fail_first_with=None):
        self.requests = []
        self.fail_first_with = fail_first_with

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append((url, dict(params or {}), dict(headers or {})))
        if self.fail_first_with:
            status, self.fail_first_with = self.fail_first_with, None
            return FakeResponse(status, {"detail": "slow down"}, {"x-ratelimit-reset": "7"})
        if url.endswith("/locations"):
            return FakeResponse(200, LOCATIONS)
        sensor_id = url.split("/sensors/")[1].split("/")[0]
        hour = {"value": 120.5, "parameter": {"name": "pm25"},
                "period": {"datetimeFrom": {"utc": params["datetime_from"]}}}
        return FakeResponse(200, {"meta": {"found": 1}, "results": [hour], "sensor": sensor_id})


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Send all data writes to a temporary folder."""
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "data" / "manifest.csv")
    return tmp_path


def make_client(session):
    sleeps = []
    client = ApiClient("https://api.openaq.org/v3", headers={"X-API-Key": "test"},
                       session=session, sleep=sleeps.append)
    return client, sleeps


# --- Pure logic ------------------------------------------------------------------

def test_month_starts_crosses_year_end():
    assert fetch_openaq.month_starts(date(2025, 11, 20), date(2026, 1, 3)) == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]


def test_month_is_finished_only_after_settle_days():
    assert fetch_openaq.is_finished(date(2026, 9, 1), NOW)            # Sept ended 18 days ago
    assert not fetch_openaq.is_finished(date(2026, 10, 1), NOW)       # current month
    assert not fetch_openaq.is_finished(date(2026, 9, 1), datetime(2026, 10, 1, 12, tzinfo=timezone.utc))


def test_only_fixed_pm25_sensors_are_kept():
    sensors = fetch_openaq.pm25_sensors(LOCATIONS["results"], "lahore")
    assert [s.sensor_id for s in sensors] == [9001]
    assert sensors[0].licence == "CC BY 4.0"
    assert sensors[0].first.date() == date(2026, 8, 15)


def test_months_start_at_first_measurement_not_before():
    sensor = fetch_openaq.pm25_sensors(LOCATIONS["results"], "lahore")[0]
    months = fetch_openaq.months_for(sensor, date(2025, 6, 1), NOW)
    assert months == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]


# --- Full run against the fake API --------------------------------------------------

def test_first_run_downloads_and_records_everything(sandbox):
    session = FakeSession()
    client, _ = make_client(session)

    summary = fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)

    assert "1 sensors, 3 new" in summary
    rows = {r["file_path"]: r for r in manifest.load()}
    month_file = "data/raw/openaq/hours/sensor_9001/2026-09.json"
    assert month_file in rows
    assert rows[month_file]["licence"] == "CC BY 4.0"
    assert "datetime_from=2026-09-01T00%3A00%3A00Z" in rows[month_file]["query"]
    assert "test" not in rows[month_file]["query"]          # the API key never reaches the manifest
    assert session.requests[0][2]["X-API-Key"] == "test"    # but it is sent as a header
    assert manifest.verify().ok


def test_second_run_skips_finished_months_and_refreshes_current(sandbox):
    client, _ = make_client(FakeSession())
    fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)

    session = FakeSession()
    client, _ = make_client(session)
    summary = fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)

    assert "0 new + 1 refreshed" in summary and "2 already complete" in summary
    # locations list is reused from today's file, so only 1 call: the current month
    assert len(session.requests) == 1
    assert "2026-10-01" in session.requests[0][1]["datetime_from"]
    assert manifest.verify().ok


def test_rate_limit_waits_then_succeeds(sandbox):
    client, sleeps = make_client(FakeSession(fail_first_with=429))
    fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)
    assert 7.0 in sleeps          # waited for the time the API asked for


class MonthTooHeavySession(FakeSession):
    """Like OpenAQ for the heavy station: a whole month gives HTTP 500, a week works."""

    def get(self, url, params=None, headers=None, timeout=None):
        if params and "datetime_from" in params:
            start = datetime.fromisoformat(params["datetime_from"].replace("Z", "+00:00"))
            stop = datetime.fromisoformat(params["datetime_to"].replace("Z", "+00:00"))
            if (stop - start).days > 7:
                self.requests.append((url, dict(params), dict(headers or {})))
                return FakeResponse(500, {"detail": "Internal Server Error"})
        return super().get(url, params, headers, timeout)


def test_server_error_on_month_falls_back_to_weekly_parts(sandbox):
    client, _ = make_client(MonthTooHeavySession())
    summary = fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)

    assert "3 months fetched in weekly parts" in summary and "0 failed" in summary
    month_tries = [r for r in client.session.requests if r[1].get("datetime_from") == "2026-09-01T00:00:00Z"
                   and r[1].get("datetime_to") == "2026-10-01T00:00:00Z"]
    assert len(month_tries) == 2      # gave up on the whole month after 2 tries, not 5
    sept_parts = sorted(p.name for p in (config.RAW_DIR / "openaq/hours/sensor_9001").glob("2026-09_part*.json"))
    assert sept_parts == [f"2026-09_part{i}.json" for i in range(1, 6)]   # 7+7+7+7+2 days
    assert manifest.verify().ok

    # Next run: finished months are complete from their parts, so they are skipped.
    session = MonthTooHeavySession()
    client, _ = make_client(session)
    summary = fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)
    assert "2 already complete" in summary


class AlwaysBrokenSession(FakeSession):
    """OpenAQ's server fails on every hourly request for this sensor, even weekly ones."""

    def get(self, url, params=None, headers=None, timeout=None):
        if "/hours" in url:
            self.requests.append((url, dict(params or {}), dict(headers or {})))
            return FakeResponse(500, {"detail": "Internal Server Error"})
        return super().get(url, params, headers, timeout)


def test_failed_finished_months_are_logged_and_skipped_next_time(sandbox):
    client, _ = make_client(AlwaysBrokenSession())
    with pytest.raises(ApiError, match="every download failed"):   # all 3 failed -> the run stops...
        fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)

    gaps = fetch_openaq.load_gaps()                                 # ...but the gaps were saved first
    assert set(gaps) == {(9001, "2026-08"), (9001, "2026-09"), (9001, "2026-10")}
    assert gaps[(9001, "2026-09")]["location_name"] == "Gulberg III"

    # Next run: Aug and Sep are finished known gaps -> skipped without any call.
    # October is the current month, so it is still tried.
    session = AlwaysBrokenSession()
    client, _ = make_client(session)
    summary = fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)
    assert "2 skipped as known gaps" in summary and "1 failed" in summary
    assert all("2026-10" in r[1]["datetime_from"] for r in session.requests)

    # --retry-gaps tries them all again.
    session = AlwaysBrokenSession()
    client, _ = make_client(session)
    with pytest.raises(ApiError):
        fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW, retry_gaps=True)
    assert any("2026-08" in r[1]["datetime_from"] for r in session.requests)
    assert fetch_openaq.load_gaps()[(9001, "2026-08")]["failures"] == 2


def test_week_parts_cover_the_whole_month_without_overlap():
    sensor = fetch_openaq.pm25_sensors(LOCATIONS["results"], "lahore")[0]
    parts = fetch_openaq.week_parts(sensor, date(2026, 2, 1))
    assert parts[0][1] == date(2026, 2, 1) and parts[-1][2] == date(2026, 3, 1)
    assert all(a[2] == b[1] for a, b in zip(parts, parts[1:]))   # each part starts where the last ended


def test_bad_key_stops_with_clear_message(sandbox):
    client, _ = make_client(FakeSession(fail_first_with=401))
    with pytest.raises(ApiError, match="Check the key"):
        fetch_openaq.run(city_ids=["lahore"], client=client, now=NOW)
