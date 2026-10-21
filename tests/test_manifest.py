"""Tests for manifest.csv: fingerprints must catch any change to raw files."""
from smog import config, manifest


def _setup(tmp_path, monkeypatch):
    """Point the manifest at a temporary folder so tests never touch real data."""
    monkeypatch.setattr(config, "ROOT", tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    return raw, tmp_path / "data" / "manifest.csv"


def test_record_then_verify_passes(tmp_path, monkeypatch):
    raw, man = _setup(tmp_path, monkeypatch)
    f = raw / "sample.csv"
    f.write_text("time,pm25\n2026-10-19T00:00Z,120\n")

    row = manifest.record(f, "openaq", "GET /v3/sensors/1/hours", manifest_path=man)

    assert row["file_path"] == "data/raw/sample.csv"
    assert row["licence"]  # filled from sources.yaml
    assert manifest.verify(man, raw).ok


def test_verify_catches_edited_file(tmp_path, monkeypatch):
    raw, man = _setup(tmp_path, monkeypatch)
    f = raw / "sample.csv"
    f.write_text("time,pm25\n2026-10-19T00:00Z,120\n")
    manifest.record(f, "openaq", "q", manifest_path=man)

    f.write_text("time,pm25\n2026-10-19T00:00Z,999\n")  # someone edits raw data

    result = manifest.verify(man, raw)
    assert not result.ok
    assert result.changed == ["data/raw/sample.csv"]


def test_verify_catches_missing_and_unlisted(tmp_path, monkeypatch):
    raw, man = _setup(tmp_path, monkeypatch)
    listed = raw / "listed.csv"
    listed.write_text("a\n1\n")
    manifest.record(listed, "firms", "q", manifest_path=man)
    listed.unlink()                              # listed but now missing
    (raw / "stray.csv").write_text("b\n2\n")     # on disk but never recorded

    result = manifest.verify(man, raw)
    assert result.missing == ["data/raw/listed.csv"]
    assert result.unlisted == ["data/raw/stray.csv"]
    assert not result.ok                         # an unrecorded file on disk always fails


def test_files_listed_elsewhere_but_not_on_this_machine_do_not_fail(tmp_path, monkeypatch):
    # A fresh clone gets the committed manifest, which also lists hourly forecast snapshots saved on the
    # original machine. They can never be downloaded again; that must not stop `make data`.
    raw, man = _setup(tmp_path, monkeypatch)
    snap = raw / "snapshot.json"
    snap.write_text("{}")
    manifest.record(snap, "openmeteo_weather", "q", manifest_path=man)
    snap.unlink()
    here = raw / "here.csv"
    here.write_text("a\n1\n")
    manifest.record(here, "firms", "q", manifest_path=man)
    result = manifest.verify(man, raw)
    assert result.ok and result.missing == ["data/raw/snapshot.json"]
    here.write_text("a\n2\n")                  # but an edited file on disk still fails
    assert not manifest.verify(man, raw).ok


def test_recording_same_file_twice_keeps_one_row(tmp_path, monkeypatch):
    raw, man = _setup(tmp_path, monkeypatch)
    f = raw / "sample.csv"
    f.write_text("x\n1\n")
    manifest.record(f, "openaq", "q1", manifest_path=man)
    manifest.record(f, "openaq", "q2", manifest_path=man)

    rows = manifest.load(man)
    assert len(rows) == 1 and rows[0]["query"] == "q2"
