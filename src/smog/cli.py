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

from smog import config, manifest

# --- Stages ------------------------------------------------------------------
# Each stage is a function that returns a one-line summary, or raises an error.
# A stage set to None has not been built yet. We will fill these in one by one.


def stage_prepare_folders() -> str:
    config.ensure_folders()
    return "data/raw, data/cleaned and data/gold are ready"


def stage_verify_raw() -> str:
    result = manifest.verify()
    if not result.ok:
        lines = []
        if result.missing:
            lines.append(f"{len(result.missing)} file(s) in the manifest are missing on disk")
        if result.changed:
            lines.append(f"{len(result.changed)} file(s) do not match their SHA-256: {result.changed[:3]}")
        if result.unlisted:
            lines.append(f"{len(result.unlisted)} raw file(s) are not in the manifest: {result.unlisted[:3]}")
        raise RuntimeError("; ".join(lines))
    return f"{result.checked} raw file(s) match their SHA-256 in manifest.csv"


STAGES: list[tuple[str, Optional[Callable[[], str]]]] = [
    ("Prepare folders", stage_prepare_folders),
    ("Fetch OpenAQ sensors (raw)", None),
    ("Fetch NASA FIRMS fires (raw)", None),
    ("Fetch Open-Meteo weather (raw)", None),
    ("Fetch Open-Meteo CAMS air quality (raw)", None),
    ("Verify raw files against manifest", stage_verify_raw),
    ("Build cleaned layer", None),
    ("Build gold layer", None),
    ("Run Data Quality Agent", None),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smog", description="Smog forecasting pipeline")
    parser.add_argument("command", choices=["data", "clean"])
    args = parser.parse_args(argv)
    if args.command == "data":
        return run_data()
    return run_clean()


if __name__ == "__main__":
    sys.exit(main())
