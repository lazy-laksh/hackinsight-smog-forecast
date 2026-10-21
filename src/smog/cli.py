"""The commands behind `make data` and `make clean`.

`make data` runs the stages below in order. A stage whose code is not
written yet is reported as NOT BUILT YET, so the output always tells the
truth about what the pipeline really did.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from typing import Callable, Optional

from smog import clean, config, fetch_firms, fetch_openaq, fetch_openmeteo, gold, manifest, quality

# --- Stages ------------------------------------------------------------------
# Each stage is a function that returns a one-line summary, or raises an error.
# A stage set to None has not been built yet. We will fill these in one by one.


def stage_prepare_folders() -> str:
    config.ensure_folders()
    return "data/raw, data/cleaned and data/gold are ready"


def stage_verify_raw() -> str:
    result = manifest.verify()
    note = ""
    if result.missing:   # reported and listed in a log, but not a failure (see manifest.VerifyResult.ok)
        log = config.ROOT / "data/logs/manifest_missing.csv"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("file_path\n" + "\n".join(result.missing) + "\n", encoding="utf-8")
        kinds = sorted({p.split("/")[2] if p.count("/") > 2 else p for p in result.missing})
        note = (f"; {len(result.missing)} listed file(s) are not on this machine ({', '.join(kinds)}; "
                f"e.g. forecast snapshots saved elsewhere), listed in data/logs/manifest_missing.csv")
    if not result.ok:
        lines = []
        if result.changed:
            lines.append(f"{len(result.changed)} file(s) do not match their SHA-256: {result.changed[:3]}")
        if result.unlisted:
            lines.append(f"{len(result.unlisted)} raw file(s) are not in the manifest: {result.unlisted[:3]}")
        raise RuntimeError("; ".join(lines))
    present = result.checked - len(result.missing)
    return f"{present} raw file(s) on disk match their SHA-256 in manifest.csv{note}"


STAGES: list[tuple[str, Optional[Callable[[], str]]]] = [
    ("Prepare folders", stage_prepare_folders),
    ("Fetch OpenAQ sensors (raw)", fetch_openaq.run),
    ("Fetch NASA FIRMS fires (raw)", fetch_firms.run),
    ("Fetch Open-Meteo weather + CAMS (raw)", fetch_openmeteo.run),
    ("Verify raw files against manifest", stage_verify_raw),
    ("Build cleaned layer", clean.run),
    ("Data Quality Agent: gates 1 + 2 (fix / fill / flag, logged)", quality.run),
    ("Build gold layer", gold.run),
]


def run_data() -> int:
    print("=== make data ===")
    built = failed = 0
    start = time.perf_counter()

    for number, (name, func) in enumerate(STAGES, start=1):
        label = f"[{number}/{len(STAGES)}] {name}"
        if func is None:
            print(f"{label}: NOT BUILT YET")
            continue
        try:
            t0 = time.perf_counter()
            summary = func()
            built += 1
            print(f"{label}: OK ({time.perf_counter() - t0:.1f}s) - {summary}")
        except Exception as error:  # stop at the first failure, like a real pipeline
            failed += 1
            print(f"{label}: FAILED - {error}")
            break

    not_built = sum(1 for _, f in STAGES if f is None)
    print(
        f"=== done in {time.perf_counter() - start:.1f}s: "
        f"{built} stage(s) ran, {failed} failed, {not_built} not built yet ==="
    )
    return 1 if failed else 0


def run_clean() -> int:
    """Delete generated data so `make data` can be tested from an empty folder.

    manifest.csv is kept: it is the committed record of what we fetched.
    """
    for folder in (config.RAW_DIR, config.CLEANED_DIR, config.GOLD_DIR):
        if folder.exists():
            shutil.rmtree(folder)
            print(f"deleted {folder.relative_to(config.ROOT)}")
    for path in (config.WAREHOUSE_PATH, config.WAREHOUSE_PATH.with_suffix(".duckdb.wal")):
        if path.exists():
            path.unlink()
            print(f"deleted {path.relative_to(config.ROOT)}")
    return 0


def run_fetch_openaq(city: str | None, max_locations: int | None, retry_gaps: bool) -> int:
    """Download OpenAQ only (optionally a small piece of it, for testing)."""
    config.ensure_folders()
    print(fetch_openaq.run(city_ids=[city] if city else None, max_locations=max_locations,
                           retry_gaps=retry_gaps))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:                       # Windows consoles default to a code page that cannot print Urdu or µ
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="smog", description="Smog forecasting pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("data", help="run the whole pipeline (what `make data` does)")
    sub.add_parser("clean", help="delete generated data, keep manifest.csv")
    quick = sub.add_parser("fetch-openaq", help="download OpenAQ data only (for testing)")
    quick.add_argument("--city", choices=sorted(config.cities()), help="only this city")
    quick.add_argument("--max-locations", type=int, help="only the first N sensors per city")
    quick.add_argument("--retry-gaps", action="store_true",
                       help="also retry months listed in data/logs/openaq_known_gaps.csv")
    sub.add_parser("fetch-firms", help="download NASA FIRMS fires only")
    sub.add_parser("fetch-openmeteo", help="download Open-Meteo weather + CAMS only")
    sub.add_parser("build-cleaned", help="rebuild the cleaned layer from data/raw only")
    sub.add_parser("profile", help="print a read-only profile of the cleaned tables")
    sub.add_parser("quality", help="run the Data Quality Agent on the cleaned layer")
    sub.add_parser("build-gold", help="rebuild the gold layer from the quality-checked data")
    sub.add_parser("verify", help="check every raw file against its SHA-256 in manifest.csv")
    sub.add_parser("train", help="train the forecast model, score it on the test set, write reports/")
    sub.add_parser("predict", help="forecast tomorrow for each city and add it to gold.predictions")
    sub.add_parser("publish", help="export dashboard files to data/publish and upload them to Hugging Face")
    ask = sub.add_parser("ask", help="ask the Insight Agent a question (answers with citations or says I don't know)")
    ask.add_argument("question", nargs="+")
    ui = sub.add_parser("agent-ui", help="open a local web page to chat with the Insight Agent")
    ui.add_argument("--port", type=int, default=8000)
    ui.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to accept visitors from outside (hosting)")
    sub.add_parser("agent-models", help="list the chat models your Groq/Gemini keys can use, and the order they are tried")
    sub.add_parser("validate-local", help="compare our Lahore values with Punjab EPA reference stations -> reports/")
    sub.add_parser("summary", help="write reports/Summary.html (all numbers from reports/), then print it to PDF")
    sub.add_parser("eval-agent", help="run the Insight Agent on the golden question set, write reports/agent_eval.csv")
    space = sub.add_parser("deploy-space", help="create or update the Hugging Face Space (the dashboard)")
    space.add_argument("--name", default="smog-forecast-pakistan", help="Space name under your HF account")
    export = sub.add_parser("export-runs", help="write Prefect run history to prefect_runs.csv")
    export.add_argument("--start", default="2026-10-20T16:00:00+05:00", help="ISO time, e.g. 2026-10-20T16:00:00+05:00")
    export.add_argument("--end", default="2026-10-21T16:00:00+05:00")
    args = parser.parse_args(argv)

    if args.command == "data":
        return run_data()
    if args.command == "fetch-openaq":
        return run_fetch_openaq(args.city, args.max_locations, args.retry_gaps)
    if args.command == "fetch-firms":
        config.ensure_folders()
        print(fetch_firms.run())
        return 0
    if args.command == "fetch-openmeteo":
        config.ensure_folders()
        print(fetch_openmeteo.run())
        return 0
    if args.command == "build-cleaned":
        config.ensure_folders()
        print(clean.run())
        return 0
    if args.command == "profile":
        from smog import profile
        profile.run()
        return 0
    if args.command == "quality":
        try:
            print(quality.run())
        except quality.QualityGateFailed as failed:
            print(failed)
            return 1
        return 0
    if args.command == "build-gold":
        print(gold.run())
        return 0
    if args.command == "train":
        from smog import model
        print(model.train())
        return 0
    if args.command == "predict":
        try:
            from smog import model
        except ImportError as missing:
            # Never turn an hourly run red because a package was not installed yet.
            print(f"SKIPPED: {missing.name} is not installed here (run `pip install -r requirements.txt`)")
            return 0
        print(model.predict())
        return 0
    if args.command == "publish":
        try:
            from smog import publish
        except ImportError as missing:
            print(f"SKIPPED: {missing.name} is not installed here (run `pip install -r requirements.txt`)")
            return 0
        print(publish.run())
        return 0
    if args.command == "ask":
        from smog import agent
        print(agent.format_answer(agent.InsightAgent().ask(" ".join(args.question))))
        return 0
    if args.command == "agent-models":
        from smog import agent
        for name, models in agent.list_models().items():
            print(f"{name}: " + ", ".join(models))
        print("chain tried in this order: " + " -> ".join(f"{p['name']}:{m}" for p in agent.providers() for m in p["models"]))
        return 0
    if args.command == "validate-local":
        from smog import local_validation
        print(local_validation.run())
        return 0
    if args.command == "summary":
        from smog import summary
        print(summary.build())
        return 0
    if args.command == "agent-ui":
        from smog import agent_ui
        agent_ui.serve(args.port, args.host)
        return 0
    if args.command == "eval-agent":
        from smog import agent
        df = agent.evaluate(agent.InsightAgent())
        for r in df.itertuples():
            print(f"{'PASS' if r.passed else 'FAIL'}  {r.id:<20} {r.failed_checks}")
        print(f"{int(df['passed'].sum())}/{len(df)} passed -> reports/agent_eval.csv")
        import json
        print(json.dumps(json.loads((agent.config.ROOT / "reports/agent_eval_summary.json").read_text()), indent=2))
        return 0
    if args.command == "deploy-space":
        from smog import space_deploy
        print(space_deploy.run(args.name))
        return 0
    if args.command == "verify":
        try:
            print(stage_verify_raw())
        except RuntimeError as problem:
            print(f"FAILED: {problem}")
            return 1
        return 0
    if args.command == "export-runs":
        from datetime import datetime
        from smog import export_runs
        print(export_runs.export(datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)))
        return 0
    return run_clean()


if __name__ == "__main__":
    sys.exit(main())
