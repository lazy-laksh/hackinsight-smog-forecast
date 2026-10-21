"""ApiClient: a "200 OK" with a broken body must be retried, never saved, and never crash a whole stage."""
import pytest

from smog.api import ApiClient, ApiError


class Resp:
    def __init__(self, body: bytes, status=200):
        self.status_code, self.content, self.headers = status, body, {}
        self.text = body.decode()

    def json(self):
        import json
        return json.loads(self.content)


class Session:
    def __init__(self, bodies):
        self.bodies = list(bodies)

    def get(self, *a, **k):
        return Resp(self.bodies.pop(0))


def client(bodies):
    return ApiClient("https://example.org", session=Session(bodies), sleep=lambda s: None, min_interval_s=0)


def test_empty_200_is_retried_then_succeeds():
    c = client([b"", b"<html>gateway</html>", b'{"ok": 1}'])
    assert c.get("x").data == {"ok": 1} and c.calls == 3


def test_body_that_stays_broken_becomes_api_error():
    c = client([b""] * 5)
    with pytest.raises(ApiError, match="not valid JSON"):
        c.get("x")


def test_openmeteo_month_with_broken_body_is_counted_as_failed_not_fatal(tmp_path, monkeypatch):
    from smog import config, fetch_openmeteo
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")

    class Broken:
        calls = 0

        def get(self, *a, **k):
            raise ApiError("answer was not valid JSON", status=200)
    clients = {name: Broken() for name in ("archive", "forecast", "air")}
    with pytest.raises(ApiError, match="every Open-Meteo download failed"):     # everything broken -> stage fails
        fetch_openmeteo.run(clients)
