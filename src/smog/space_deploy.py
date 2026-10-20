"""Create or update the Hugging Face Space that shows the dashboard.

Run from the main laptop (HF_TOKEN in .env):  python -m smog deploy-space
It uploads space_static/ as a free *static* Space (plain HTML + JavaScript). The page reads the
public dataset that `python -m smog publish` updates every hour on the Dell, directly in the
visitor's browser, so the Space itself never needs a server, a secret or a restart.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from smog import config
from smog.publish import DEFAULT_REPO_NAME

PLACEHOLDER = "__DATA_REPO__"


def run(name: str) -> str:
    from dotenv import load_dotenv
    from huggingface_hub import HfApi
    load_dotenv(config.ROOT / ".env")
    token = os.getenv("HF_TOKEN")
    if not token:
        return "FAILED: put HF_TOKEN=... (a Write token) in .env first"
    api = HfApi(token=token)
    user = api.whoami()["name"]
    space_id = f"{user}/{name}"
    data_repo = os.getenv("HF_DATASET_REPO") or f"{user}/{DEFAULT_REPO_NAME}"
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        shutil.copytree(config.ROOT / "space_static", site)
        page = site / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace(PLACEHOLDER, data_repo), encoding="utf-8")
        api.create_repo(space_id, repo_type="space", space_sdk="static", exist_ok=True, private=False)
        api.upload_folder(folder_path=str(site), repo_id=space_id, repo_type="space", commit_message="update dashboard")
    return (f"Space ready: https://huggingface.co/spaces/{space_id} (a static page reading datasets/{data_repo}). "
            "Give it a minute, then refresh.")
