"""Keep the pipeline's memory in a private Hugging Face dataset, so the hourly run can happen on GitHub Actions.

Why this file exists
--------------------
On a laptop, everything the hourly run needs simply stays on the disk between runs. A GitHub Actions
machine starts empty every hour and is thrown away afterwards. So before each run we load the
pipeline's memory from a private Hugging Face dataset ("state repo"), and after the run we save back
only what changed.

What is saved (the "state")
    data/raw/                 every downloaded file, exactly as downloaded (the manifest proves them)
    data/manifest.csv         SHA-256 fingerprint of every raw file
    data/models/              the trained model (model.joblib); it is NOT retrained every hour
    data/logs/                quality log, drift log, pipeline status, known gaps
    data/state/predictions.parquet   every forecast ever issued (gold.predictions); the report card,
                                     the "were we right?" chart and the drift check need this history
    data/state/versions.txt   library versions the model was trained with (installed before each run)

What is NOT saved: data/cleaned, data/gold and warehouse.duckdb. Every hourly run rebuilds them from
data/raw anyway; only the forecast history lives in the warehouse, and that is exported above.

Commands
    python scripts/cloud_state.py push --all   once, on the computer that has the data (the Dell)
    python scripts/cloud_state.py pull         at the start of every GitHub Actions run
    python scripts/cloud_state.py push         at the end of every GitHub Actions run (only changes)

Needs HF_TOKEN (a WRITE token) in .env or the environment. The state repo is
HF_STATE_REPO if set, otherwise <your HF user name>/smog-forecast-state. It is created PRIVATE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STATE_DIRS = ["data/raw", "data/models", "data/logs", "data/state"]
STATE_FILES = ["data/manifest.csv"]
PREDICTIONS = "data/state/predictions.parquet"
VERSIONS = "data/state/versions.txt"
PULLED = ROOT / "data" / ".pulled.json"          # fingerprints right after `pull`; never uploaded
PINNED = ("scikit-learn", "numpy", "scipy", "joblib", "pandas")   # what model.joblib depends on
MAX_FILES_PER_COMMIT = 500


# --- small helpers ---------------------------------------------------------------------------------

def _token() -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    token = os.getenv("HF_TOKEN", "").strip()
    if not token or token.startswith("put-your-"):
        sys.exit("HF_TOKEN is not set (it needs WRITE access). Put it in .env or in the GitHub secrets.")
    return token


def _repo(api, token: str) -> str:
    return os.getenv("HF_STATE_REPO", "").strip() or f"{api.whoami(token=token)['name']}/smog-forecast-state"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def state_files(root: Path = ROOT) -> list[str]:
    """Every file that belongs to the state, as repo-relative POSIX paths."""
    out = [f for f in STATE_FILES if (root / f).is_file()]
    for d in STATE_DIRS:
        base = root / d
        if base.is_dir():
            out += [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()]
    return sorted(out)


def fingerprints(root: Path = ROOT) -> dict[str, str]:
    return {f: _sha(root / f) for f in state_files(root)}


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """New files and files whose content changed. Files are never deleted from the state."""
    return sorted(f for f, sha in after.items() if before.get(f) != sha)


# --- forecast history (gold.predictions) <-> parquet -------------------------------------------------

def export_predictions() -> str:
    from smog import config, warehouse
    if not config.WAREHOUSE_PATH.exists():
        return "no warehouse yet: forecast history not exported"
    with warehouse.connect(read_only=True) as con:
        has = con.execute("SELECT count(*) FROM information_schema.tables "
                          "WHERE table_schema = 'gold' AND table_name = 'predictions'").fetchone()[0]
        if not has:
            return "no gold.predictions table: forecast history not exported"
        (ROOT / PREDICTIONS).parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY (SELECT * FROM gold.predictions ORDER BY issued_utc, city_id) "
                    f"TO '{(ROOT / PREDICTIONS).as_posix()}' (FORMAT parquet)")
        n = con.execute("SELECT count(*) FROM gold.predictions").fetchone()[0]
    return f"exported {n} forecast rows"


def import_predictions() -> str:
    from smog import warehouse
    path = ROOT / PREDICTIONS
    if not path.exists():
        return "no saved forecast history yet"
    with warehouse.connect() as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")
        con.execute("CREATE OR REPLACE TABLE gold.predictions AS SELECT * FROM read_parquet(?)", [path.as_posix()])
        n = con.execute("SELECT count(*) FROM gold.predictions").fetchone()[0]
    return f"restored {n} forecast rows into gold.predictions"


def write_versions() -> None:
    lines = [f"# trained with Python {platform.python_version()}; installed before every cloud run"]
    for name in PINNED:
        try:
            lines.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            pass
    (ROOT / VERSIONS).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / VERSIONS).write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the three commands --------------------------------------------------------------------------------

def push_all(api, token: str, repo: str) -> None:
    """First upload, from the computer that already has the data. Stop the Prefect schedule first."""
    if not (ROOT / "data/models/model.joblib").exists():
        sys.exit("data/models/model.joblib is missing: run this on the computer that ran the pipeline.")
    print(export_predictions())
    write_versions()
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True, token=token)
    files = state_files()
    print(f"uploading {len(files)} files to the PRIVATE dataset {repo} (this can take a while the first time)")
    api.upload_folder(folder_path=str(ROOT), repo_id=repo, repo_type="dataset", token=token,
                      allow_patterns=[f"{d}/**" for d in STATE_DIRS] + STATE_FILES,
                      commit_message="Initial pipeline state")
    print("done. GitHub Actions can now run the hourly pipeline.")


def pull(api, token: str, repo: str) -> None:
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, repo_type="dataset", local_dir=str(ROOT), token=token,
                      allow_patterns=[f"{d}/**" for d in STATE_DIRS] + STATE_FILES)
    if not (ROOT / "data/models/model.joblib").exists():
        sys.exit(f"the state repo {repo} has no data/models/model.joblib: run `push --all` on the Dell first")
    print(import_predictions())
    trained = (ROOT / VERSIONS).read_text(encoding="utf-8").splitlines()[0] if (ROOT / VERSIONS).exists() else ""
    if trained and platform.python_version().rsplit(".", 1)[0] not in trained:
        print(f"WARNING: model {trained.lstrip('# ')}, but this machine runs Python {platform.python_version()}")
    PULLED.write_text(json.dumps(fingerprints()), encoding="utf-8")
    print(f"loaded {len(state_files())} state files from {repo}")


def push(api, token: str, repo: str) -> None:
    from huggingface_hub import CommitOperationAdd
    if not PULLED.exists():
        sys.exit("refusing to push: `pull` did not finish in this run, so the state here may be incomplete")
    print(export_predictions())
    todo = changed_files(json.loads(PULLED.read_text(encoding="utf-8")), fingerprints())
    if not todo:
        print("nothing changed")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for i in range(0, len(todo), MAX_FILES_PER_COMMIT):
        part = todo[i:i + MAX_FILES_PER_COMMIT]
        api.create_commit(repo_id=repo, repo_type="dataset", token=token,
                          operations=[CommitOperationAdd(path_in_repo=f, path_or_fileobj=str(ROOT / f)) for f in part],
                          commit_message=f"Hourly run {stamp}: {len(part)} files")
    print(f"saved {len(todo)} changed files to {repo}")
    if os.getenv("SQUASH_HISTORY") == "1":
        # Each hour adds a commit; old versions of overwritten files would pile up. Keep only the latest.
        api.super_squash_history(repo_id=repo, repo_type="dataset", token=token,
                                 commit_message=f"Weekly squash {stamp}")
        print("squashed the state repo history (keeps storage small)")


def main(argv: list[str] | None = None) -> None:
    from huggingface_hub import HfApi
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["pull", "push"])
    parser.add_argument("--all", action="store_true", help="push: first upload of the whole state")
    args = parser.parse_args(argv)
    token = _token()
    api = HfApi()
    repo = _repo(api, token)
    if args.command == "pull":
        pull(api, token, repo)
    elif args.all:
        push_all(api, token, repo)
    else:
        push(api, token, repo)


if __name__ == "__main__":
    main()
