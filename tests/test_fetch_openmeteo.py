"""Tests for the Open-Meteo fetcher, using a fake API (no internet needed)."""
import json
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from smog import config, fetch_openmeteo, manifest
from smog.api import ApiClient

NOW = datetime(2026, 10, 19, 14, 5, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = {}
        self._body = body

    def json(self):
        return self._body


class FakeOpenMeteo:
    """Answers like Open-Meteo. Can pretend boundary_layer_height does not exist."""

    def __init__(self, reject_blh=False):
        self.reject_blh = reject_blh
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        hourly = (params or {}).get("hourly", "")
        if self.reject_blh and "boundary_layer_height" in hourly:
            return FakeResponse(400, {"error": True, "reason":
                                      "Cannot initialize WeatherVariable from invalid String value boundary_layer_height"})
        return FakeResponse(200, {"hourly": {"time": ["2026-10-01T00:00"], hourly.split(",")[0]: [1.0]}})


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "data" / "manifest.csv")
    real_load_yaml = config.load_yaml
    monkeypatch.setattr(config, "load_yaml", lambda name: (
        {**real_load_yaml(name), "history_start": "2026-08-01"} if name == "cities" else real_load_yaml(name)))
    monkeypatch.setattr(config, "cities", lambda: {"lahore": {"name": "Lahore", "lat": 31.52, "lon": 74.36}})
    return tmp_path


def fake_clients(session):
    return {name: ApiClient(url, min_interval_s=0, session=session, sleep=lambda s: None)
            for name, url in (("archive", fetch_openmeteo.ARCHIVE_URL),
                              ("forecast", fetch_openmeteo.FORECAST_URL),
                              ("air", fetch_openmeteo.AIR_URL))}


def test_month_settles_a_week_after_it_ends():
    assert fetch_openmeteo.is_settled(date(2026, 9, 1), date(2026, 10, 8))
    assert not fetch_openmeteo.is_settled(date(2026, 9, 1), date(2026, 10, 6))


def test_first_run_gets_months_and_forecast_snapshots(sandbox):
    session = FakeOpenMeteo()
    summary = fetch_openmeteo.run(clients=fake_clients(session), now=NOW)

    # Aug, Sep, Oct for weather and for air = 6 month files; 2 forecast snapshots
    assert "6 new + 0 refreshed" in summary and "2 forecast snapshots" in summary
    raw = config.RAW_DIR
    assert (raw / "openmeteo_weather/lahore/2026-09.json").exists()
    assert (raw / "openmeteo_air/lahore/2026-10.json").exists()
    assert (raw / "openmeteo_forecast/lahore/weather_20261019T14.json").exists()
    assert manifest.verify().ok

    # The current month only asks up to today, never for future dates.
    october = [p for u, p in session.calls if p.get("start_date") == "2026-10-01"]
    assert all(p["end_date"] == "2026-10-19" for p in october)
    # Times are requested in UTC (GMT), matching our rule of storing UTC.
    assert all(p["timezone"] == "GMT" for _, p in session.calls)


def test_second_run_same_hour_refreshes_only_unsettled_months(sandbox):
    fetch_openmeteo.run(clients=fake_clients(FakeOpenMeteo()), now=NOW)
    summary = fetch_openmeteo.run(clients=fake_clients(FakeOpenMeteo()), now=NOW)
    # Aug is settled; Sep ended only 18 days ago... it is settled too (>= 7 days); Oct is not.
    assert "0 new + 2 refreshed" in summary and "4 already complete" in summary
    assert "0 forecast snapshots" in summary      # this hour's snapshot already exists


def test_unknown_optional_variable_is_dropped_once(sandbox):
    session = FakeOpenMeteo(reject_blh=True)
    summary = fetch_openmeteo.run(clients=fake_clients(session), now=NOW)
    assert "0 failed" in summary
    archive_calls = [p for u, p in session.calls if u.endswith("/archive")]
    with_blh = [p for p in archive_calls if "boundary_layer_height" in p["hourly"]]
    assert len(with_blh) == 1   # tried once, then remembered it is not offered
