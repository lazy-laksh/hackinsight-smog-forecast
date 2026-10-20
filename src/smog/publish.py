"""Publish a small, fresh copy of the results for the public dashboard.

Every hourly run ends with `python -m smog publish`:
  1. export: write small CSV/JSON files to data/publish/ (latest forecasts, recent gold rows,
     data freshness, quality gate results, run status, model and decision reports);
  2. upload: push that folder to a public Hugging Face *dataset* repo. The dashboard (a
     Hugging Face Space) reads these files, so it always shows what the Dell last produced.

Nothing secret is published: no API keys, no raw files, only gold-level results and logs.
If HF_TOKEN is not set, the export still happens and the upload is reported as SKIPPED
(the hourly run does not fail because of the dashboard).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pandas as pd

from smog import config, warehouse

PUBLISH_DIR_NAME = "data/publish"
DEFAULT_REPO_NAME = "smog-forecast-live"
REPORT_FILES = ("model_metrics.json", "decision_metrics.json", "test_errors_by_city.csv",
                "validation_errors_by_city_and_season.csv", "test_predictions.csv",
                "validation_predictions.csv", "MODEL_CARD.md")
EVENING_ISSUE_HOUR_PKT = 20      # the "official" forecast is the one issued at 20:05 PKT (as in training)


def publish_dir():
    return config.ROOT / PUBLISH_DIR_NAME


def _query(con, sql: str, *params) -> pd.DataFrame:
    try:
        return con.execute(sql, list(params)).df()
    except Exception as error:        # a missing table (e.g. before the first forecast) is not fatal
        return pd.DataFrame({"error": [str(error).splitlines()[0]]})


def scorecard(pred: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Live report card: the evening forecast for each day next to what really happened.

    For each city and target day we take the forecast issued closest to (but not after) 20:59 PKT
    the evening before, because that is what the model was trained for and what an officer would
    act on. If no evening run exists yet (the very first day), we take the latest forecast issued
    the day before, and say which one we used.
    """
    if pred.empty or "issued_utc" not in pred:
        return pd.DataFrame()
    p = pred.copy()
    p["issued_pkt"] = pd.to_datetime(p["issued_utc"], utc=True).dt.tz_convert("Asia/Karachi").dt.tz_localize(None)
    p["target_day_pkt"] = pd.to_datetime(p["target_day_pkt"])
    p = p[p["issued_pkt"] < p["target_day_pkt"]]                         # issued the day before or earlier
    cutoff = p["target_day_pkt"] - pd.Timedelta(days=1) + pd.Timedelta(hours=EVENING_ISSUE_HOUR_PKT, minutes=59)
    p["evening"] = p["issued_pkt"] <= cutoff
    p = (p.sort_values(["evening", "issued_pkt"])
           .groupby(["city_id", "target_day_pkt"]).tail(1))                # prefer evening, latest
    d = daily.copy()
    d["target_day_pkt"] = pd.to_datetime(d["day_pkt"])
    want = [c for c in ("city_id", "target_day_pkt", "pm25_mean", "hours_covered", "complete") if c in d.columns]
    out = p.merge(d[want],
                  on=["city_id", "target_day_pkt"], how="left")
    out["status"] = out["complete"].map({True: "final", False: "so far today"}).fillna("waiting for data")
    out["error"] = (out["pm25_pred"] - out["pm25_mean"]).round(1)
    out["persistence_error"] = (out["baseline_persistence"] - out["pm25_mean"]).round(1)
    out["actual_unhealthy"] = out["pm25_mean"] >= 55.5
    cols = ["city_id", "target_day_pkt", "issued_pkt", "evening", "pm25_pred", "pm25_p10", "pm25_p90",
            "p_unhealthy", "advisory_default", "baseline_persistence", "pm25_mean", "hours_covered", "status",
            "error", "persistence_error", "actual_unhealthy", "row_id"]
    return out[[c for c in cols if c in out.columns]].sort_values(["target_day_pkt", "city_id"], ascending=[False, True])


def export(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    folder = publish_dir()
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "reports").mkdir(parents=True)
    since = (now - timedelta(days=120)).date()
    files: dict[str, pd.DataFrame] = {}
    with warehouse.connect(read_only=True) as con:
        files["predictions.csv"] = _query(
            con, "SELECT * FROM gold.predictions WHERE issued_utc >= ? ORDER BY issued_utc, city_id",
            now - timedelta(days=10))
        files["city_daily.csv"] = _query(con, "SELECT * FROM gold.city_daily WHERE day_pkt >= ? ORDER BY day_pkt", since)
        files["city_hourly.csv"] = _query(
            con, "SELECT * FROM gold.city_hourly WHERE ts_utc >= ? ORDER BY ts_utc", now - timedelta(days=10))
        files["weather_daily.csv"] = _query(con, "SELECT * FROM gold.weather_daily WHERE day_pkt >= ? ORDER BY day_pkt", since)
        files["fires_daily.csv"] = _query(con, "SELECT * FROM gold.fires_daily WHERE day_pkt >= ? ORDER BY day_pkt", since)
        files["cams_daily.csv"] = _query(con, "SELECT * FROM gold.cams_daily WHERE day_pkt >= ? ORDER BY day_pkt", since)
        files["freshness.csv"] = _query(con, "SELECT * FROM gold.freshness")
        files["quality_gates.csv"] = _query(con, "SELECT * FROM qc.gate_results ORDER BY decided_at_utc DESC LIMIT 200")
        files["quality_decisions.csv"] = _query(
            con, "SELECT * FROM qc.decisions WHERE run_id = (SELECT max(run_id) FROM qc.decisions)")
        files["qc_status_counts.csv"] = _query(
            con, "SELECT qc_status, count(*) AS hours FROM qc.pm25_hourly GROUP BY 1 ORDER BY 2 DESC")
        files["sensors.csv"] = _query(con, "SELECT * FROM cleaned.sensors")
    if "error" not in files["predictions.csv"] and "error" not in files["city_daily.csv"]:
        files["scorecard.csv"] = scorecard(files["predictions.csv"], files["city_daily.csv"])

    counts = {}
    for name, df in files.items():
        df.to_csv(folder / name, index=False)
        counts[name] = len(df)
    for name in REPORT_FILES:
        src = config.ROOT / "reports" / name
        if src.exists():
            shutil.copy2(src, folder / "reports" / name)
    status = config.ROOT / "data/logs/pipeline_status.json"
    if status.exists():
        shutil.copy2(status, folder / "pipeline_status.json")
    manifest = config.ROOT / "data/manifest.csv"
    raw_files = sum(1 for _ in manifest.open(encoding="utf-8")) - 1 if manifest.exists() else 0
    (folder / "published.json").write_text(json.dumps({
        "published_utc": now.isoformat(timespec="seconds"), "raw_files_in_manifest": raw_files,
        "rows": counts}, indent=2), encoding="utf-8")
    (folder / "README.md").write_text(_readme(), encoding="utf-8")
    return counts


def _readme() -> str:
    lines = ["---", "license: cc-by-4.0", "pretty_name: Pakistan next-day PM2.5 forecast (live)", "---", "",
             "# Pakistan next-day PM2.5 forecast: live results", "",
             "Updated every hour by the pipeline in the project's GitHub repository. Gold-level results only "
             "(city averages, forecasts, quality logs); no raw files and no keys.", "", "## Sources and licences"]
    for name, info in (config.load_yaml("sources") or {}).get("sources", {}).items():
        if isinstance(info, dict):
            lines.append(f"- **{info.get('publisher', name)}**: {info.get('licence', '')}. {info.get('attribution', '')}")
    return "\n".join(lines) + "\n"


def upload(token: str, repo_id: str | None = None) -> str:
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    repo_id = repo_id or f"{api.whoami(cache=True)['name']}/{DEFAULT_REPO_NAME}"
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(folder_path=str(publish_dir()), repo_id=repo_id, repo_type="dataset",
                      commit_message=f"hourly update {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
                      delete_patterns=["*.csv", "reports/*"])
    return repo_id


def run() -> str:
    from dotenv import load_dotenv
    load_dotenv(config.ROOT / ".env")
    counts = export()
    summary = f"exported {len(counts)} tables ({counts.get('predictions.csv', 0)} forecast rows)"
    token = os.getenv("HF_TOKEN")
    if not token:
        return summary + "; upload SKIPPED (HF_TOKEN not set in .env)"
    repo = upload(token, os.getenv("HF_DATASET_REPO") or None)
    return summary + f"; uploaded to huggingface.co/datasets/{repo}"
