"""The hourly pipeline, run by Prefect.

Start it (on the computer that stays on) with:
    python -m smog.flow
It runs at 5 minutes past every hour ("5 * * * *"), as the guide suggests.

Each step calls our own command line (`python -m smog <step>`) in a fresh
process. That means:
  - the code you change during the day is used by the next run automatically,
  - one step crashing can never leave stale code or memory behind,
  - running a step by hand gives exactly the same result as the scheduler.

What happens when something fails:
  - A download fails (after 2 retries): the run continues with the data we
    already have; the Data Quality Agent's freshness check then marks that
    source as stale on the dashboard.
  - A quality gate fails: the run stops and is marked Failed; the gold layer
    and dashboard keep the last good version (see quality.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from smog import config

SCHEDULE = "5 * * * *"          # at 5 minutes past every hour (UTC; same minute in PKT)
STATUS_FILE = "data/logs/pipeline_status.json"


def smog(*args: str) -> str:
    """Run `python -m smog <args>`, send its output to the Prefect log, return its last line."""
    logger = get_run_logger()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "smog", *args], cwd=str(config.ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout.splitlines():
        logger.info(line)
    if proc.stderr.strip():
        logger.warning(proc.stderr[-3000:])
    if proc.returncode != 0:
        raise RuntimeError(f"`smog {' '.join(args)}` failed with exit code {proc.returncode}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


# --- Steps -------------------------------------------------------------------------
# Downloads retry twice (2 minutes apart): most API problems are short.

@task(name="fetch OpenAQ PM2.5", retries=2, retry_delay_seconds=120)
def fetch_openaq() -> str:
    return smog("fetch-openaq")


@task(name="fetch NASA FIRMS fires", retries=2, retry_delay_seconds=120)
def fetch_firms() -> str:
    return smog("fetch-firms")


@task(name="fetch Open-Meteo weather + CAMS", retries=2, retry_delay_seconds=120)
def fetch_openmeteo() -> str:
    return smog("fetch-openmeteo")


@task(name="verify raw files (SHA-256)")
def verify() -> str:
    return smog("verify")


@task(name="build cleaned layer")
def build_cleaned() -> str:
    return smog("build-cleaned")


@task(name="Data Quality Agent (gates 1 + 2)")
def quality() -> str:
    return smog("quality")


@task(name="build gold layer")
def build_gold() -> str:
    return smog("build-gold")


def write_status(status: str, started: datetime, steps: dict) -> None:
    """Small JSON file the dashboard reads to show the last run and the last successful run."""
    path = config.ROOT / STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {"last_run": {"status": status, "started_utc": started.isoformat(timespec="seconds"),
                           "finished_utc": now, "steps": steps}}
    record["last_success"] = record["last_run"] if status == "success" else previous.get("last_success")
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


@flow(name="smog-hourly", log_prints=True)
def hourly() -> dict:
    started = datetime.now(timezone.utc)
    steps: dict[str, str] = {}

    # 1. Downloads: a failed source does not stop the run.
    for name, step in (("openaq", fetch_openaq), ("firms", fetch_firms), ("openmeteo", fetch_openmeteo)):
        state = step(return_state=True)
        if state.is_completed():
            steps[name] = state.result()
        else:
            steps[name] = f"FAILED after retries: {state.message}"
            print(f"WARNING: {name} download failed; continuing with existing data (it will show as stale)")

    # 2. Everything after the downloads must succeed, in order.
    try:
        for name, step in (("verify", verify), ("cleaned", build_cleaned),
                           ("quality", quality), ("gold", build_gold)):
            steps[name] = step()
    except Exception as error:
        steps["error"] = str(error)
        write_status("failed", started, steps)
        raise

    write_status("success", started, steps)
    create_markdown_artifact(
        key="hourly-run-summary",
        markdown="| step | result |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in steps.items()),
        description="What each step of this hourly run did",
    )
    return steps


if __name__ == "__main__":
    # limit=1: never start a new run while the previous one is still going.
    hourly.serve(name="smog-hourly", cron=SCHEDULE, limit=1,
                 description="Hourly: fetch -> verify -> clean -> quality gates -> gold")
