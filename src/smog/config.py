"""Where things live, and how settings and secret keys are loaded.

Every other module imports paths and settings from here, so if a folder
moves we only change it in one place.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

# --- Folder layout -----------------------------------------------------------
# ROOT is the repo folder (two levels up from this file: src/smog/config.py).
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # files exactly as downloaded. Never edited.
CLEANED_DIR = DATA_DIR / "cleaned"  # types fixed, units unified, problems flagged
GOLD_DIR = DATA_DIR / "gold"        # final tables used by the model and dashboard
MANIFEST_PATH = DATA_DIR / "manifest.csv"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"


def ensure_folders() -> None:
    """Create the data folders if they do not exist yet."""
    for folder in (RAW_DIR, CLEANED_DIR, GOLD_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# --- Settings from YAML ------------------------------------------------------
@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    """Read config/<name>.yaml and return it as a dictionary."""
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def cities() -> dict:
    """The cities we forecast for, keyed by a short id such as 'lahore'."""
    return load_yaml("cities")["cities"]


def source_info(source_id: str) -> dict:
    """Publisher, URL and licence for one data source (see config/sources.yaml)."""
    return load_yaml("sources")["sources"][source_id]


# --- Secret keys -------------------------------------------------------------
class MissingKeyError(RuntimeError):
    """Raised when a key that a step needs is not set in .env."""


def get_key(name: str) -> str:
    """Return a secret key from the .env file (or the environment).

    Keys are only read when a step actually needs them, so steps that do not
    need a key still run on a computer that has no .env file.
    """
    load_dotenv(ROOT / ".env")
    value = os.getenv(name, "").strip()
    if not value or value.startswith("put-your-"):
        raise MissingKeyError(
            f"{name} is not set. Copy .env.example to .env and paste your real key."
        )
    return value
