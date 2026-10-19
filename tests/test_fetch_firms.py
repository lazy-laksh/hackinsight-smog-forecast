"""Tests for the NASA FIRMS fetcher, using a fake API (no internet needed)."""
from datetime import date, datetime, timezone

import pytest

from smog import config, fetch_firms, manifest
from smog.api import ApiClient, ApiError
from smog.fetch_firms import Window

KEY = "abc123secretkey"
TODAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

# SP (final) data stops at 31 July 2026; NRT covers August onwards.
AVAILABILITY_CSV = (
    "data_id,min_date,max_date\n"
    "VIIRS_SNPP_SP,2012-01-20,2026-07-31\n"
    "VIIRS_SNPP_NRT,2026-08-01,2026-10-19\n"
    "VIIRS_NOAA20_SP,2018-04-01,2026-07-31\n"
    "VIIRS_NOAA20_NRT,2026-08-01,2026-10-19\n"
    "MODIS_SP,2000-11-01,2026-07-31\n"
    "MODIS_NRT,2026-08-01,2026-10-19\n"
)
AVAIL = {
    "VIIRS_SNPP_SP": (date(2012, 1, 20), date(2026, 7, 31)),
    "VIIRS_SNPP_NRT": (date(2026, 8, 1), date(2026, 10, 19)),
}
FIRE_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
    "confidence,version,bright_ti5,frp,daynight\n"
    "30.95,74.01,330.1,0.4,0.37,2026-10-15,0812,N,VIIRS,n,2.0NRT,295.2,6.3,D\n"
    "31.02,74.40,341.7,0.4,0.37,2026-10-15,0812,N,VIIRS,h,2.0NRT,296.0,12.8,D\n"
)


class FakeResponse:
    def __init__(self, status, text):
        self.status_code = status
        self.content = text.encode()
        self.text = text
        self.headers = {}

    def json(self):
        raise ValueError("not JSON")


class FakeFirms:
    def __init__(self, bad_key=False):
        self.urls = []
        self.bad_key = bad_key

    def get(self, url, params=None, headers=None, timeout=None):
        self.urls.append(url)
        if self.bad_key:
            return FakeResponse(200, "Invalid MAP_KEY.")
        if "/data_availability/" in url:
            return FakeResponse(200, AVAILABILITY_CSV)
        return FakeResponse(200, FIRE_CSV)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "data" / "manifest.csv")
    return tmp_path


def client_for(session):
    return ApiClient(fetch_firms.BASE_URL, min_interval_s=0, session=session,
                     sleep=lambda s: None, secrets=[KEY])


# --- Planning -------------------------------------------------------------------

def test_prefers_final_sp_data_then_nrt():
    assert fetch_firms.product_for_day("VIIRS_SNPP", date(2026, 7, 31), AVAIL) == "VIIRS_SNPP_SP"
    assert fetch_firms.product_for_day("VIIRS_SNPP", date(2026, 8, 1), AVAIL) == "VIIRS_SNPP_NRT"
    assert fetch_firms.product_for_day("VIIRS_SNPP", date(2026, 10, 25), AVAIL) is None


def test_windows_are_at_most_5_days_and_split_at_sp_nrt_boundary():
    windows = fetch_firms.plan_windows("VIIRS_SNPP", date(2026, 7, 29), date(2026, 8, 6), AVAIL)
    assert windows == [
        Window("VIIRS_SNPP_SP", date(2026, 7, 29), 3),    # 29, 30, 31 July
        Window("VIIRS_SNPP_NRT", date(2026, 8, 1), 2),    # 1, 2 Aug (end of first 5-day block)
        Window("VIIRS_SNPP_NRT", date(2026, 8, 3), 4),    # 3-6 Aug
    ]
    assert all(w.days <= 5 for w in windows)


def test_windows_cover_every_day_exactly_once():
    windows = fetch_firms.plan_windows("VIIRS_SNPP", date(2025, 6, 1), TODAY, AVAIL)
    covered = [w.first.toordinal() + i for w in windows for i in range(w.days)]
    assert len(covered) == len(set(covered)) == (TODAY - date(2025, 6, 1)).days + 1


# --- Full run against the fake API -----------------------------------------------

def test_key_never_reaches_manifest_or_files(sandbox, monkeypatch):
    monkeypatch.setattr(fetch_firms, "FAMILIES", {"VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT")})
    session = FakeFirms()
    fetch_firms.run(client=client_for(session), key=KEY, now=NOW)

    assert KEY in session.urls[0]                       # the key IS sent to NASA
    manifest_text = config.MANIFEST_PATH.read_text()
    assert KEY not in manifest_text and "***" in manifest_text
    assert manifest.verify().ok


def test_rerun_only_refreshes_recent_windows(sandbox, monkeypatch):
    monkeypatch.setattr(fetch_firms, "FAMILIES", {"VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT")})
    fetch_firms.run(client=client_for(FakeFirms()), key=KEY, now=NOW)

    session = FakeFirms()
    summary = fetch_firms.run(client=client_for(session), key=KEY, now=NOW)

    # Only the windows ending in the last 2 days (14-18 Oct and 19 Oct) are refetched.
    # The availability list is reused from today's file, so no call for it.
    assert "0 new + 2 refreshed" in summary
    assert len(session.urls) == 2
    assert all("2026-10-14" in u or "2026-10-19" in u for u in session.urls)


def test_invalid_key_message_stops_the_run_without_leaking_key(sandbox):
    with pytest.raises(ApiError) as caught:
        fetch_firms.run(client=client_for(FakeFirms(bad_key=True)), key=KEY, now=NOW)
    assert "Invalid MAP_KEY" in str(caught.value)
    assert KEY not in str(caught.value)
