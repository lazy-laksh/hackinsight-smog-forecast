"""manifest.csv: the record of where every raw file came from.

For each raw file we store: the source, the exact query used, when it was
fetched (UTC), its SHA-256 fingerprint, its size and its licence.

The SHA-256 is like a fingerprint of the file. If even one character in the
file changes, the fingerprint changes. `verify()` recomputes the fingerprint
of every raw file and compares it with the manifest, so nobody (including us)
can quietly edit raw data.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from smog import config

COLUMNS = [
    "file_path",       # relative to the repo root, always with forward slashes
    "source",          # id from config/sources.yaml, e.g. "openaq"
    "query",           # the exact URL or parameters used to fetch the file
    "fetched_at_utc",  # ISO time in UTC
    "sha256",
    "bytes",
    "licence",
    "publisher",
]


def sha256_of(path: Path) -> str:
    """Fingerprint of a file. Reads it in 1 MB pieces so big files are fine."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    """Store paths relative to the repo so the manifest works on any computer."""
    return path.resolve().relative_to(config.ROOT).as_posix()


def load(manifest_path: Path | None = None) -> list[dict]:
    """All rows of the manifest (an empty list if it does not exist yet)."""
    manifest_path = manifest_path or config.MANIFEST_PATH
    if not manifest_path.exists():
        return []
    with manifest_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["file_path"])
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def record(
    path: Path,
    source: str,
    query: str,
    licence: str | None = None,
    fetched_at: datetime | None = None,
    manifest_path: Path | None = None,
) -> dict:
    """Add (or update) the manifest row for one raw file and return the row.

    Call this right after saving a raw file to disk. If the licence is not
    given, the default licence for that source from sources.yaml is used.
    """
    manifest_path = manifest_path or config.MANIFEST_PATH
    info = config.source_info(source)
    fetched_at = fetched_at or datetime.now(timezone.utc)
    row = {
        "file_path": _relative(path),
        "source": source,
        "query": query,
        "fetched_at_utc": fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256_of(path),
        "bytes": str(path.stat().st_size),
        "licence": licence or info["licence"],
        "publisher": info["publisher"],
    }
    rows = [r for r in load(manifest_path) if r["file_path"] != row["file_path"]]
    rows.append(row)
    _save(rows, manifest_path)
    return row


@dataclass
class VerifyResult:
    checked: int = 0
    missing: list[str] = field(default_factory=list)     # in manifest, not on disk
    changed: list[str] = field(default_factory=list)     # fingerprint does not match
    unlisted: list[str] = field(default_factory=list)    # on disk, not in manifest

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.unlisted)


def verify(manifest_path: Path | None = None, raw_dir: Path | None = None) -> VerifyResult:
    """Check every raw file against its manifest fingerprint.

    Reports three kinds of problem:
    - missing:  listed in the manifest but the file is not on disk
    - changed:  the file is on disk but its fingerprint is different
    - unlisted: a raw file exists that the manifest does not know about
    """
    manifest_path = manifest_path or config.MANIFEST_PATH
    raw_dir = raw_dir or config.RAW_DIR
    result = VerifyResult()
    listed = set()

    for row in load(manifest_path):
        listed.add(row["file_path"])
        path = config.ROOT / row["file_path"]
        result.checked += 1
        if not path.exists():
            result.missing.append(row["file_path"])
        elif sha256_of(path) != row["sha256"]:
            result.changed.append(row["file_path"])

    if raw_dir.exists():
        for path in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
            if _relative(path) not in listed:
                result.unlisted.append(_relative(path))

    return result
