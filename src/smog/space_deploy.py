"""Create or update the Hugging Face Space that shows the dashboard.

Run once from the main laptop (HF_TOKEN in .env):  python -m smog deploy-space
It uploads the files in space/ and tells the Space which dataset to read
(the one `python -m smog publish` updates every hour on the Dell).
"""
from __future__ import annotations

import os

from smog import config
from smog.publish import DEFAULT_REPO_NAME


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
    api.create_repo(space_id, repo_type="space", space_sdk="gradio", exist_ok=True, private=False)
    api.add_space_variable(space_id, "DATA_REPO", data_repo)
    groq = os.getenv("GROQ_API_KEY")
    if groq:                                   # stored as a Space *secret*: never visible in the code or logs
        api.add_space_secret(space_id, "GROQ_API_KEY", groq)
    api.upload_folder(folder_path=str(config.ROOT / "space"), repo_id=space_id, repo_type="space",
                      commit_message="update dashboard")
    return (f"Space ready: https://huggingface.co/spaces/{space_id} (reads datasets/{data_repo}"
            f"{'; Groq key stored as a Space secret' if groq else ''}). It takes 1-3 minutes to build.")
