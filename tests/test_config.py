"""Tests for the settings files."""
import pytest

from smog import config


def test_four_cities_with_valid_coordinates():
    cities = config.cities()
    assert set(cities) == {"lahore", "islamabad_rawalpindi", "karachi", "peshawar"}
    for city in cities.values():
        assert 23 < city["lat"] < 37 and 60 < city["lon"] < 78   # inside Pakistan
        assert city["radius_m"] <= 25000                          # OpenAQ limit


def test_every_source_has_a_licence():
    for source_id in ("openaq", "firms", "openmeteo_weather", "openmeteo_air"):
        info = config.source_info(source_id)
        assert info["licence"] and info["publisher"] and info["url"]


def test_missing_key_gives_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)   # no .env here
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    with pytest.raises(config.MissingKeyError):
        config.get_key("FIRMS_MAP_KEY")
