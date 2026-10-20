"""The static dashboard must only reference bundled libraries and the published files."""
import re

from smog import config


def test_page_uses_bundled_libraries_and_placeholder():
    html = (config.ROOT / "space_static/index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts and all(s.startswith("lib/") for s in scripts)
    for s in scripts:
        assert (config.ROOT / "space_static" / s).exists(), s
    assert "__DATA_REPO__" in html                     # filled in by deploy-space
    assert "sdk: static" in (config.ROOT / "space_static/README.md").read_text(encoding="utf-8")


def test_page_reads_every_file_that_publish_writes():
    from smog import publish
    html = (config.ROOT / "space_static/index.html").read_text(encoding="utf-8")
    for name in ("predictions.csv", "scorecard.csv", "freshness.csv", "quality_gates.csv", "pipeline_status.json",
                 "published.json", "reports/MODEL_CARD.md", "reports/decision_metrics.json"):
        assert name in html, name
    for name in publish.REPORT_FILES:
        if name.endswith((".csv", ".json", ".md")) and name != "validation_errors_by_city_and_season.csv":
            assert f"reports/{name}" in html, name


def test_space_metadata_is_valid_for_hugging_face():
    meta = (config.ROOT / "space_static/README.md").read_text(encoding="utf-8").split("---")[1]
    short = [line for line in meta.splitlines() if line.startswith("short_description:")][0]
    assert len(short.split(":", 1)[1].strip()) <= 60          # Hugging Face rejects longer ones


def test_missing_probability_is_never_shown_as_no_advisory():
    html = (config.ROOT / "space_static/index.html").read_text(encoding="utf-8")
    assert "Chance not computed for this run" in html and "امکان دستیاب نہیں" in html


def test_markers_follow_the_theme_colour():
    html = (config.ROOT / "space_static/index.html").read_text(encoding="utf-8")
    assert 'color: "#111827"}' not in html            # black markers vanish on the dark theme
